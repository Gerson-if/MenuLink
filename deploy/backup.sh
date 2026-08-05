#!/usr/bin/env bash
#
# Backup completo do Menu Link para migração entre servidores: empacota o
# banco de dados, os arquivos enviados pelos lojistas (logos, fotos de
# produtos, banners, vídeo/imagem da landing) e o .env de configuração
# num único arquivo — o suficiente para restaurar tudo numa VPS nova sem
# perder dados de nenhum lojista.
#
# Por padrão, o arquivo final é criptografado (GPG, AES256 simétrico) —
# ele contém o .env, que tem SECRET_KEY, credenciais do banco, chaves da
# Asaas etc. Sem criptografia, qualquer um que copiasse o arquivo (de um
# backup na nuvem mal configurado, por exemplo) teria acesso a tudo isso.
#
# Uso:
#   sudo bash deploy/backup.sh
#       Gera deploy/backups/menulink_backup_<data>.tar.gz.gpg (pede uma
#       senha de criptografia duas vezes, interativamente).
#
#   BACKUP_PASSPHRASE='minha-senha' sudo -E bash deploy/backup.sh
#       Mesma coisa, sem precisar digitar a senha (útil em cron/scripts).
#       Guarde essa senha em local seguro — ela é necessária pra restaurar.
#
#   sudo bash deploy/backup.sh --output /caminho/outro/lugar
#       Salva o arquivo final noutro diretório (ex: um disco/pendrive
#       montado), além de deixá-lo também em deploy/backups/.
#
#   sudo bash deploy/backup.sh --no-encrypt
#       Gera o .tar.gz sem criptografia (não recomendado — só faça isso
#       se for criptografar por fora, ou copiar direto para um lugar já
#       seguro e apagar depois).
#
# Depois de gerado, copie o arquivo para FORA deste servidor (é o ponto
# principal de ter um backup!) — por exemplo, do seu computador:
#   scp root@SEU_SERVIDOR:/opt/menulink/deploy/backups/menulink_backup_*.tar.gz.gpg .
#
# Para restaurar na VPS nova, veja deploy/restore.sh.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_CYAN="\033[36m"
log()   { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info()  { echo -e "    $*"; }
warn()  { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()   { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()   { err "$*"; finish "ERRO" 1; }
banner(){ echo -e "${C_CYAN}$*${C_RESET}"; }

START_TS="$SECONDS"
TMP_DIR=""

finish() {
    local status="$1" code="$2"
    local elapsed=$((SECONDS - START_TS))
    [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ] && rm -rf "$TMP_DIR"
    echo
    case "$status" in
        OK)   banner "RESUMO: backup gerado com sucesso." ;;
        ERRO) banner "RESUMO: o backup falhou — veja a mensagem de erro acima. Nada foi restaurado/alterado no sistema em produção (isto só LÊ dados)." ;;
    esac
    info "Tempo total: ${elapsed}s"
    echo "STATUS=${status}"
    exit "$code"
}
trap 'err "Falha inesperada na linha $LINENO."; finish "ERRO" 1' ERR

require_cmd() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    [ "${#missing[@]}" -eq 0 ] || die "Comando(s) necessário(s) não encontrado(s): ${missing[*]}. Instale-os antes de rodar este script."
}

# ---- opções de linha de comando ------------------------------------------

OUTPUT_DIR=""
NO_ENCRYPT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT_DIR="${2:?"--output precisa de um caminho"}"; shift 2 ;;
        --no-encrypt) NO_ENCRYPT=1; shift ;;
        -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opção desconhecida: $1 (use --output <dir>, --no-encrypt ou nenhum argumento)." >&2; exit 1 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "Rode este script como root (ex: sudo bash deploy/backup.sh)."
require_cmd tar gzip sha256sum date du df find id sudo mktemp hostname
[ -f "$CONF_FILE" ] || die "Não encontrei ${CONF_FILE}. Rode deploy/install.sh primeiro (esta máquina precisa já ter o Menu Link instalado)."

# shellcheck disable=SC1090
source "$CONF_FILE"
APP_DIR="${APP_DIR:?"APP_DIR não definido em ${CONF_FILE}."}"
SERVICE_USER="${SERVICE_USER:?"SERVICE_USER não definido em ${CONF_FILE}."}"

[ -d "$APP_DIR" ] || die "APP_DIR (${APP_DIR}) não existe."
[ -f "${APP_DIR}/.env" ] || die "Não encontrei ${APP_DIR}/.env — sem ele não dá pra saber qual é o banco de dados."

BACKUP_DIR="${APP_DIR}/deploy/backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date +%Y%m%d%H%M%S)"
ARCHIVE_NAME="menulink_backup_${STAMP}"

