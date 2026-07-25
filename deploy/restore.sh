#!/usr/bin/env bash
#
# Restaura um backup gerado por deploy/backup.sh — banco de dados,
# arquivos enviados pelos lojistas e (por padrão) o .env. Uso típico:
# migrar o Comanda para uma VPS nova sem perder dados.
#
# Passo a passo de uma migração completa:
#   1. Na VPS ANTIGA:  sudo bash deploy/backup.sh
#   2. Copie o arquivo gerado (deploy/backups/comanda_backup_*.tar.gz.gpg)
#      para fora do servidor (seu computador, outro storage etc.):
#        scp root@VPS_ANTIGA:/opt/comanda/deploy/backups/comanda_backup_*.gpg .
#   3. Na VPS NOVA: rode deploy/install.sh normalmente (clona o repo,
#      cria usuário de serviço, systemd, Nginx/SSL — mas com um banco
#      novo e vazio).
#   4. Envie o arquivo de backup para a VPS nova:
#        scp comanda_backup_*.gpg root@VPS_NOVA:/opt/comanda/deploy/backups/
#   5. Na VPS NOVA: sudo bash deploy/restore.sh deploy/backups/comanda_backup_*.gpg
#
# O que este script garante antes de mexer em qualquer coisa:
#   - o arquivo de backup existe e o checksum SHA256 bate (se o
#     .sha256 estiver ao lado do arquivo);
#   - a máquina já foi preparada por deploy/install.sh;
#   - você confirmou explicitamente (este comando SOBRESCREVE o banco de
#     dados e os uploads atuais desta máquina).
#
# Antes de sobrescrever, o script tira um snapshot de segurança do
# estado atual (banco + uploads) em deploy/backups/pre_restore_<data>/ —
# então mesmo uma restauração feita por engano tem como ser desfeita.
#
# Uso:
#   sudo bash deploy/restore.sh <arquivo_de_backup>
#       Restaura banco de dados, uploads E o .env (config completa).
#
#   sudo bash deploy/restore.sh --skip-env <arquivo_de_backup>
#       Restaura banco de dados e uploads, mas MANTÉM o .env atual desta
#       máquina — use isso se a VPS nova já tem domínio/SMTP/Asaas
#       configurados diferentes do servidor de origem.
#
#   sudo bash deploy/restore.sh --yes <arquivo_de_backup>
#       Pula a confirmação interativa (para automação — use com cuidado).
#
#   BACKUP_PASSPHRASE='minha-senha' sudo -E bash deploy/restore.sh arquivo.gpg
#       Senha de descriptografia sem prompt interativo.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_CYAN="\033[36m"
log()   { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info()  { echo -e "    $*"; }
warn()  { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()   { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()   {
    err "$*"
    # Depois que RESTORE_STARTED=1 (serviço já parado, .env/banco/uploads
    # já podem ter sido tocados), um "die" no meio do caminho NÃO é mais
    # "nada foi alterado" — reporta FALHOU (e o local do snapshot de
    # segurança) em vez do ERRO_VALIDACAO enganoso.
    if [ "$RESTORE_STARTED" = "1" ]; then
        finish "FALHOU" 4
    fi
    finish "ERRO_VALIDACAO" 1
}
banner(){ echo -e "${C_CYAN}$*${C_RESET}"; }

START_TS="$SECONDS"
TMP_DIR=""
RESTORE_STARTED=0

finish() {
    local status="$1" code="$2"
    local elapsed=$((SECONDS - START_TS))
    [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ] && rm -rf "$TMP_DIR"
    echo
    case "$status" in
        OK)                banner "RESUMO: restauração concluída com sucesso." ;;
        FALHOU)            banner "RESUMO: a restauração falhou. Veja o snapshot de segurança em: ${PRE_RESTORE_DIR:-<não chegou a ser criado>}" ;;
        ERRO_VALIDACAO)    banner "RESUMO: abortado antes de alterar qualquer coisa (erro de validação)." ;;
        CANCELADO)         banner "RESUMO: cancelado pelo usuário — nada foi alterado." ;;
    esac
    info "Tempo total: ${elapsed}s"
    echo "STATUS=${status}"
    exit "$code"
}

