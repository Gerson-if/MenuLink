#!/usr/bin/env bash
#
# Atualização segura do Comanda: busca a versão mais nova do repositório,
# aplica migrations e reinicia a aplicação — com backup do banco e
# rollback automático se algo der errado no caminho.
#
# O que este script garante antes de mexer em qualquer coisa:
#   - só uma atualização roda por vez (trava com flock);
#   - não há mudanças locais não commitadas no servidor que poderiam ser
#     perdidas ou causar um merge inesperado;
#   - há espaço em disco suficiente para o backup e a atualização;
#   - o backup do banco foi feito com sucesso (por padrão, aborta a
#     atualização se o backup falhar — veja ALLOW_UPDATE_WITHOUT_BACKUP
#     abaixo para forçar mesmo assim).
#
# Se qualquer passo depois disso falhar (dependências, migrations, ou a
# aplicação não voltar a responder), o script reverte sozinho: código,
# banco de dados e serviço voltam ao estado de antes da atualização.
#
# Uso:
#   sudo bash deploy/update.sh
#
#   ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh
#       Prossegue mesmo se o backup do banco falhar (não recomendado —
#       um rollback sem backup não consegue restaurar os dados).
#
# Pré-requisito: a máquina já configurada por deploy/install.sh (existe
# deploy/.deploy.conf) e o git remoto já com acesso configurado (SSH ou
# um token de acesso na URL do remote), já que este script só dá
# `git fetch` / `git pull` — não lida com autenticação.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"
LOCK_FILE="${SCRIPT_DIR}/.update.lock"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"
log()  { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info() { echo -e "    $*"; }
warn() { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()  { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()  { err "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Rode este script como root (ex: sudo bash deploy/update.sh)."
[ -f "$CONF_FILE" ] || die "Não encontrei ${CONF_FILE}. Rode deploy/install.sh primeiro."

# Impede duas atualizações rodando ao mesmo tempo (ex: cron sobrepondo uma
# execução manual) — o que poderia deixar o serviço num estado inconsistente.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    die "Já existe uma atualização em andamento (trava em ${LOCK_FILE}). Aguarde ela terminar."
fi

# shellcheck disable=SC1090
source "$CONF_FILE"

APP_DIR="${APP_DIR:?}"
SERVICE_USER="${SERVICE_USER:?}"
SERVICE_NAME="${SERVICE_NAME:-comanda}"
APP_PORT="${APP_PORT:-8000}"
DB_ENGINE="${DB_ENGINE:-postgres_local}"
ALLOW_UPDATE_WITHOUT_BACKUP="${ALLOW_UPDATE_WITHOUT_BACKUP:-0}"

BACKUP_DIR="${APP_DIR}/deploy/backups"
KEEP_BACKUPS=5
STAMP="$(date +%Y%m%d%H%M%S)"

run_as_app_user() { sudo -u "${SERVICE_USER}" -H bash -lc "$*"; }

get_database_url() {
    grep -m1 '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2- || true
}

check_local_changes() {
    # Um servidor de produção não deve ter edições manuais não commitadas:
    # elas seriam perdidas (ou causariam um merge inesperado) na atualização.
    local dirty
    dirty="$(run_as_app_user "cd '${APP_DIR}' && git status --porcelain")"
    if [ -n "$dirty" ]; then
        err "Há mudanças locais não commitadas em ${APP_DIR}:"
        echo "$dirty" | sed 's/^/    /' >&2
        die "Resolva isso antes (commit, descarte ou stash) — nada foi alterado."
    fi
}

check_disk_space() {
    # Backup + atualização de dependências precisam de espaço livre; evita
    # deixar o banco de dados pela metade por falta de espaço em disco.
    local min_kb=524288  # 512 MB
    local free_kb
    free_kb="$(df -Pk "${APP_DIR}" | awk 'NR==2 {print $4}')"
    if [ -n "$free_kb" ] && [ "$free_kb" -lt "$min_kb" ]; then
        die "Pouco espaço em disco livre em ${APP_DIR} ($((free_kb / 1024)) MB). Libere espaço antes de atualizar."
    fi
}

backup_database() {
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    local db_url; db_url="$(get_database_url)"
    case "$db_url" in
        postgres*)
            log "Fazendo backup do PostgreSQL..."
            if run_as_app_user "pg_dump --clean --if-exists '${db_url}' > '${BACKUP_DIR}/db_${STAMP}.sql'" \
                && [ -s "${BACKUP_DIR}/db_${STAMP}.sql" ]; then
                chmod 600 "${BACKUP_DIR}/db_${STAMP}.sql"
                echo "${BACKUP_DIR}/db_${STAMP}.sql" > "${BACKUP_DIR}/.last_backup"
                info "Backup salvo em ${BACKUP_DIR}/db_${STAMP}.sql"
            else
                rm -f "${BACKUP_DIR}/db_${STAMP}.sql" "${BACKUP_DIR}/.last_backup"
                if [ "$ALLOW_UPDATE_WITHOUT_BACKUP" = "1" ]; then
                    warn "Falha ao gerar backup do banco — continuando mesmo assim porque ALLOW_UPDATE_WITHOUT_BACKUP=1 (sem rede de segurança para o banco em caso de rollback)."
                else
                    die "Falha ao gerar backup do banco — abortando antes de mexer em qualquer coisa (nada foi alterado). Para prosseguir mesmo assim (não recomendado): ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh"
                fi
            fi
            ;;
        sqlite*)
            warn "DATABASE_URL usa SQLite — suportado aqui só por compatibilidade com instalações antigas."
            warn "deploy/install.sh não oferece mais SQLite; considere migrar para PostgreSQL."
            log "Fazendo backup do SQLite..."
            local db_path="${db_url#sqlite:///}"
            db_path="/${db_path#/}"
            if [ -f "$db_path" ]; then
                cp "$db_path" "${BACKUP_DIR}/db_${STAMP}.sqlite3"
                chmod 600 "${BACKUP_DIR}/db_${STAMP}.sqlite3"
                echo "${BACKUP_DIR}/db_${STAMP}.sqlite3" > "${BACKUP_DIR}/.last_backup"
                info "Backup salvo em ${BACKUP_DIR}/db_${STAMP}.sqlite3"
            else
                rm -f "${BACKUP_DIR}/.last_backup"
                if [ "$ALLOW_UPDATE_WITHOUT_BACKUP" = "1" ]; then
                    warn "Arquivo SQLite não encontrado em ${db_path} — continuando mesmo assim (ALLOW_UPDATE_WITHOUT_BACKUP=1)."
                else
                    die "Arquivo SQLite não encontrado em ${db_path} — abortando (nada foi alterado). Para prosseguir mesmo assim: ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh"
                fi
            fi
            ;;
        *)
            rm -f "${BACKUP_DIR}/.last_backup"
            if [ "$ALLOW_UPDATE_WITHOUT_BACKUP" = "1" ]; then
                warn "Não reconheci o tipo de DATABASE_URL — pulando backup do banco (ALLOW_UPDATE_WITHOUT_BACKUP=1)."
            else
                die "Não reconheci o tipo de DATABASE_URL — abortando por segurança (nada foi alterado). Para prosseguir mesmo assim: ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh"
            fi
            ;;
    esac

    # mantém só os KEEP_BACKUPS mais recentes de cada tipo
    ls -1t "${BACKUP_DIR}"/db_*.sql 2>/dev/null      | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
    ls -1t "${BACKUP_DIR}"/db_*.sqlite3 2>/dev/null  | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
}

restore_database() {
    local last_backup_file="${BACKUP_DIR}/.last_backup"
    [ -f "$last_backup_file" ] || { warn "Nenhum backup registrado desta execução — banco não foi restaurado."; return; }
    local backup_path; backup_path="$(cat "$last_backup_file")"
    [ -f "$backup_path" ] || { warn "Arquivo de backup ${backup_path} não encontrado — banco não foi restaurado."; return; }

    local db_url; db_url="$(get_database_url)"
    case "$backup_path" in
        *.sql)
            log "Restaurando backup do PostgreSQL a partir de ${backup_path}..."
            run_as_app_user "psql '${db_url}' < '${backup_path}'" || warn "Falha ao restaurar o backup do PostgreSQL — verifique manualmente."
            ;;
        *.sqlite3)
            log "Restaurando backup do SQLite a partir de ${backup_path}..."
            local db_path="${db_url#sqlite:///}"
            db_path="/${db_path#/}"
            cp "$backup_path" "$db_path" || warn "Falha ao restaurar o backup do SQLite — verifique manualmente."
            ;;
    esac
}