get_database_url() {
    grep -m1 '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2- \
        | sed -E 's#^(postgres(ql)?)\+[A-Za-z0-9_]+://#\1://#' || true
}

run_as_app_user() {
    sudo -u "${SERVICE_USER}" -H bash -c "$*"
}

check_disk_space() {
    # Backup pode temporariamente precisar de ~2x o tamanho do banco +
    # uploads (dump/tar solto + arquivo final compactado) até a limpeza
    # no final — melhor abortar cedo do que deixar um backup pela metade.
    local uploads_kb db_kb min_kb free_kb
    uploads_kb="$(du -sk "${APP_DIR}/app/static/uploads" 2>/dev/null | cut -f1 || echo 0)"
    db_kb=262144  # estimativa conservadora de 256 MB se não der pra medir o banco direto
    min_kb=$(( (uploads_kb + db_kb) * 2 + 262144 ))
    free_kb="$(df -Pk "${APP_DIR}" | awk 'NR==2 {print $4}')"
    if [ -n "$free_kb" ] && [ "$free_kb" -lt "$min_kb" ]; then
        die "Pouco espaço em disco livre (~$((free_kb / 1024)) MB) para um backup seguro (recomendado ~$((min_kb / 1024)) MB). Libere espaço antes de continuar."
    fi
    info "Espaço em disco livre: $((free_kb / 1024)) MB — ok."
}

# ---------------------------------------------------------------------------

log "Preparando backup de migração do Menu Link..."
check_disk_space

TMP_DIR="$(mktemp -d "${BACKUP_DIR}/.tmp_backup_XXXXXX")"
chmod 700 "$TMP_DIR"
# Dono precisa ser o SERVICE_USER (não root): o dump do Postgres roda via
# run_as_app_user (sudo -u "$SERVICE_USER"), que precisa conseguir
# escrever dentro de TMP_DIR. Root continua conseguindo ler/escrever
# aqui normalmente (root ignora permissões de dono).
chown "${SERVICE_USER}:$(id -gn "$SERVICE_USER")" "$TMP_DIR"

# ---- 1. Banco de dados ----------------------------------------------------

db_url="$(get_database_url)"
[ -n "$db_url" ] || die "Não encontrei DATABASE_URL em ${APP_DIR}/.env."

case "$db_url" in
    postgres://*|postgresql://*)
        require_cmd pg_dump
        log "Fazendo dump do PostgreSQL..."
        if ! run_as_app_user "pg_dump --clean --if-exists '${db_url}' > '${TMP_DIR}/db.sql'" || [ ! -s "${TMP_DIR}/db.sql" ]; then
            die "Falha ao gerar o dump do PostgreSQL — nada foi salvo."
        fi
        info "Dump do banco: $(du -h "${TMP_DIR}/db.sql" | cut -f1)."
        ;;
    sqlite:///*)
        warn "DATABASE_URL usa SQLite — suportado só por compatibilidade com instalações antigas."
        db_path="${db_url#sqlite:///}"; db_path="/${db_path#/}"
        [ -f "$db_path" ] || die "Arquivo SQLite não encontrado em ${db_path}."
        cp "$db_path" "${TMP_DIR}/db.sqlite3"
        info "Cópia do banco SQLite: $(du -h "${TMP_DIR}/db.sqlite3" | cut -f1)."
        ;;
    *)
        die "Não reconheci o tipo de DATABASE_URL (${db_url%%:*}://...) — backup abortado por segurança."
        ;;
esac

# ---- 2. Arquivos enviados pelos lojistas (logos, fotos, banners...) ------

log "Empacotando arquivos enviados (logos, fotos, banners)..."
if [ -d "${APP_DIR}/app/static/uploads" ]; then
    tar -czf "${TMP_DIR}/uploads.tar.gz" -C "${APP_DIR}/app/static" uploads
    info "Uploads: $(du -h "${TMP_DIR}/uploads.tar.gz" | cut -f1)."
else
    warn "Não encontrei ${APP_DIR}/app/static/uploads — nenhum arquivo de lojista para empacotar (instalação nova?)."
fi

# ---- 3. .env (configuração + segredos) -----------------------------------

cp "${APP_DIR}/.env" "${TMP_DIR}/env.backup"
chmod 600 "${TMP_DIR}/env.backup"

# ---- 4. Manifesto (o que tem aqui dentro, pra conferência depois) -------

