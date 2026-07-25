#!/usr/bin/env bash
#
# Atualização segura do Comanda: busca a versão mais nova do repositório,
# aplica migrations e reinicia a aplicação — com backup do banco e
# rollback automático se algo der errado no caminho.
#
# O que este script garante antes de mexer em qualquer coisa:
#   - as ferramentas necessárias (git, curl, systemctl...) estão instaladas;
#   - a máquina foi de fato preparada pelo deploy/install.sh (usuário de
#     serviço, ambiente virtual Python e serviço systemd existem);
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
# banco de dados e serviço voltam ao estado de antes da atualização. Se
# algo inesperado quebrar o próprio script (bug, comando faltando no meio
# do caminho), o trap de erro abaixo garante uma mensagem clara em vez de
# um script morrendo em silêncio.
#
# No final, o script sempre imprime um resumo com o resultado — incluindo
# a linha "STATUS=..." (fácil de gravar em log ou grepar num cron):
#   STATUS=SEM_ATUALIZACOES   já estava na versão mais nova, nada mudou
#   STATUS=ATUALIZADO         atualizou e a aplicação subiu normalmente
#   STATUS=FALHOU_REVERTIDO   a atualização falhou, mas o rollback deu
#                             certo — a aplicação voltou a responder na
#                             versão anterior
#   STATUS=FALHOU_CRITICO     a atualização falhou E o rollback também
#                             não conseguiu deixar a aplicação respondendo
#                             — precisa de intervenção manual AGORA
#   STATUS=ERRO_VALIDACAO     abortou antes de tocar em qualquer coisa
#                             (pré-requisito faltando, config inválida
#                             etc.) — nada foi alterado
#
# Códigos de saída: 0 = sem atualizações ou atualizado com sucesso;
# 1 = erro de validação (nada alterado); 3 = falhou mas reverteu bem;
# 4 = falhou e o rollback também não garantiu a aplicação de pé.
#
# Uso:
#   sudo bash deploy/update.sh
#       Atualiza de verdade (padrão).
#
#   sudo bash deploy/update.sh --check
#       Só verifica se há uma versão nova no remoto, sem alterar nada —
#       útil num cron de monitoramento separado da atualização em si.
#
#   ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh
#       Prossegue mesmo se o backup do banco falhar (não recomendado —
#       um rollback sem backup não consegue restaurar os dados).
#
# Pré-requisito: a máquina já configurada por deploy/install.sh (existe
# deploy/.deploy.conf) e o git remoto já com acesso configurado (SSH ou
# um token de acesso na URL do remote), já que este script só dá
# `git fetch` / `git pull` — não lida com autenticação.

set -Eeuo pipefail  # -E (errtrace): sem isso, o "trap ... ERR" abaixo só dispara
                    # para erros no corpo principal do script — erros dentro de
                    # qualquer função (ou seja, quase o script inteiro) passariam
                    # batido, matando o script em silêncio, sem log e sem rollback.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"
LOCK_FILE="${SCRIPT_DIR}/.update.lock"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_CYAN="\033[36m"
log()   { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info()  { echo -e "    $*"; }
warn()  { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()   { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()   { err "$*"; finish "ERRO_VALIDACAO" 1; }
banner(){ echo -e "${C_CYAN}$*${C_RESET}"; }

START_TS="$SECONDS"
DRY_RUN=0
UPDATE_STARTED=0   # vira 1 assim que o script começa a mexer em código/banco — usado pelo trap de erro pra saber se ainda dá pra tentar rollback
PREVIOUS_COMMIT=""
REMOTE_COMMIT=""

case "${1:-}" in
    --check|--dry-run) DRY_RUN=1 ;;
    "") : ;;
    -h|--help)
        sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *) echo "Opção desconhecida: $1 (use --check ou nenhum argumento)." >&2; exit 1 ;;
esac