health_check() {
    local tries=15
    for _ in $(seq 1 "$tries"); do
        if curl -fsS -m 3 "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

rollback() {
    local previous_commit="$1"
    err "Algo deu errado durante a atualização — revertendo para o estado anterior."
    run_as_app_user "cd '${APP_DIR}' && git reset --hard '${previous_commit}'"
    run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt"
    restore_database
    systemctl restart "$SERVICE_NAME"
    if health_check; then
        warn "Rollback concluído: a aplicação voltou a responder na versão anterior."
    else
        err "A aplicação não respondeu nem após o rollback. Verifique manualmente:"
        err "  journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
    fi
    exit 1
}

# --------------------------------------------------------------------------

cd "$APP_DIR"

check_disk_space

log "Verificando atualizações..."
run_as_app_user "cd '${APP_DIR}' && git fetch --quiet origin"

PREVIOUS_COMMIT="$(run_as_app_user "cd '${APP_DIR}' && git rev-parse HEAD")"
REMOTE_COMMIT="$(run_as_app_user "cd '${APP_DIR}' && git rev-parse '@{u}'" 2>/dev/null || true)"

if [ -z "$REMOTE_COMMIT" ]; then
    die "Não há um branch remoto (upstream) configurado para o branch atual. Configure com: git branch --set-upstream-to=origin/<branch>"
fi

if [ "$PREVIOUS_COMMIT" = "$REMOTE_COMMIT" ]; then
    log "Já está atualizado (${PREVIOUS_COMMIT:0:7}). Nada a fazer."
    exit 0
fi

info "Versão atual : ${PREVIOUS_COMMIT:0:7}"
info "Nova versão  : ${REMOTE_COMMIT:0:7}"

check_local_changes
backup_database

log "Baixando a nova versão (fast-forward)..."
if ! run_as_app_user "cd '${APP_DIR}' && git merge --ff-only '@{u}'"; then
    die "Não foi possível avançar por fast-forward (há mudanças locais divergentes no servidor). Resolva manualmente antes de tentar de novo — nada foi alterado."
fi

log "Atualizando dependências Python..."
if ! run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt"; then
    rollback "$PREVIOUS_COMMIT"
fi

log "Rodando migrations do banco de dados..."
if ! run_as_app_user "cd '${APP_DIR}' && venv/bin/flask db upgrade"; then
    rollback "$PREVIOUS_COMMIT"
fi

log "Reiniciando o serviço..."
systemctl restart "$SERVICE_NAME"

log "Verificando se a aplicação subiu corretamente..."
if ! health_check; then
    rollback "$PREVIOUS_COMMIT"
fi

log "Atualização concluída com sucesso (${PREVIOUS_COMMIT:0:7} → ${REMOTE_COMMIT:0:7})."