on_error() {
    local exit_code=$? line=$1
    err "Falha inesperada na linha ${line} (código ${exit_code})."
    if [ "$RESTORE_STARTED" = "1" ]; then
        err "A restauração já tinha começado a alterar dados quando isso aconteceu."
        err "O snapshot de segurança de ANTES da restauração está em: ${PRE_RESTORE_DIR:-<desconhecido>}"
        finish "FALHOU" 4
    fi
    finish "ERRO_VALIDACAO" 1
}
trap 'on_error $LINENO' ERR

require_cmd() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    [ "${#missing[@]}" -eq 0 ] || die "Comando(s) necessário(s) não encontrado(s): ${missing[*]}. Instale-os antes de rodar este script."
}

# ---- opções de linha de comando ------------------------------------------

SKIP_ENV=0
ASSUME_YES=0
BACKUP_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-env) SKIP_ENV=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        -h|--help) sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "Opção desconhecida: $1" >&2; exit 1 ;;
        *) BACKUP_FILE="$1"; shift ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "Rode este script como root (ex: sudo bash deploy/restore.sh <arquivo>)."
require_cmd tar gzip sha256sum date find systemctl curl sleep seq id sudo mktemp

[ -n "$BACKUP_FILE" ] || die "Uso: sudo bash deploy/restore.sh [--skip-env] [--yes] <arquivo_de_backup>"
[ -f "$BACKUP_FILE" ] || die "Arquivo não encontrado: ${BACKUP_FILE}"
BACKUP_FILE="$(cd "$(dirname "$BACKUP_FILE")" && pwd)/$(basename "$BACKUP_FILE")"

[ -f "$CONF_FILE" ] || die "Não encontrei ${CONF_FILE}. Rode deploy/install.sh primeiro nesta máquina (a restauração espera uma instalação já existente para preencher)."

# shellcheck disable=SC1090
source "$CONF_FILE"
APP_DIR="${APP_DIR:?"APP_DIR não definido em ${CONF_FILE}."}"
SERVICE_USER="${SERVICE_USER:?"SERVICE_USER não definido em ${CONF_FILE}."}"
SERVICE_NAME="${SERVICE_NAME:-comanda}"
APP_PORT="${APP_PORT:-8000}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

[ -d "$APP_DIR" ] || die "APP_DIR (${APP_DIR}) não existe."
[ -x "${APP_DIR}/venv/bin/flask" ] || die "Não encontrei ${APP_DIR}/venv/bin/flask — o ambiente virtual Python existe? Rode deploy/install.sh."
systemctl list-unit-files "${SERVICE_NAME}.service" --no-legend 2>/dev/null | grep -q . \
    || die "O serviço systemd '${SERVICE_NAME}' não existe. Rode deploy/install.sh primeiro."

BACKUP_DIR="${APP_DIR}/deploy/backups"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d%H%M%S)"
PRE_RESTORE_DIR="${BACKUP_DIR}/pre_restore_${STAMP}"

run_as_app_user() {
    sudo -u "${SERVICE_USER}" -H bash -c "$*"
}

get_database_url() {
    grep -m1 '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2- \
        | sed -E 's#^(postgres(ql)?)\+[A-Za-z0-9_]+://#\1://#' || true
}

health_check() {
    local tries=15
    for _ in $(seq 1 "$tries"); do
        curl -fsS -m 3 "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

# ---------------------------------------------------------------------------

echo
banner "Este comando vai SOBRESCREVER o banco de dados e os arquivos de"
banner "lojistas (uploads) atuais desta máquina com o conteúdo de:"
info "  ${BACKUP_FILE}"
if [ "$SKIP_ENV" = "1" ]; then
    info "O .env atual desta máquina será mantido (--skip-env)."
else
    info "O .env atual também será substituído pelo do backup."
fi
echo
if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "Digite 'restaurar' para confirmar: " CONFIRM
    [ "$CONFIRM" = "restaurar" ] || finish "CANCELADO" 0
fi

TMP_DIR="$(mktemp -d "${BACKUP_DIR}/.tmp_restore_XXXXXX")"
chmod 700 "$TMP_DIR"
# Dono precisa ser o SERVICE_USER (não root): a restauração do Postgres
# roda via run_as_app_user (sudo -u "$SERVICE_USER"), que precisa
# conseguir LER o db.sql extraído aqui dentro. Root continua acessando
# normalmente (root ignora permissões de dono).
chown "${SERVICE_USER}:${SERVICE_GROUP}" "$TMP_DIR"

# ---- 1. Descriptografa (se aplicável) e extrai ---------------------------

WORK_FILE="$BACKUP_FILE"
case "$BACKUP_FILE" in
    *.gpg)
        require_cmd gpg
        log "Descriptografando backup..."
        DECRYPTED="${TMP_DIR}/backup.tar.gz"
        # "|| true" nas duas chamadas: sem isso, um gpg com senha errada
        # (código de saída != 0) dispara o trap de erro genérico em vez
        # da mensagem específica logo abaixo — o `[ -s "$DECRYPTED" ]`
        # já cobre tanto "senha errada" quanto qualquer outra falha.
        if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
            gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" \
                --decrypt --output "$DECRYPTED" "$BACKUP_FILE" || true
        else
            info "Digite a senha usada para criptografar este backup:"
            gpg --decrypt --output "$DECRYPTED" "$BACKUP_FILE" || true
        fi
        [ -s "$DECRYPTED" ] || die "Falha ao descriptografar ${BACKUP_FILE} (senha errada?)."
        WORK_FILE="$DECRYPTED"
        ;;