# Resumo final: sempre roda, mesmo em caminhos de erro, pra deixar claro
# o que aconteceu e (se aplicável) um STATUS= fácil de grepar num cron.
finish() {
    local status="$1" code="$2"
    local elapsed=$((SECONDS - START_TS))
    echo
    case "$status" in
        SEM_ATUALIZACOES) banner "RESUMO: já estava na versão mais nova — nada foi alterado." ;;
        VERIFICACAO_OK)   banner "RESUMO: há uma atualização disponível (nada foi aplicado — modo --check)." ;;
        ATUALIZADO)        banner "RESUMO: atualização concluída com sucesso." ;;
        FALHOU_REVERTIDO)  banner "RESUMO: a atualização falhou, mas o rollback funcionou — aplicação de volta à versão anterior." ;;
        FALHOU_CRITICO)    banner "RESUMO: a atualização falhou e o rollback NÃO garantiu a aplicação de pé — intervenção manual necessária." ;;
        ERRO_VALIDACAO)    banner "RESUMO: abortado antes de alterar qualquer coisa (erro de validação)." ;;
    esac
    [ -n "$PREVIOUS_COMMIT" ] && info "Versão anterior : ${PREVIOUS_COMMIT:0:7}"
    [ -n "$REMOTE_COMMIT" ] && [ "$REMOTE_COMMIT" != "$PREVIOUS_COMMIT" ] && info "Versão nova     : ${REMOTE_COMMIT:0:7}"
    info "Tempo total     : ${elapsed}s"
    echo "STATUS=${status}"
    exit "$code"
}

# Rede de segurança contra falhas inesperadas (bug no script, comando que
# some no meio do caminho etc.) — sem isso, `set -e` mata o script na
# hora sem contexto nenhum sobre o que quebrou nem sobre o estado em que
# a aplicação ficou.
on_error() {
    local exit_code=$? line=$1
    err "Falha inesperada na linha ${line} do script (código de saída ${exit_code})."
    if [ "$UPDATE_STARTED" = "1" ] && [ -n "$PREVIOUS_COMMIT" ]; then
        rollback "$PREVIOUS_COMMIT"
    else
        err "Isso aconteceu antes de qualquer alteração em código ou banco — nada foi tocado."
        finish "ERRO_VALIDACAO" 1
    fi
}
trap 'on_error $LINENO' ERR

require_cmd() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        die "Comando(s) necessário(s) não encontrado(s) no PATH: ${missing[*]}. Instale-os antes de rodar este script."
    fi
}

[ "$(id -u)" -eq 0 ] || die "Rode este script como root (ex: sudo bash deploy/update.sh)."
require_cmd git curl systemctl sudo flock df awk sed cut seq id date xargs find sort

[ -f "$CONF_FILE" ] || die "Não encontrei ${CONF_FILE}. Rode deploy/install.sh primeiro."

# Impede duas atualizações rodando ao mesmo tempo (ex: cron sobrepondo uma
# execução manual) — o que poderia deixar o serviço num estado inconsistente.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    die "Já existe uma atualização em andamento (trava em ${LOCK_FILE}). Aguarde ela terminar."
fi

# shellcheck disable=SC1090
source "$CONF_FILE"

APP_DIR="${APP_DIR:?"APP_DIR não definido em ${CONF_FILE} — o arquivo está corrompido? Rode deploy/install.sh de novo."}"
SERVICE_USER="${SERVICE_USER:?"SERVICE_USER não definido em ${CONF_FILE} — o arquivo está corrompido? Rode deploy/install.sh de novo."}"
SERVICE_NAME="${SERVICE_NAME:-comanda}"
APP_PORT="${APP_PORT:-8000}"
DB_ENGINE="${DB_ENGINE:-postgres_local}"
ALLOW_UPDATE_WITHOUT_BACKUP="${ALLOW_UPDATE_WITHOUT_BACKUP:-0}"

BACKUP_DIR="${APP_DIR}/deploy/backups"
KEEP_BACKUPS=5
STAMP="$(date +%Y%m%d%H%M%S)"

# ---- validações de ambiente (nada é alterado até aqui) --------------------

