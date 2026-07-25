#!/usr/bin/env bash
#
# Instalador guiado do Comanda para um servidor Ubuntu (VPS).
#
# O que este script faz:
#   - instala as dependências de sistema (Python, Nginx, PostgreSQL, ffmpeg
#     — usado para recomprimir vídeos enviados na landing page — etc.);
#   - cria um usuário de sistema dedicado para rodar a aplicação (sem shell,
#     sem privilégios de root);
#   - cria o ambiente virtual Python e instala as dependências do projeto;
#   - gera a chave secreta e o arquivo .env de produção (permissão 600);
#   - configura o banco de dados: PostgreSQL local (nome, usuário e senha
#     você escolhe, ou o script gera uma senha forte sozinho) ou uma
#     DATABASE_URL externa já existente. SQLite NÃO é aceito aqui — é uma
#     opção só para desenvolvimento local (veja o README), pois trava sob
#     concorrência e não tem controle de acesso por usuário/senha;
#   - roda as migrations e, opcionalmente, cria o super admin;
#   - configura o Gunicorn como serviço systemd, já com um sandboxing básico
#     (reinicia sozinho se cair);
#   - configura o Nginx como proxy reverso, com domínio (e "www." opcional)
#     ou apenas o IP;
#   - opcionalmente emite um certificado SSL gratuito (Let's Encrypt ou
#     ZeroSSL) cobrindo o domínio e o "www.", e configura a renovação
#     automática;
#   - opcionalmente ativa um firewall básico (ufw), liberando só SSH, HTTP
#     e HTTPS;
#   - ao final, mostra (uma única vez) as credenciais do banco de dados
#     geradas e salva uma cópia num arquivo só para o root — veja a seção
#     "Credenciais do banco de dados" impressa no final da instalação.
#
# Uso:
#   sudo bash deploy/install.sh
#
# O script é interativo (faz perguntas) e explica cada decisão antes de
# perguntar. Rode-o a partir de uma cópia do repositório já clonada no
# servidor (ex: /opt/comanda). Ele detecta automaticamente o diretório do
# projeto a partir da própria localização deste arquivo.
#
# É seguro rodar de novo (ex: para reconfigurar): o script avisa antes de
# sobrescrever o que já existe e nunca apaga dados do banco.

set -euo pipefail
umask 077

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_BLUE="\033[34m"

log()  { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info() { echo -e "    $*"; }
warn() { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()  { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()  { err "$*"; exit 1; }

ask() {
    # ask "pergunta" "valor_padrao" -> imprime a resposta em REPLY_VALUE
    local prompt="$1" default="${2:-}" answer
    if [ -n "$default" ]; then
        read -rp "$(echo -e "${C_BLUE}?${C_RESET} ${prompt} [${default}]: ")" answer || true
        REPLY_VALUE="${answer:-$default}"
    else
        read -rp "$(echo -e "${C_BLUE}?${C_RESET} ${prompt}: ")" answer || true
        REPLY_VALUE="${answer}"
    fi
}

ask_secret() {
    # ask_secret "pergunta" -> imprime a resposta (não ecoada) em REPLY_VALUE
    local prompt="$1" answer
    read -rsp "$(echo -e "${C_BLUE}?${C_RESET} ${prompt}: ")" answer || true
    echo
    REPLY_VALUE="${answer}"
}

valid_identifier() {
    # nomes de banco/usuário do Postgres: letras, números e underscore, começando por letra
    [[ "$1" =~ ^[a-zA-Z][a-zA-Z0-9_]{0,62}$ ]]
}

valid_domain() {
    [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]
}

confirm() {
    # confirm "pergunta" "S|n" -> retorna 0 (sim) ou 1 (não)
    local prompt="$1" default="${2:-S}" answer
    local hint="S/n"; [ "$default" = "n" ] && hint="s/N"
    read -rp "$(echo -e "${C_BLUE}?${C_RESET} ${prompt} [${hint}]: ")" answer || true
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[sSyY] ]]
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "Rode este script como root (ex: sudo bash deploy/install.sh)."
    fi
}

detect_public_ip() {
    curl -fsSL --max-time 5 https://ifconfig.me 2>/dev/null \
        || curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null \
        || hostname -I 2>/dev/null | awk '{print $1}' \
        || echo ""
}

render_template() {
    # render_template arquivo_origem arquivo_destino
    local src="$1" dst="$2"
    sed \
        -e "s#__APP_DIR__#${APP_DIR}#g" \
        -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
        -e "s#__SERVICE_GROUP__#${SERVICE_GROUP}#g" \
        -e "s#__PORT__#${APP_PORT}#g" \
        -e "s#__WORKERS__#${GUNICORN_WORKERS}#g" \
        -e "s#__SERVER_NAME__#${SERVER_NAME}#g" \
        -e "s#__CERT_NAME__#${CERT_NAME:-$DOMAIN}#g" \
        -e "s#__DB_SERVICE__#${DB_SYSTEMD_DEP:-}#g" \
        "$src" > "$dst"
}

run_as_app_user() {
    # bash -c (não -lc): evita sequenciar .bash_profile/.profile do usuário.
    # Como o usuário de sistema agora pode ser um login existente de verdade
    # (ex: reaproveitado do SSH), um perfil customizado que imprime algo
    # (MOTD, banner, etc.) contaminaria a saída de comandos como
    # "git rev-parse", quebrando a leitura do commit atual. -H já garante
    # $HOME correto para git/pip sem precisar de shell de login.
    sudo -u "${SERVICE_USER}" -H bash -c "$*"
}

# --------------------------------------------------------------------------
# 0. Checagens iniciais
# --------------------------------------------------------------------------

require_root

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ]; then
        warn "Este script foi feito e testado para Ubuntu. Detectado: ${PRETTY_NAME:-desconhecido}. Continuando mesmo assim."
    fi