esac

if [ -f "${BACKUP_FILE}.sha256" ]; then
    log "Conferindo integridade (SHA256)..."
    expected="$(cat "${BACKUP_FILE}.sha256")"
    actual="$(sha256sum "$BACKUP_FILE" | awk '{print $1}')"
    [ "$expected" = "$actual" ] || die "Checksum não confere para ${BACKUP_FILE} — o arquivo pode estar corrompido ou incompleto. Restauração abortada por segurança."
    info "Checksum OK."
else
    warn "Não encontrei ${BACKUP_FILE}.sha256 ao lado do backup — pulando conferência de integridade."
fi

log "Extraindo backup..."
EXTRACT_DIR="${TMP_DIR}/extracted"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$WORK_FILE" -C "$EXTRACT_DIR"
# mkdir/tar acima rodam como root — os arquivos extraídos nascem donos
# de root independente do dono de TMP_DIR. run_as_app_user (sudo -u
# "$SERVICE_USER") precisa conseguir LER db.sql daqui a pouco.
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$EXTRACT_DIR"
[ -f "${EXTRACT_DIR}/MANIFESTO.txt" ] && { info "Manifesto do backup:"; sed 's/^/    /' "${EXTRACT_DIR}/MANIFESTO.txt"; }

[ -f "${EXTRACT_DIR}/db.sql" ] || [ -f "${EXTRACT_DIR}/db.sqlite3" ] \
    || die "O backup não contém db.sql nem db.sqlite3 — arquivo inválido ou corrompido."

# ---- 2. Snapshot de segurança do estado ATUAL (antes de sobrescrever) ----

log "Salvando snapshot de segurança do estado atual em ${PRE_RESTORE_DIR}..."
mkdir -p "$PRE_RESTORE_DIR"
chmod 700 "$PRE_RESTORE_DIR"
chown "${SERVICE_USER}:${SERVICE_GROUP}" "$PRE_RESTORE_DIR"

current_db_url="$(get_database_url)"
case "$current_db_url" in
    postgres://*|postgresql://*)
        require_cmd pg_dump
        run_as_app_user "pg_dump --clean --if-exists '${current_db_url}' > '${PRE_RESTORE_DIR}/db.sql'" \
            || warn "Não consegui snapshotar o banco atual (pode ser um banco novo/vazio ainda) — seguindo mesmo assim."
        ;;
    sqlite:///*)
        cur_db_path="${current_db_url#sqlite:///}"; cur_db_path="/${cur_db_path#/}"
        [ -f "$cur_db_path" ] && cp "$cur_db_path" "${PRE_RESTORE_DIR}/db.sqlite3"
        ;;
esac
if [ -d "${APP_DIR}/app/static/uploads" ]; then
    tar -czf "${PRE_RESTORE_DIR}/uploads.tar.gz" -C "${APP_DIR}/app/static" uploads 2>/dev/null || true
fi
[ -f "${APP_DIR}/.env" ] && cp "${APP_DIR}/.env" "${PRE_RESTORE_DIR}/env.backup"
info "Snapshot salvo. Se algo der errado, os dados de antes da restauração estão preservados aí."

# A partir daqui o script começa a sobrescrever dados de verdade.
RESTORE_STARTED=1

# ---- 3. Para o serviço (evita escritas durante a restauração) -----------