[ -d "$APP_DIR" ] || die "APP_DIR (${APP_DIR}) não existe ou não é um diretório. Verifique ${CONF_FILE}."
[ -d "${APP_DIR}/.git" ] || die "${APP_DIR} não é um repositório git (falta ${APP_DIR}/.git). A instalação está corrompida?"
id "$SERVICE_USER" >/dev/null 2>&1 || die "O usuário de serviço '${SERVICE_USER}' (definido em ${CONF_FILE}) não existe no sistema."
[ -x "${APP_DIR}/venv/bin/python" ] || die "Não encontrei ${APP_DIR}/venv/bin/python — o ambiente virtual Python existe? Rode deploy/install.sh."
[ -x "${APP_DIR}/venv/bin/pip" ] || die "Não encontrei ${APP_DIR}/venv/bin/pip — ambiente virtual incompleto ou corrompido."
[ -x "${APP_DIR}/venv/bin/flask" ] || die "Não encontrei ${APP_DIR}/venv/bin/flask — 'flask' não está instalado no ambiente virtual."
[ -f "${APP_DIR}/requirements.txt" ] || die "Não encontrei ${APP_DIR}/requirements.txt."
[ -f "${APP_DIR}/.env" ] || die "Não encontrei ${APP_DIR}/.env — sem ele não dá pra saber qual é o banco de dados a atualizar/backupar."
systemctl list-unit-files "${SERVICE_NAME}.service" --no-legend 2>/dev/null | grep -q . \
    || die "O serviço systemd '${SERVICE_NAME}' não existe. Verifique SERVICE_NAME em ${CONF_FILE} ou rode deploy/install.sh."

run_as_app_user() {
    # bash -c (não -lc): evita sequenciar .bash_profile/.profile do usuário
    # de sistema, que hoje pode ser um login existente de verdade. Um
    # perfil customizado que imprime algo (MOTD, banner) contaminaria a
    # saída de comandos como "git rev-parse", quebrando o commit lido.
    sudo -u "${SERVICE_USER}" -H bash -c "$*"
}

get_database_url() {
    # O .env usa o formato do SQLAlchemy (ex: postgresql+psycopg2://...),
    # mas pg_dump/psql (libpq) só reconhecem "postgresql://" ou
    # "postgres://" — sem o sufixo "+driver" a URI vira uma string
    # inválida para o libpq, que a trata como um nome de banco literal.
    grep -m1 '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2- \
        | sed -E 's#^(postgres(ql)?)\+[A-Za-z0-9_]+://#\1://#' || true
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
        die "Pouco espaço em disco livre em ${APP_DIR} ($((free_kb / 1024)) MB, mínimo recomendado 512 MB). Libere espaço antes de atualizar."
    fi
    info "Espaço em disco livre: $((free_kb / 1024)) MB — ok."
}

backup_database() {
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    local db_url; db_url="$(get_database_url)"
    if [ -z "$db_url" ]; then
        rm -f "${BACKUP_DIR}/.last_backup"
        if [ "$ALLOW_UPDATE_WITHOUT_BACKUP" = "1" ]; then
            warn "Não encontrei DATABASE_URL em ${APP_DIR}/.env — pulando backup do banco (ALLOW_UPDATE_WITHOUT_BACKUP=1)."
            return
        else
            die "Não encontrei DATABASE_URL em ${APP_DIR}/.env — abortando por segurança (nada foi alterado). Para prosseguir mesmo assim: ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh"
        fi
    fi
    case "$db_url" in
        postgres*)
            require_cmd pg_dump psql
            log "Fazendo backup do PostgreSQL..."
            if run_as_app_user "pg_dump --clean --if-exists '${db_url}' > '${BACKUP_DIR}/db_${STAMP}.sql'" \
                && [ -s "${BACKUP_DIR}/db_${STAMP}.sql" ]; then
                chmod 600 "${BACKUP_DIR}/db_${STAMP}.sql"
                echo "${BACKUP_DIR}/db_${STAMP}.sql" > "${BACKUP_DIR}/.last_backup"
                info "Backup salvo em ${BACKUP_DIR}/db_${STAMP}.sql ($(du -h "${BACKUP_DIR}/db_${STAMP}.sql" | cut -f1))."
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
                info "Backup salvo em ${BACKUP_DIR}/db_${STAMP}.sqlite3 ($(du -h "${BACKUP_DIR}/db_${STAMP}.sqlite3" | cut -f1))."
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

    # mantém só os KEEP_BACKUPS mais recentes de cada tipo. Usa `find` (não
    # `ls db_*.sql | ...`): com `set -o pipefail`, um glob sem nenhum arquivo
    # correspondente faz o `ls` falhar (fica literal, "No such file or
    # directory") e derruba o pipeline inteiro — na primeira atualização
    # (sem backups antigos pra limpar ainda) isso abortava o script inteiro
    # bem no meio da atualização. `find` simplesmente não lista nada quando
    # não há match, sem erro.
    find "${BACKUP_DIR}" -maxdepth 1 -name 'db_*.sql' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | cut -d' ' -f2- | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
    find "${BACKUP_DIR}" -maxdepth 1 -name 'db_*.sqlite3' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | cut -d' ' -f2- | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
}