else
    warn "Não foi possível identificar a distribuição do sistema. Continuando mesmo assim."
fi

echo
echo -e "${C_BOLD}Instalador guiado do Comanda${C_RESET}"
echo "Diretório do projeto: ${APP_DIR}"
echo

if [ -f "$CONF_FILE" ]; then
    warn "Já existe uma configuração de deploy anterior em ${CONF_FILE}."
    if ! confirm "Deseja reconfigurar (isso pode sobrescrever .env, Nginx e o serviço systemd)?" "n"; then
        die "Instalação cancelada. Use deploy/update.sh para apenas atualizar uma instalação existente."
    fi
fi

# --------------------------------------------------------------------------
# 1. Perguntas
# --------------------------------------------------------------------------

log "Domínio ou IP"
info "Se você tem um domínio (ex: cardapio.seusite.com.br) já apontando para"
info "este servidor (registro DNS tipo A), informe abaixo — isso habilita"
info "HTTPS gratuito. Deixe em branco para acessar só pelo IP do servidor"
info "(sem SSL — os certificados gratuitos exigem um domínio válido)."
ask "Domínio" ""
DOMAIN_INPUT="$REPLY_VALUE"

DOMAIN=""
WWW_ALIAS=""
if [ -n "$DOMAIN_INPUT" ]; then
    if ! valid_domain "$DOMAIN_INPUT"; then
        die "'${DOMAIN_INPUT}' não parece um domínio válido (ex: cardapio.seusite.com.br)."
    fi
    case "$DOMAIN_INPUT" in
        www.*) DOMAIN="${DOMAIN_INPUT#www.}" ;;
        *)     DOMAIN="$DOMAIN_INPUT" ;;
    esac
    echo
    if confirm "Fazer 'www.${DOMAIN}' apontar para o mesmo site, além de '${DOMAIN}'?" "S"; then
        WWW_ALIAS="www.${DOMAIN}"
        info "Certifique-se de que o DNS tenha um registro para 'www.${DOMAIN}' também"
        info "(um segundo registro A, ou um CNAME apontando para '${DOMAIN}')."
    fi
fi

PUBLIC_IP="$(detect_public_ip)"
if [ -n "$DOMAIN" ]; then
    SERVER_NAME="$DOMAIN"
    [ -n "$WWW_ALIAS" ] && SERVER_NAME="${DOMAIN} ${WWW_ALIAS}"
    CERT_NAME="$DOMAIN"
else
    if [ -z "$PUBLIC_IP" ]; then
        ask "Não consegui detectar o IP público automaticamente. Informe o IP do servidor" ""
        PUBLIC_IP="$REPLY_VALUE"
    fi
    SERVER_NAME="$PUBLIC_IP"
    CERT_NAME="$PUBLIC_IP"
    info "Usando o IP do servidor: ${SERVER_NAME}"