GIT_COMMIT="$(run_as_app_user "cd '${APP_DIR}' && git rev-parse HEAD" 2>/dev/null || echo "desconhecido")"
cat > "${TMP_DIR}/MANIFESTO.txt" <<EOF
Backup de migração do Menu Link
Gerado em : $(date -Iseconds)
Servidor  : $(hostname)
Commit    : ${GIT_COMMIT}
Banco     : ${db_url%%://*}

Conteúdo deste arquivo:
  - db.sql ou db.sqlite3   -> dump/cópia completa do banco de dados
  - uploads.tar.gz         -> logos, fotos de produtos, banners e mídia
                              da landing enviados pelos lojistas
  - env.backup             -> cópia do .env (config + segredos: SECRET_KEY,
                              credenciais do banco, chaves da Asaas etc.)

ATENÇÃO: env.backup contém segredos. Trate este arquivo (criptografado
ou não) com o mesmo cuidado que uma senha de banco — não deixe em local
público, e prefira sempre a versão criptografada (.gpg).

Para restaurar: veja deploy/restore.sh no repositório.
EOF

# ---- 5. Empacota tudo num único arquivo ----------------------------------

log "Compactando arquivo final..."
FINAL_TAR="${BACKUP_DIR}/${ARCHIVE_NAME}.tar.gz"
# Lista só o que realmente existe (db.sql OU db.sqlite3, nunca os dois;
# uploads.tar.gz pode faltar numa instalação sem nenhum upload ainda).
TAR_ITEMS=()
for item in db.sql db.sqlite3 uploads.tar.gz env.backup MANIFESTO.txt; do
    [ -e "${TMP_DIR}/${item}" ] && TAR_ITEMS+=("$item")
done
tar -czf "$FINAL_TAR" -C "$TMP_DIR" "${TAR_ITEMS[@]}"
chmod 600 "$FINAL_TAR"

# ---- 6. Criptografia (recomendado) ---------------------------------------

FINAL_FILE="$FINAL_TAR"
if [ "$NO_ENCRYPT" = "1" ]; then
    warn "Backup gerado SEM criptografia (--no-encrypt) — contém segredos do .env em texto puro. Proteja este arquivo com cuidado."
elif command -v gpg >/dev/null 2>&1; then
    log "Criptografando o backup (GPG, AES256)..."
    # "|| true" nas duas chamadas: sem isso, uma falha do gpg (ex: sem
    # espaço, entropia insuficiente) dispara o trap de erro genérico em
    # vez da mensagem específica do `if [ -s ... ]` logo abaixo.
    if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
        gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" \
            --symmetric --cipher-algo AES256 --output "${FINAL_TAR}.gpg" "$FINAL_TAR" || true
    else
        info "Digite uma senha para criptografar o backup (você vai precisar dela para restaurar depois — guarde-a num lugar seguro, ela NÃO fica salva em lugar nenhum)."
        gpg --symmetric --cipher-algo AES256 --output "${FINAL_TAR}.gpg" "$FINAL_TAR" || true
    fi
    if [ -s "${FINAL_TAR}.gpg" ]; then
        rm -f "$FINAL_TAR"
        FINAL_FILE="${FINAL_TAR}.gpg"
        chmod 600 "$FINAL_FILE"
        info "Backup criptografado com sucesso."
    else
        die "Falha ao criptografar o backup — o arquivo não criptografado (${FINAL_TAR}) foi mantido; rode de novo ou use --no-encrypt conscientemente."
    fi
else
    warn "gpg não está instalado — backup gerado SEM criptografia. Para criptografar, instale gpg (apt install gnupg) e rode de novo, ou proteja este arquivo manualmente."
fi

sha256sum "$FINAL_FILE" | awk '{print $1}' > "${FINAL_FILE}.sha256"
chmod 600 "${FINAL_FILE}.sha256"

# ---- 7. Cópia extra opcional (--output) ----------------------------------

if [ -n "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR"
    cp "$FINAL_FILE" "${FINAL_FILE}.sha256" "$OUTPUT_DIR/"
    info "Cópia também salva em ${OUTPUT_DIR}/$(basename "$FINAL_FILE")."
fi

echo
banner "Backup pronto: ${FINAL_FILE}"
info "Tamanho: $(du -h "$FINAL_FILE" | cut -f1)"
info "Checksum SHA256 salvo em: ${FINAL_FILE}.sha256"
echo
info "PRÓXIMO PASSO (o mais importante): copie este arquivo para FORA deste"
info "servidor — um backup que mora só na própria máquina que pode falhar"
info "não protege de nada. Exemplo, rodando no seu computador:"
info "  scp root@$(hostname -I 2>/dev/null | awk '{print $1}'):${FINAL_FILE} ."
echo
info "Para restaurar (nesta máquina ou numa VPS nova já com deploy/install.sh"
info "rodado): sudo bash deploy/restore.sh ${FINAL_FILE}"

finish "OK" 0