restore_database() {
    local last_backup_file="${BACKUP_DIR}/.last_backup"
    if [ ! -f "$last_backup_file" ]; then
        warn "Nenhum backup registrado desta execução — banco NÃO foi restaurado (pode estar com migrations parciais aplicadas)."
        return 1
    fi
    local backup_path; backup_path="$(cat "$last_backup_file")"
    if [ ! -f "$backup_path" ]; then
        warn "Arquivo de backup ${backup_path} não encontrado — banco NÃO foi restaurado."
        return 1
    fi

    local db_url; db_url="$(get_database_url)"
    case "$backup_path" in
        *.sql)
            log "Restaurando backup do PostgreSQL a partir de ${backup_path}..."
            if run_as_app_user "psql '${db_url}' < '${backup_path}'"; then
                info "Banco de dados restaurado com sucesso."
            else
                warn "Falha ao restaurar o backup do PostgreSQL — verifique manualmente (o backup em si continua em ${backup_path})."
                return 1
            fi
            ;;
        *.sqlite3)
            log "Restaurando backup do SQLite a partir de ${backup_path}..."
            local db_path="${db_url#sqlite:///}"
            db_path="/${db_path#/}"
            if cp "$backup_path" "$db_path"; then
                info "Banco de dados restaurado com sucesso."
            else
                warn "Falha ao restaurar o backup do SQLite — verifique manualmente (o backup em si continua em ${backup_path})."
                return 1
            fi
            ;;
        *)
            warn "Formato de backup desconhecido (${backup_path}) — banco NÃO foi restaurado."
            return 1
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
    local ok=1
    err "Algo deu errado durante a atualização — revertendo para o estado anterior (${previous_commit:0:7})."

    # Cada passo do rollback é best-effort: uma falha aqui não pode
    # derrubar o próprio rollback no meio do caminho (senão o script
    # morre sem sequer tentar reiniciar o serviço ou avisar o estado
    # final) — por isso cada um é isolado com `if`/`||` em vez de deixar
    # o `set -e` global decidir.
    if run_as_app_user "cd '${APP_DIR}' && git reset --hard '${previous_commit}'"; then
        info "Código revertido para ${previous_commit:0:7}."
    else
        err "Não consegui reverter o código com 'git reset --hard'. Intervenção manual necessária."
        ok=0
    fi

    if run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt"; then
        info "Dependências Python revertidas."
    else
        warn "Falha ao reinstalar as dependências da versão anterior — o serviço pode não subir corretamente."
        ok=0
    fi

    restore_database || ok=0

    if systemctl restart "$SERVICE_NAME"; then
        info "Serviço '${SERVICE_NAME}' reiniciado."
    else
        err "Falha ao reiniciar o serviço '${SERVICE_NAME}' durante o rollback."
        ok=0
    fi

    if health_check; then
        warn "Rollback concluído: a aplicação voltou a responder na versão anterior."
        [ "$ok" = "1" ] && finish "FALHOU_REVERTIDO" 3
        finish "FALHOU_REVERTIDO" 3   # app respondeu mesmo com algum passo do rollback incompleto — ainda assim é melhor que nada
    else
        err "A aplicação NÃO respondeu nem após o rollback. Verifique manualmente agora:"
        err "  systemctl status ${SERVICE_NAME}"
        err "  journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
        finish "FALHOU_CRITICO" 4
    fi
}