fi

WANT_SSL="n"
SSL_PROVIDER=""
SSL_EMAIL=""
if [ -n "$DOMAIN" ]; then
    SSL_NAMES="${DOMAIN}"
    [ -n "$WWW_ALIAS" ] && SSL_NAMES="${DOMAIN} e ${WWW_ALIAS}"
    echo
    log "SSL (HTTPS)"
    if confirm "Configurar um certificado SSL gratuito para ${SSL_NAMES} agora?" "S"; then
        WANT_SSL="s"
        info "Antes de continuar, confirme que o DNS de ${SSL_NAMES} já aponta para"
        info "o IP deste servidor (${PUBLIC_IP:-desconhecido}) — a emissão do"
        info "certificado falha se algum desses nomes ainda não resolver para cá."
        echo
        echo "  1) Let's Encrypt (via certbot) — recomendado, mais testado"
        echo "  2) ZeroSSL (via acme.sh)"
        ask "Escolha a autoridade certificadora [1/2]" "1"
        case "$REPLY_VALUE" in
            2) SSL_PROVIDER="zerossl" ;;
            *) SSL_PROVIDER="letsencrypt" ;;
        esac
        ask "E-mail para avisos de expiração/renovação do certificado" "admin@${DOMAIN}"
        SSL_EMAIL="$REPLY_VALUE"
    fi
else
    warn "Sem domínio configurado: a aplicação vai rodar em HTTP simples (http://${SERVER_NAME}/)."
    warn "Você pode rodar este script novamente mais tarde, depois de apontar um domínio para este servidor, para habilitar HTTPS."
fi

echo
log "Porta interna da aplicação"
info "O Gunicorn escuta só localmente nessa porta; o Nginx é quem fica exposto na 80/443."
ask "Porta interna (mude só se já tiver algo usando a 8000 neste servidor)" "8000"
APP_PORT="$REPLY_VALUE"

echo
log "Banco de dados"
info "Este instalador só aceita PostgreSQL em produção. SQLite (usado no"
info "desenvolvimento local) não é uma opção aqui de propósito: ele trava sob"
info "concorrência, não tem usuário/senha próprios e não segura bem múltiplos"
info "lojistas gravando ao mesmo tempo."
echo "  1) PostgreSQL local (recomendado — o script instala, cria o banco e o usuário)"
echo "  2) Já tenho uma URL de banco PostgreSQL gerenciado (RDS, Supabase, Neon, etc.)"
ask "Escolha [1/2]" "1"
DB_CHOICE="$REPLY_VALUE"

DATABASE_URL=""
DB_SYSTEMD_DEP=""
DB_NAME=""
DB_USER=""
DB_PASSWORD=""
case "$DB_CHOICE" in
    2)
        DB_ENGINE="external"
        ask "Cole a DATABASE_URL (ex: postgresql+psycopg2://usuario:senha@host:5432/banco)" ""
        DATABASE_URL="$REPLY_VALUE"
        [ -n "$DATABASE_URL" ] || die "DATABASE_URL não pode ficar em branco."
        case "$DATABASE_URL" in
            sqlite*) die "DATABASE_URL do tipo sqlite:// não é aceita em produção. Use PostgreSQL." ;;
            postgres*) : ;;
            *) warn "A URL não começa com 'postgresql://' — confira se o driver está correto." ;;
        esac
        GUNICORN_WORKERS=3
        ;;
    *)
        DB_ENGINE="postgres_local"
        DB_SYSTEMD_DEP="postgresql.service"
        GUNICORN_WORKERS=3

        echo
        info "Nome e usuário do banco: só letras, números e underscore, começando"
        info "por letra. Pode manter os padrões se não tiver preferência."
        ask "Nome do banco de dados a criar" "comanda"
        DB_NAME="$REPLY_VALUE"
        valid_identifier "$DB_NAME" || die "Nome de banco inválido: '${DB_NAME}'."

        ask "Nome do usuário do banco de dados a criar" "comanda"
        DB_USER="$REPLY_VALUE"
        valid_identifier "$DB_USER" || die "Nome de usuário inválido: '${DB_USER}'."

        echo
        info "A senha do banco fica só no arquivo .env (permissão 600) e não é"
        info "reaproveitada entre instalações — cada instalação gera a sua, para"
        info "que vazar a senha de um servidor não comprometa outro."
        if confirm "Gerar uma senha forte automaticamente (recomendado)?" "S"; then
            DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
        else
            while true; do
                ask_secret "Senha do banco de dados (mín. 12 caracteres)"
                DB_PASSWORD="$REPLY_VALUE"
                if [ "${#DB_PASSWORD}" -lt 12 ]; then
                    err "Senha muito curta (mínimo 12 caracteres). Tente de novo."
                    continue
                fi
                ask_secret "Confirme a senha"
                if [ "$REPLY_VALUE" != "$DB_PASSWORD" ]; then
                    err "As senhas não conferem. Tente de novo."
                    continue
                fi
                break
            done
        fi
        ;;