log "Parando o serviço '${SERVICE_NAME}'..."
systemctl stop "$SERVICE_NAME" || warn "Falha ao parar o serviço — continuando mesmo assim."

# ---- 4. Restaura .env (a menos que --skip-env) ---------------------------

if [ "$SKIP_ENV" = "1" ]; then
    info "Mantendo o .env atual (--skip-env)."
else
    if [ -f "${EXTRACT_DIR}/env.backup" ]; then
        log "Restaurando .env..."
        cp "${EXTRACT_DIR}/env.backup" "${APP_DIR}/.env"
        chown "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}/.env"
        chmod 600 "${APP_DIR}/.env"
    else
        warn "O backup não contém env.backup — mantendo o .env atual desta máquina."
    fi
fi

# ---- 5. Restaura o banco de dados ----------------------------------------

db_url="$(get_database_url)"
[ -n "$db_url" ] || die "Não encontrei DATABASE_URL em ${APP_DIR}/.env após a restauração do .env."

if [ -f "${EXTRACT_DIR}/db.sql" ]; then
    case "$db_url" in
        postgres://*|postgresql://*)
            require_cmd psql
            log "Restaurando dump do PostgreSQL (isso substitui todo o conteúdo do banco atual)..."
            run_as_app_user "psql '${db_url}' < '${EXTRACT_DIR}/db.sql'" \
                || die "Falha ao restaurar o dump do PostgreSQL. O snapshot de segurança em ${PRE_RESTORE_DIR} preserva o estado anterior."
            ;;
        *)
            die "O backup tem um dump de PostgreSQL (db.sql), mas o DATABASE_URL atual não é PostgreSQL (${db_url%%:*}). Restauração abortada — bancos incompatíveis."
            ;;
    esac
elif [ -f "${EXTRACT_DIR}/db.sqlite3" ]; then
    case "$db_url" in
        sqlite:///*)
            log "Restaurando arquivo SQLite..."
            db_path="${db_url#sqlite:///}"; db_path="/${db_path#/}"
            cp "${EXTRACT_DIR}/db.sqlite3" "$db_path" \
                || die "Falha ao copiar o SQLite restaurado para ${db_path}."
            chown "${SERVICE_USER}:${SERVICE_GROUP}" "$db_path"
            ;;
        *)
            die "O backup tem um banco SQLite, mas o DATABASE_URL atual não é SQLite (${db_url%%:*}). Restauração abortada — bancos incompatíveis."
            ;;
    esac
fi
info "Banco de dados restaurado."

# ---- 6. Restaura uploads --------------------------------------------------

if [ -f "${EXTRACT_DIR}/uploads.tar.gz" ]; then
    log "Restaurando arquivos enviados pelos lojistas (uploads)..."
    rm -rf "${APP_DIR}/app/static/uploads"
    mkdir -p "${APP_DIR}/app/static"
    tar -xzf "${EXTRACT_DIR}/uploads.tar.gz" -C "${APP_DIR}/app/static"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}/app/static/uploads"
    info "Uploads restaurados."
else
    warn "O backup não contém uploads.tar.gz — mantendo os uploads atuais desta máquina."
fi

# ---- 7. Migrations (o código local pode ser mais novo que o backup) -----

log "Rodando migrations do banco de dados..."
run_as_app_user "cd '${APP_DIR}' && venv/bin/flask db upgrade" \
    || die "Falha ao rodar migrations após restaurar. O snapshot de segurança em ${PRE_RESTORE_DIR} preserva o estado anterior."

# ---- 8. Reinicia e confere -------------------------------------------------

log "Reiniciando o serviço '${SERVICE_NAME}'..."
systemctl start "$SERVICE_NAME" || die "Falha ao iniciar o serviço '${SERVICE_NAME}' após a restauração."

log "Verificando se a aplicação subiu corretamente..."
if ! health_check; then
    err "A aplicação não respondeu em http://127.0.0.1:${APP_PORT}/healthz após a restauração."
    err "Verifique manualmente: systemctl status ${SERVICE_NAME}  /  journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
    finish "FALHOU" 4
fi

echo
banner "Restauração concluída — a aplicação está respondendo normalmente."
info "Snapshot do estado anterior (antes desta restauração) preservado em:"
info "  ${PRE_RESTORE_DIR}"
info "Pode apagar esse snapshot manualmente quando tiver certeza de que está tudo certo."

finish "OK" 0