# --------------------------------------------------------------------------

cd "$APP_DIR"

check_disk_space

log "Verificando atualizações..."
if ! run_as_app_user "cd '${APP_DIR}' && git fetch --quiet origin"; then
    die "Não foi possível buscar atualizações do repositório remoto (git fetch falhou). Verifique a conexão com a internet e o acesso ao repositório (SSH/token) e tente de novo — nada foi alterado."
fi

PREVIOUS_COMMIT="$(run_as_app_user "cd '${APP_DIR}' && git rev-parse HEAD" 2>/dev/null || true)"
[ -n "$PREVIOUS_COMMIT" ] || die "Não consegui identificar o commit atual em ${APP_DIR} (git rev-parse HEAD falhou). O repositório está corrompido?"

REMOTE_COMMIT="$(run_as_app_user "cd '${APP_DIR}' && git rev-parse '@{u}'" 2>/dev/null || true)"
if [ -z "$REMOTE_COMMIT" ]; then
    die "Não há um branch remoto (upstream) configurado para o branch atual. Configure com: git branch --set-upstream-to=origin/<branch>"
fi

if [ "$PREVIOUS_COMMIT" = "$REMOTE_COMMIT" ]; then
    log "Já está atualizado (${PREVIOUS_COMMIT:0:7}). Nada a fazer."
    finish "SEM_ATUALIZACOES" 0
fi

info "Versão atual : ${PREVIOUS_COMMIT:0:7}"
info "Nova versão  : ${REMOTE_COMMIT:0:7}"
COMMITS_ATRAS="$(run_as_app_user "cd '${APP_DIR}' && git rev-list --count HEAD..'@{u}'" 2>/dev/null || echo "?")"
info "Commits novos: ${COMMITS_ATRAS}"

if [ "$DRY_RUN" = "1" ]; then
    log "Modo --check: há uma atualização disponível, mas nada será alterado."
    finish "VERIFICACAO_OK" 0
fi

check_local_changes
backup_database

# A partir daqui o script começa a alterar código/dependências/banco — se
# algo falhar de agora em diante (inclusive um erro inesperado pego pelo
# trap), a rede de segurança do rollback entra em ação.
UPDATE_STARTED=1

log "Baixando a nova versão (fast-forward)..."
if ! run_as_app_user "cd '${APP_DIR}' && git merge --ff-only '@{u}'"; then
    err "Não foi possível avançar por fast-forward (há mudanças locais divergentes no servidor)."
    UPDATE_STARTED=0  # nada de código/dependência/banco foi tocado ainda — não há o que reverter
    die "Resolva manualmente antes de tentar de novo — nada foi alterado."
fi

log "Atualizando dependências Python..."
if ! run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt"; then
    err "Falha ao instalar as dependências Python da nova versão."
    rollback "$PREVIOUS_COMMIT"
fi

log "Rodando migrations do banco de dados..."
if ! run_as_app_user "cd '${APP_DIR}' && venv/bin/flask db upgrade"; then
    err "Falha ao rodar as migrations do banco de dados."
    rollback "$PREVIOUS_COMMIT"
fi

log "Reiniciando o serviço..."
if ! systemctl restart "$SERVICE_NAME"; then
    err "Falha ao reiniciar o serviço '${SERVICE_NAME}'."
    rollback "$PREVIOUS_COMMIT"
fi

log "Verificando se a aplicação subiu corretamente..."
if ! health_check; then
    err "A aplicação não respondeu em http://127.0.0.1:${APP_PORT}/healthz após reiniciar."
    rollback "$PREVIOUS_COMMIT"
fi

log "Atualização concluída com sucesso (${PREVIOUS_COMMIT:0:7} → ${REMOTE_COMMIT:0:7})."
finish "ATUALIZADO" 0