esac

echo
log "Usuário de sistema"
info "A aplicação roda com um usuário sem privilégios de root — pode ser um"
info "usuário já existente no servidor (ex.: o mesmo usado para dar deploy"
info "por SSH) ou um novo, se preferir um dedicado só para a aplicação."
DEFAULT_SERVICE_USER="${SUDO_USER:-}"
if [ -z "$DEFAULT_SERVICE_USER" ] || [ "$DEFAULT_SERVICE_USER" = "root" ]; then
    DEFAULT_SERVICE_USER="comanda"
fi
ask "Nome do usuário de sistema (criado automaticamente se não existir)" "$DEFAULT_SERVICE_USER"
SERVICE_USER="$REPLY_VALUE"
if id "${SERVICE_USER}" &>/dev/null; then
    info "Usuário '${SERVICE_USER}' já existe — será reaproveitado, nada será criado."
fi

echo
log "Firewall (ufw)"
FW_SSH_PORT="$(awk '$1=="Port"{print $2}' /etc/ssh/sshd_config 2>/dev/null | tail -n1)"
[ -n "$FW_SSH_PORT" ] || FW_SSH_PORT=22
info "Um firewall básico libera só SSH, HTTP e HTTPS e bloqueia o resto —"
info "reduz o risco de outro serviço acabar exposto numa porta que não devia."
info "Porta de SSH detectada neste servidor: ${FW_SSH_PORT} (confira se está certa"
info "antes de confirmar, para não perder o acesso remoto)."
FW_ENABLE="n"
if confirm "Ativar o firewall, liberando as portas ${FW_SSH_PORT} (SSH), 80 e 443?" "n"; then
    FW_ENABLE="s"
fi

DB_LABEL="PostgreSQL local"
[ "$DB_ENGINE" = "external" ] && DB_LABEL="URL de banco externa (fornecida por você)"

echo
echo -e "${C_BOLD}Resumo${C_RESET}"
echo "  Diretório do projeto  : ${APP_DIR}"
if [ -n "$DOMAIN" ]; then
    echo "  Domínio               : ${DOMAIN}$( [ -n "$WWW_ALIAS" ] && echo " (+ ${WWW_ALIAS})" )"
else
    echo "  Domínio               : (nenhum — acesso só pelo IP)"
fi
echo "  Endereço final         : ${SERVER_NAME}"
echo "  SSL                    : $( [ "$WANT_SSL" = "s" ] && echo "sim (${SSL_PROVIDER})" || echo "não" )"
echo "  Porta interna          : ${APP_PORT}"
echo "  Banco de dados         : ${DB_LABEL}"
if [ "$DB_ENGINE" = "postgres_local" ]; then
    echo "    Nome do banco        : ${DB_NAME}"
    echo "    Usuário do banco     : ${DB_USER}"
    echo "    Senha do banco       : ${DB_PASSWORD}"
fi
echo "  Usuário de sistema     : ${SERVICE_USER}"
echo "  Firewall (ufw)         : $( [ "$FW_ENABLE" = "s" ] && echo "sim (portas ${FW_SSH_PORT}, 80, 443)" || echo "não" )"
echo
[ "$DB_ENGINE" = "postgres_local" ] && warn "Anote a senha do banco acima — ela também será mostrada (e salva num arquivo só para o root) ao final da instalação."
confirm "Confirma e inicia a instalação?" "S" || die "Instalação cancelada pelo usuário."

# --------------------------------------------------------------------------
# 2. Pacotes de sistema
# --------------------------------------------------------------------------

log "Atualizando pacotes e instalando dependências de sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    build-essential libjpeg-dev zlib1g-dev \
    git curl nginx ca-certificates ffmpeg

if [ "$DB_ENGINE" = "postgres_local" ]; then
    apt-get install -y -qq postgresql postgresql-contrib libpq-dev
    systemctl enable --now postgresql
elif [ "$DB_ENGINE" = "external" ]; then
    case "$DATABASE_URL" in
        postgres*) apt-get install -y -qq postgresql-client libpq-dev ;;
    esac
fi

if [ "$WANT_SSL" = "s" ] && [ "$SSL_PROVIDER" = "letsencrypt" ]; then
    apt-get install -y -qq certbot python3-certbot-nginx
fi

if [ "$FW_ENABLE" = "s" ]; then
    apt-get install -y -qq ufw
fi

# --------------------------------------------------------------------------
# 3. Usuário de sistema e diretório
# --------------------------------------------------------------------------

log "Configurando o usuário de sistema '${SERVICE_USER}'..."
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --create-home --home-dir "/home/${SERVICE_USER}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
# Não assume que o grupo primário tem o mesmo nome do usuário — verdade
# para o "comanda" criado por este script, mas não necessariamente para
# um usuário existente reaproveitado (ex: alguns provedores de VPS criam
# o usuário inicial num grupo "users" ou "sudo" em vez de um grupo próprio).
SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}"
mkdir -p "${APP_DIR}/instance" "${APP_DIR}/app/static/uploads" "${APP_DIR}/deploy/backups"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}/instance" "${APP_DIR}/app/static/uploads" "${APP_DIR}/deploy/backups"

# --------------------------------------------------------------------------
# 4. Ambiente virtual e dependências Python
# --------------------------------------------------------------------------

log "Criando ambiente virtual e instalando dependências Python (pode levar alguns minutos)..."
run_as_app_user "cd '${APP_DIR}' && python3 -m venv venv"
run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet --upgrade pip"
run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt"

# --------------------------------------------------------------------------
# 5. Banco de dados
# --------------------------------------------------------------------------

if [ "$DB_ENGINE" = "postgres_local" ]; then
    log "Criando o banco de dados PostgreSQL local (${DB_NAME}) e o usuário (${DB_USER})..."
    # Dobra aspas simples para inserir a senha com segurança dentro do SQL
    # (a senha pode ter sido digitada manualmente e conter qualquer caractere).
    DB_PASSWORD_SQL="${DB_PASSWORD//\'/\'\'}"

    sudo -u postgres psql -v ON_ERROR_STOP=1 <<-SQL
        DO \$\$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
                CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD_SQL}';
            ELSE
                ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD_SQL}';
            END IF;
        END
        \$\$;
        SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
        WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL

    # Codifica a senha para a URL (ela pode conter caracteres como @ : / % que
    # quebrariam o formato da URL se fossem colocados sem escapar).
    DB_PASSWORD_URLENC="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$DB_PASSWORD")"
    DATABASE_URL="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD_URLENC}@localhost:5432/${DB_NAME}"
fi
# DB_ENGINE = external já tem DATABASE_URL preenchida pela pergunta acima.

# --------------------------------------------------------------------------
# 6. Arquivo .env
# --------------------------------------------------------------------------

log "Gerando o arquivo .env de produção..."
if [ -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/.env" "${APP_DIR}/.env.bak.$(date +%Y%m%d%H%M%S)"
    info "Uma cópia do .env anterior foi salva (.env.bak.<timestamp>)."
fi

SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
FORCE_HTTPS="false"
[ "$WANT_SSL" = "s" ] && FORCE_HTTPS="true"

cat > "${APP_DIR}/.env" <<ENVEOF
# Gerado por deploy/install.sh em $(date -Iseconds)
FLASK_ENV=production
SECRET_KEY=${SECRET_KEY}
DATABASE_URL=${DATABASE_URL}
FORCE_HTTPS=${FORCE_HTTPS}

# Rate limiting: em memória por padrão (por worker). Para um limite
# global entre workers, instale Redis e descomente a linha abaixo.
# RATELIMIT_STORAGE_URI=redis://localhost:6379/0

# Integração Asaas (opcional) — deixe em branco para manter o
# lançamento manual de faturas. Preencha para habilitar cobrança real.
ASAAS_API_KEY=
ASAAS_ENVIRONMENT=sandbox
ASAAS_WEBHOOK_TOKEN=
ENVEOF

chown "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}/.env"
chmod 600 "${APP_DIR}/.env"

# --------------------------------------------------------------------------
# 7. Migrations e dados iniciais
# --------------------------------------------------------------------------

log "Rodando as migrations do banco de dados..."
run_as_app_user "cd '${APP_DIR}' && venv/bin/flask db upgrade"

if confirm "Criar um usuário Super Admin agora?" "S"; then
    run_as_app_user "cd '${APP_DIR}' && venv/bin/flask create-admin"
else
    info "Você pode criar depois com: sudo -u ${SERVICE_USER} bash -lc \"cd ${APP_DIR} && venv/bin/flask create-admin\""
fi

# --------------------------------------------------------------------------
# 8. Serviço systemd (Gunicorn)
# --------------------------------------------------------------------------

log "Configurando o serviço systemd..."
render_template "${TEMPLATES_DIR}/comanda.service.tmpl" "/etc/systemd/system/comanda.service"
systemctl daemon-reload
systemctl enable comanda
systemctl restart comanda
sleep 2
if ! systemctl is-active --quiet comanda; then
    err "O serviço 'comanda' não subiu corretamente. Últimas linhas do log:"
    journalctl -u comanda --no-pager -n 40
    die "Corrija o problema acima e rode: systemctl restart comanda"
fi
info "Serviço 'comanda' ativo (systemctl status comanda / journalctl -u comanda -f)."

# --------------------------------------------------------------------------
# 9. Nginx
# --------------------------------------------------------------------------

log "Configurando o Nginx..."
render_template "${TEMPLATES_DIR}/nginx.conf.tmpl" "/etc/nginx/sites-available/comanda"
ln -sf /etc/nginx/sites-available/comanda /etc/nginx/sites-enabled/comanda
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx || systemctl restart nginx

# --------------------------------------------------------------------------
# 10. SSL
# --------------------------------------------------------------------------

if [ "$WANT_SSL" = "s" ]; then
    CERT_DOMAINS=("-d" "${DOMAIN}")
    [ -n "$WWW_ALIAS" ] && CERT_DOMAINS+=("-d" "${WWW_ALIAS}")

    if [ "$SSL_PROVIDER" = "letsencrypt" ]; then
        log "Emitindo certificado com Let's Encrypt (certbot)..."
        if certbot --nginx --non-interactive --agree-tos -m "${SSL_EMAIL}" "${CERT_DOMAINS[@]}" --redirect; then
            info "Certificado emitido para ${SSL_NAMES}. A renovação automática já está agendada (certbot.timer)."
        else
            warn "A emissão do certificado falhou. Confira se o DNS de ${SSL_NAMES} já aponta para ${PUBLIC_IP:-este servidor} e rode depois:"
            warn "  certbot --nginx ${CERT_DOMAINS[*]}"
        fi
    else
        log "Instalando acme.sh e emitindo certificado com ZeroSSL..."
        if [ ! -x "/root/.acme.sh/acme.sh" ]; then
            curl -fsSL https://get.acme.sh | sh -s email="${SSL_EMAIL}"
        fi
        ACME="/root/.acme.sh/acme.sh"
        "$ACME" --set-default-ca --server zerossl || true
        "$ACME" --register-account -m "${SSL_EMAIL}" --server zerossl || true
        mkdir -p "/etc/nginx/ssl/${CERT_NAME}"
        if "$ACME" --issue "${CERT_DOMAINS[@]}" --nginx; then
            "$ACME" --install-cert -d "${DOMAIN}" \
                --key-file       "/etc/nginx/ssl/${CERT_NAME}/privkey.pem" \
                --fullchain-file "/etc/nginx/ssl/${CERT_NAME}/fullchain.pem" \
                --reloadcmd      "systemctl reload nginx"
            render_template "${TEMPLATES_DIR}/nginx-ssl.conf.tmpl" "/etc/nginx/sites-available/comanda"
            nginx -t && systemctl reload nginx
            info "Certificado ZeroSSL emitido para ${SSL_NAMES} e instalado. acme.sh já agenda a renovação automática via cron."
        else
            warn "A emissão do certificado ZeroSSL falhou. Confira se o DNS de ${SSL_NAMES} já aponta para ${PUBLIC_IP:-este servidor} e rode depois:"
            warn "  ${ACME} --issue ${CERT_DOMAINS[*]} --nginx"
        fi
    fi
fi

# --------------------------------------------------------------------------
# 11. Firewall (ufw)
# --------------------------------------------------------------------------

if [ "$FW_ENABLE" = "s" ]; then
    log "Configurando o firewall (ufw)..."
    ufw allow "${FW_SSH_PORT}/tcp" comment "SSH" >/dev/null
    ufw allow "80/tcp" comment "HTTP" >/dev/null
    ufw allow "443/tcp" comment "HTTPS" >/dev/null
    ufw --force enable
    info "Firewall ativo. Portas liberadas: ${FW_SSH_PORT} (SSH), 80 (HTTP), 443 (HTTPS)."
    info "Para liberar outra porta depois: ufw allow <porta>/tcp"
else
    info "Firewall (ufw) não foi ativado. Configure depois se quiser essa camada extra de proteção."
fi

# --------------------------------------------------------------------------
# 12. Salva a configuração para o deploy/update.sh
# --------------------------------------------------------------------------

cat > "${CONF_FILE}" <<CONFEOF
APP_DIR="${APP_DIR}"
SERVICE_USER="${SERVICE_USER}"
SERVICE_NAME="comanda"
APP_PORT="${APP_PORT}"
DB_ENGINE="${DB_ENGINE}"
DOMAIN="${DOMAIN}"
WWW_ALIAS="${WWW_ALIAS}"
SERVER_NAME="${SERVER_NAME}"
SSL_ENABLED="${WANT_SSL}"
CONFEOF
chmod 600 "${CONF_FILE}"

echo
log "Instalação concluída!"
if [ "$WANT_SSL" = "s" ]; then
    echo "  Acesse: https://${DOMAIN}/"
    [ -n "$WWW_ALIAS" ] && echo "          https://${WWW_ALIAS}/"
elif [ -n "$DOMAIN" ]; then
    echo "  Acesse: http://${DOMAIN}/"
    [ -n "$WWW_ALIAS" ] && echo "          http://${WWW_ALIAS}/"
else
    echo "  Acesse: http://${SERVER_NAME}/"
fi
echo "  Logs da aplicação : journalctl -u comanda -f"
echo "  Reiniciar         : systemctl restart comanda"
echo "  Atualizar         : sudo bash ${APP_DIR}/deploy/update.sh"
echo

if [ "$DB_ENGINE" = "postgres_local" ]; then
    CRED_FILE="/root/comanda-db-credentials-$(date +%Y%m%d%H%M%S).txt"
    cat > "$CRED_FILE" <<CREDEOF
Credenciais do banco de dados PostgreSQL do Comanda
Geradas em $(date -Iseconds) por deploy/install.sh — únicas desta instalação
(uma reinstalação gera uma senha nova, para que o vazamento de uma não
comprometa outro servidor).

Host      : localhost
Porta     : 5432
Banco     : ${DB_NAME}
Usuário   : ${DB_USER}
Senha     : ${DB_PASSWORD}

A mesma informação (via DATABASE_URL) está em ${APP_DIR}/.env, legível só
pelo usuário de sistema '${SERVICE_USER}' e pelo root.

Guarde esta senha num gerenciador de senhas e depois apague este arquivo:
  rm -f ${CRED_FILE}
CREDEOF
    chown root:root "$CRED_FILE"
    chmod 600 "$CRED_FILE"

    echo -e "${C_BOLD}Credenciais do banco de dados${C_RESET}"
    echo "  Banco   : ${DB_NAME}"
    echo "  Usuário : ${DB_USER}"
    echo "  Senha   : ${DB_PASSWORD}"
    echo
    warn "A senha acima só é mostrada nesta execução. Também foi salva (uma"
    warn "única vez) em ${CRED_FILE}, legível só pelo root — guarde-a num"
    warn "gerenciador de senhas e depois apague esse arquivo."
    echo
fi
