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

set -Eeuo pipefail  # -E (errtrace): sem isso, o "trap ... ERR" abaixo não
                    # dispara para erros dentro de funções (run_as_app_user,
                    # render_template etc.) — ou seja, para quase todo o script.
umask 077

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"
CONF_FILE="${SCRIPT_DIR}/.deploy.conf"

C_RESET="\033[0m"; C_BOLD="\033[1m"; C_DIM="\033[2m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_BLUE="\033[34m"; C_CYAN="\033[36m"

log()  { echo -e "${C_GREEN}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info() { echo -e "    $*"; }
warn() { echo -e "${C_YELLOW}==> AVISO:${C_RESET} $*"; }
err()  { echo -e "${C_RED}==> ERRO:${C_RESET} $*" >&2; }
die()  { err "$*"; exit 1; }

# Nome da etapa em andamento (atualizado no início de cada seção numerada
# abaixo) — usado só pelo trap de erro, pra dizer EM QUAL PARTE da
# instalação algo quebrou. Sem isso, uma falha inesperada (rede caindo no
# meio de um apt-get, disco cheio, um pacote com problema etc.) simplesmente
# parava o script com o erro cru do comando que falhou, sem contexto — dava
# a impressão de "a instalação não fez nada" mesmo quando várias etapas
# antes já tinham funcionado.
STEP="início"
on_error() {
    local exit_code=$? line=$1
    err "Falha inesperada na etapa '${STEP}' (linha ${line} do script, código ${exit_code})."
    err "Confira a mensagem de erro do comando logo acima — geralmente ela já diz o motivo"
    err "(pacote faltando, rede, permissão, disco cheio...). Depois de corrigir, é seguro"
    err "rodar 'sudo bash deploy/install.sh' de novo: ele detecta o que já está pronto"
    err "(usuário de sistema, ambiente virtual, banco de dados) e não refaz do zero."
    exit 1
}
trap 'on_error $LINENO' ERR

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

valid_email() {
    # Checagem simples (não é RFC 5322 completa, só o suficiente pra pegar
    # entradas obviamente erradas, tipo alguém digitar "2" sem querer aqui
    # — o servidor da ACME (Let's Encrypt/ZeroSSL) rejeita o registro
    # inteiro quando o e-mail é inválido, derrubando a emissão do
    # certificado bem no meio da instalação.
    [[ "$1" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]
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
# Menu principal — só aparece quando já existe uma instalação anterior
# (deploy/.deploy.conf). Numa instalação nova o script segue direto para o
# fluxo guiado de sempre (seções 1 em diante). As funções abaixo usam as
# variáveis carregadas do CONF_FILE (APP_DIR, SERVICE_USER, SERVICE_NAME,
# APP_PORT, DOMAIN, WWW_ALIAS, SSL_ENABLED, DB_ENGINE) — por isso só são
# chamadas depois do "source $CONF_FILE" feito na seção 0.
#
# Chrome visual do menu (banner()/section()/item()/ok()/fail()) fica só em
# ASCII puro de propósito — sem caracteres de desenho de caixa (╔═╗ etc.)
# nem centralização calculada por comprimento de string, que dependem de
# locale/UTF-8 do terminal e já causaram bug sutil de contagem de bytes em
# outro ponto deste script (ver STEP="Firewall"); aqui o risco era só
# cosmético, mas o padrão do projeto é preferir a opção que não pode
# quebrar de forma silenciosa num terminal/locale diferente do nosso.
# --------------------------------------------------------------------------

banner() {
    # banner "título" -> cabeçalho de destaque (usado só no menu principal)
    echo
    echo -e "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
    echo -e "${C_CYAN}${C_BOLD}  $1${C_RESET}"
    echo -e "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
}

section() {
    # section "título" -> cabeçalho leve (usado nos submenus e resultados)
    echo
    echo -e "${C_CYAN}${C_BOLD}▸ $1${C_RESET}"
    echo -e "${C_CYAN}------------------------------------------------------------${C_RESET}"
}

item() {
    # item NUM "texto" ["nota opcional em cinza"]
    if [ -n "${3:-}" ]; then
        echo -e "  ${C_GREEN}${C_BOLD}$1${C_RESET}) $2  ${C_DIM}($3)${C_RESET}"
    else
        echo -e "  ${C_GREEN}${C_BOLD}$1${C_RESET}) $2"
    fi
}

ok()   { info "${C_GREEN}✔${C_RESET} $1"; }
fail() { warn "${C_RED}✘${C_RESET} $1"; }

pause_menu() {
    echo
    ask "Pressione Enter para voltar" ""
}

manage_super_admin_menu() {
    while true; do
        section "Gerenciar Super Admin"
        run_as_app_user "cd '${APP_DIR}' && venv/bin/flask list-admins" \
            || warn "Não consegui listar os Super Admins agora (veja o erro acima)."
        echo
        item 1 "Criar um novo Super Admin" "ou redefinir a senha de um já existente"
        item 2 "Trocar o e-mail de um Super Admin existente"
        item 3 "Remover um Super Admin"
        item 4 "Voltar ao menu principal"
        ask "Escolha uma opção [1-4]" "4"
        case "$REPLY_VALUE" in
            1)
                info "Se o e-mail informado já existir, só a senha dele é atualizada (o cadastro não é duplicado)."
                run_as_app_user "cd '${APP_DIR}' && venv/bin/flask create-admin" \
                    || warn "Não consegui criar/atualizar o Super Admin agora (veja o erro acima)."
                ;;
            2)
                ask "E-mail ATUAL do Super Admin" ""
                admin_old_email="$REPLY_VALUE"
                [ -n "$admin_old_email" ] || { warn "E-mail em branco, operação cancelada."; continue; }
                ask "NOVO e-mail" ""
                admin_new_email="$REPLY_VALUE"
                [ -n "$admin_new_email" ] || { warn "E-mail em branco, operação cancelada."; continue; }
                run_as_app_user "cd '${APP_DIR}' && venv/bin/flask update-admin-email --old '${admin_old_email}' --new '${admin_new_email}'" \
                    || warn "Não consegui trocar o e-mail agora (veja o erro acima)."
                ;;
            3)
                ask "E-mail do Super Admin a remover" ""
                admin_del_email="$REPLY_VALUE"
                [ -n "$admin_del_email" ] || { warn "E-mail em branco, operação cancelada."; continue; }
                warn "Isso remove o acesso desse Super Admin imediatamente e não pode ser desfeito."
                if confirm "Confirma a remoção de '${admin_del_email}'?" "n"; then
                    run_as_app_user "cd '${APP_DIR}' && venv/bin/flask remove-admin --email '${admin_del_email}' --yes" \
                        || warn "Não consegui remover o Super Admin agora (veja o erro acima)."
                else
                    info "Remoção cancelada."
                fi
                ;;
            4|*) return 0 ;;
        esac
    done
}

run_verificacoes() {
    section "Verificações do sistema"

    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        ok "Serviço '${SERVICE_NAME}' está ativo."
    else
        fail "Serviço '${SERVICE_NAME}' NÃO está ativo. Diagnóstico: systemctl status ${SERVICE_NAME}"
    fi

    if command -v nginx >/dev/null 2>&1; then
        if nginx -t >/dev/null 2>&1; then
            ok "Configuração do Nginx é válida."
        else
            fail "'nginx -t' encontrou um problema na configuração atual (rode 'nginx -t' para ver o erro)."
        fi
        if systemctl is-active --quiet nginx 2>/dev/null; then
            ok "Nginx está ativo."
        else
            fail "Nginx NÃO está ativo."
        fi
    fi

    if command -v curl >/dev/null 2>&1; then
        http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${APP_PORT}/" 2>/dev/null || echo "000")"
        if [ "$http_code" != "000" ]; then
            ok "Aplicação responde localmente na porta ${APP_PORT} (HTTP ${http_code})."
        else
            fail "Aplicação não respondeu localmente na porta ${APP_PORT} — diagnóstico: journalctl -u ${SERVICE_NAME} -n 50"
        fi
    fi

    if run_as_app_user "cd '${APP_DIR}' && venv/bin/flask check-db" >/dev/null 2>&1; then
        ok "Conexão com o banco de dados OK."
    else
        fail "Não consegui confirmar a conexão com o banco de dados — diagnóstico: sudo -u ${SERVICE_USER} bash -lc \"cd ${APP_DIR} && venv/bin/flask check-db\""
    fi

    if [ "${SSL_ENABLED:-n}" = "s" ] && [ -n "${DOMAIN:-}" ] && command -v openssl >/dev/null 2>&1; then
        cert_expiry="$(echo | timeout 5 openssl s_client -servername "${DOMAIN}" -connect "${DOMAIN}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
        if [ -n "$cert_expiry" ]; then
            ok "Certificado SSL de ${DOMAIN} válido até: ${cert_expiry}"
        else
            fail "Não consegui confirmar o certificado SSL de ${DOMAIN} (porta 443 pode estar bloqueada ou o certificado ainda não foi emitido)."
        fi
    fi

    if [ -d "${APP_DIR}" ]; then
        disk_info="$(df -h "${APP_DIR}" 2>/dev/null | awk 'NR==2{print $4" livres de "$2" ("$5" em uso)"}')"
        [ -n "$disk_info" ] && info "Espaço em disco em ${APP_DIR}: ${disk_info}"
    fi

    pause_menu
}

run_update_script() {
    section "Atualizar sistema"
    log "Rodando deploy/update.sh..."
    if bash "${SCRIPT_DIR}/update.sh"; then
        info "deploy/update.sh terminou (veja o resumo/STATUS acima para o resultado exato)."
    else
        update_rc=$?
        warn "deploy/update.sh terminou com código de saída ${update_rc} — veja as mensagens acima (linha STATUS=...) para entender o que aconteceu."
    fi
    pause_menu
}

restart_service() {
    section "Reiniciar o serviço da aplicação"
    if confirm "Reiniciar '${SERVICE_NAME}' agora? A aplicação fica indisponível por alguns segundos." "n"; then
        if systemctl restart "${SERVICE_NAME}"; then
            sleep 2
            if systemctl is-active --quiet "${SERVICE_NAME}"; then
                ok "Serviço reiniciado e está ativo."
            else
                fail "O serviço foi reiniciado mas não ficou ativo — diagnóstico: journalctl -u ${SERVICE_NAME} -n 50"
            fi
        else
            fail "Falha ao reiniciar (veja o erro acima)."
        fi
    else
        info "Cancelado."
    fi
    pause_menu
}

reload_nginx() {
    section "Recarregar o Nginx"
    if ! command -v nginx >/dev/null 2>&1; then
        warn "Nginx não parece estar instalado neste servidor."
        pause_menu
        return
    fi
    if nginx -t; then
        if systemctl reload nginx; then
            ok "Nginx recarregado."
        else
            fail "Falha ao recarregar (veja o erro acima)."
        fi
    else
        fail "'nginx -t' encontrou um problema na configuração — corrija antes de recarregar."
    fi
    pause_menu
}

show_recent_logs() {
    section "Últimas linhas do log de '${SERVICE_NAME}'"
    journalctl -u "${SERVICE_NAME}" -n 60 --no-pager 2>/dev/null || warn "Não consegui ler os logs (journalctl indisponível?)."
    pause_menu
}

show_status_summary() {
    section "Status resumido"
    echo -e "${C_BOLD}${SERVICE_NAME}:${C_RESET}"
    systemctl status "${SERVICE_NAME}" --no-pager -l 2>/dev/null | head -n 10 || warn "Não consegui consultar o status de '${SERVICE_NAME}'."
    if command -v nginx >/dev/null 2>&1; then
        echo
        echo -e "${C_BOLD}nginx:${C_RESET}"
        systemctl status nginx --no-pager -l 2>/dev/null | head -n 6 || warn "Não consegui consultar o status do Nginx."
    fi
    pause_menu
}

renew_ssl_cert() {
    section "Renovar certificado SSL"
    if [ "${SSL_ENABLED:-n}" != "s" ] || [ -z "${DOMAIN:-}" ]; then
        warn "Este servidor não tem SSL configurado (sem domínio, ou SSL não foi ativado na instalação)."
        pause_menu
        return
    fi
    log "Renovando certificado de ${DOMAIN}..."
    if command -v certbot >/dev/null 2>&1 && certbot certificates 2>/dev/null | grep -q "${DOMAIN}"; then
        if certbot renew --cert-name "${DOMAIN}" --force-renewal; then
            ok "Certificado renovado com sucesso via certbot."
        else
            fail "A renovação via certbot falhou (veja o erro acima)."
        fi
    elif [ -x "/root/.acme.sh/acme.sh" ]; then
        if /root/.acme.sh/acme.sh --renew -d "${DOMAIN}" --force; then
            ok "Certificado renovado com sucesso via acme.sh."
        else
            fail "A renovação via acme.sh falhou (veja o erro acima)."
        fi
    else
        warn "Não encontrei nem certbot nem acme.sh configurados para ${DOMAIN} — o certificado pode não ter sido emitido ainda (veja a opção 'Verificações do sistema')."
    fi
    pause_menu
}

system_commands_menu() {
    while true; do
        section "Comandos do sistema"
        item 1 "Reiniciar o serviço da aplicação" "${SERVICE_NAME}"
        item 2 "Recarregar o Nginx"
        item 3 "Ver logs recentes da aplicação"
        item 4 "Ver status resumido (serviço + Nginx)"
        item 5 "Renovar certificado SSL manualmente"
        item 6 "Voltar ao menu principal"
        ask "Escolha uma opção [1-6]" "6"
        case "$REPLY_VALUE" in
            1) restart_service ;;
            2) reload_nginx ;;
            3) show_recent_logs ;;
            4) show_status_summary ;;
            5) renew_ssl_cert ;;
            6|*) return 0 ;;
        esac
    done
}

main_menu() {
    while true; do
        banner "COMANDA · Painel de instalação e manutenção"
        echo "  Diretório : ${APP_DIR}"
        if [ -f "$CONF_FILE" ]; then
            [ -n "${DOMAIN:-}" ] && echo "  Endereço  : ${SERVER_NAME:-$DOMAIN} $([ "${SSL_ENABLED:-n}" = "s" ] && echo "(HTTPS)" || echo "(HTTP)")"
            echo
            item 1 "Atualizar sistema" "busca e aplica a versão mais nova"
            item 2 "Verificações do sistema" "serviço, Nginx, banco, SSL..."
            item 3 "Comandos do sistema" "reiniciar, logs, status, renovar SSL..."
            item 4 "Gerenciar Super Admin" "criar, redefinir, trocar e-mail, remover"
            item 5 "Reconfigurar esta instalação" "domínio, SSL, banco, systemd, Nginx..."
            item 6 "Sair"
            ask "Escolha uma opção [1-6]" "6"
            case "$REPLY_VALUE" in
                1) run_update_script ;;
                2) run_verificacoes ;;
                3) system_commands_menu ;;
                4) manage_super_admin_menu ;;
                5) return 0 ;;
                6) echo "Até mais!"; exit 0 ;;
                *) warn "Opção inválida." ;;
            esac
        else
            echo
            item 1 "Instalar o Comanda agora" "fluxo guiado completo"
            item 2 "Sair"
            ask "Escolha uma opção [1-2]" "1"
            case "$REPLY_VALUE" in
                1) return 0 ;;
                2) echo "Até mais!"; exit 0 ;;
                *) warn "Opção inválida." ;;
            esac
        fi
    done
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
    # shellcheck source=/dev/null
    source "$CONF_FILE"
fi

main_menu   # só retorna (sem sair do script) quando o usuário decide instalar ou reconfigurar

if [ -f "$CONF_FILE" ]; then
    warn "Reconfigurando uma instalação existente em ${CONF_FILE} — isso pode sobrescrever .env, Nginx e o serviço systemd (os dados do banco não são apagados)."
    if ! confirm "Continuar com a reconfiguração?" "n"; then
        die "Reconfiguração cancelada. Use a opção 'Sair' do menu se só queria consultar algo."
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
        while true; do
            ask "E-mail para avisos de expiração/renovação do certificado" "admin@${DOMAIN}"
            SSL_EMAIL="$REPLY_VALUE"
            if valid_email "$SSL_EMAIL"; then
                break
            fi
            err "'${SSL_EMAIL}' não parece um e-mail válido (ex: admin@${DOMAIN}). A ACME (Let's Encrypt/ZeroSSL) recusa o registro inteiro com um e-mail inválido — tente de novo."
        done
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
# A detecção é só um "melhor palpite" (o fallback pra 22 na linha seguinte
# já cobre qualquer caso) — mas sem o "|| true", se /etc/ssh/sshd_config
# não existir (VPS sem openssh-server instalado pelo pacote padrão,
# container mínimo etc.), o `awk` falha ao abrir o arquivo (exit 2) e, com
# `set -o pipefail`, derruba o script inteiro bem no meio das perguntas —
# antes mesmo de instalar qualquer coisa.
FW_SSH_PORT="$(awk '$1=="Port"{print $2}' /etc/ssh/sshd_config 2>/dev/null | tail -n1)" || true
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

STEP="Pacotes de sistema"
log "Atualizando pacotes e instalando dependências de sistema..."
export DEBIAN_FRONTEND=noninteractive
if ! apt-get update -qq; then
    die "'apt-get update' falhou — confira a conexão deste servidor com a internet (ou o mirror configurado em /etc/apt/sources.list) e tente de novo."
fi
if ! apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    build-essential libjpeg-dev zlib1g-dev \
    git curl nginx ca-certificates ffmpeg; then
    die "Falha instalando os pacotes de sistema básicos (veja o erro do apt-get acima)."
fi

if [ "$DB_ENGINE" = "postgres_local" ]; then
    apt-get install -y -qq postgresql postgresql-contrib libpq-dev \
        || die "Falha instalando o PostgreSQL (veja o erro do apt-get acima)."
    systemctl enable --now postgresql \
        || die "PostgreSQL foi instalado mas não consegui ativar o serviço. Rode 'systemctl status postgresql' e 'journalctl -u postgresql -n 50' para diagnosticar."
elif [ "$DB_ENGINE" = "external" ]; then
    case "$DATABASE_URL" in
        postgres*) apt-get install -y -qq postgresql-client libpq-dev \
            || die "Falha instalando o cliente PostgreSQL (veja o erro do apt-get acima)." ;;
    esac
fi

if [ "$WANT_SSL" = "s" ]; then
    # Instala o certbot mesmo quando o provedor escolhido é a ZeroSSL: se a
    # emissão via acme.sh falhar (ver seção 10 — a ZeroSSL tem um passo a
    # mais, o registro de conta via EAB, que pode falhar por problemas de
    # conectividade do próprio servidor e não tem nada a ver com o DNS do
    # domínio), o script tenta automaticamente o Let's Encrypt em seguida em
    # vez de deixar o site sem HTTPS.
    apt-get install -y -qq certbot python3-certbot-nginx
fi

if [ "$FW_ENABLE" = "s" ]; then
    apt-get install -y -qq ufw
fi

# --------------------------------------------------------------------------
# 3. Usuário de sistema e diretório
# --------------------------------------------------------------------------

STEP="Usuário de sistema e diretório"
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

STEP="Ambiente virtual e dependências Python"
log "Criando ambiente virtual e instalando dependências Python (pode levar alguns minutos)..."
run_as_app_user "cd '${APP_DIR}' && python3 -m venv venv" \
    || die "Falha ao criar o ambiente virtual Python em ${APP_DIR}/venv. Confira se 'python3-venv' está instalado e se há espaço em disco (df -h)."
run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet --upgrade pip" \
    || die "Falha ao atualizar o pip dentro do ambiente virtual. Confira a conexão com a internet."
run_as_app_user "cd '${APP_DIR}' && venv/bin/pip install --quiet -r requirements.txt" \
    || die "Falha instalando as dependências Python do projeto (requirements.txt). Veja o erro do pip acima — costuma ser rede, ou uma lib do sistema faltando pra compilar algum pacote."

# --------------------------------------------------------------------------
# 5. Banco de dados
# --------------------------------------------------------------------------

STEP="Banco de dados"
if [ "$DB_ENGINE" = "postgres_local" ]; then
    log "Criando o banco de dados PostgreSQL local (${DB_NAME}) e o usuário (${DB_USER})..."

    # Logo depois de instalar/ativar o pacote, o Postgres pode levar um
    # instante pra aceitar conexões (primeira inicialização de cluster,
    # servidor mais lento) — sem esperar, o psql abaixo falhava de vez em
    # quando com "connection refused" num servidor recém-provisionado.
    PG_READY=0
    for _ in $(seq 1 15); do
        if sudo -u postgres psql -tAc 'SELECT 1' >/dev/null 2>&1; then
            PG_READY=1
            break
        fi
        sleep 1
    done
    [ "$PG_READY" = "1" ] || die "O PostgreSQL não respondeu depois de instalado/ativado. Rode 'systemctl status postgresql' e 'journalctl -u postgresql -n 50' para diagnosticar."

    # Dobra aspas simples para inserir a senha com segurança dentro do SQL
    # (a senha pode ter sido digitada manualmente e conter qualquer caractere).
    DB_PASSWORD_SQL="${DB_PASSWORD//\'/\'\'}"

    if ! sudo -u postgres psql -v ON_ERROR_STOP=1 <<-SQL
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
    then
        die "Falha ao criar/atualizar o usuário ou o banco de dados PostgreSQL (veja o erro do psql acima)."
    fi

    # Codifica a senha para a URL (ela pode conter caracteres como @ : / % que
    # quebrariam o formato da URL se fossem colocados sem escapar).
    DB_PASSWORD_URLENC="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$DB_PASSWORD")"
    DATABASE_URL="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD_URLENC}@localhost:5432/${DB_NAME}"
fi
# DB_ENGINE = external já tem DATABASE_URL preenchida pela pergunta acima.

# --------------------------------------------------------------------------
# 6. Arquivo .env
# --------------------------------------------------------------------------

STEP="Arquivo .env"
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

STEP="Migrations e dados iniciais"
log "Rodando as migrations do banco de dados..."
run_as_app_user "cd '${APP_DIR}' && venv/bin/flask db upgrade" \
    || die "Falha ao rodar as migrations do banco de dados (veja o erro acima). O restante da instalação (systemd, Nginx, SSL) não é seguro rodar sem o schema do banco em dia — corrija o erro e rode o instalador de novo."

# A criação do Super Admin é interativa (pede nome/e-mail/senha) e,
# diferente das migrations, uma falha ou cancelamento aqui (ex: Ctrl+C,
# confirmação de senha digitada errado duas vezes) NÃO deve derrubar o
# resto da instalação — sem isso, um deslize no prompt interativo fazia o
# script morrer antes de configurar systemd/Nginx/SSL/firewall, dando a
# impressão de que "a instalação não funciona", quando só faltou esse
# passo (que pode ser feito manualmente depois, a qualquer momento).
SUPERADMIN_CRIADO=1
if confirm "Criar um usuário Super Admin agora?" "S"; then
    if ! run_as_app_user "cd '${APP_DIR}' && venv/bin/flask create-admin"; then
        SUPERADMIN_CRIADO=0
        warn "Não consegui criar o Super Admin agora — isso NÃO impede o resto da instalação."
        warn "Crie depois com: sudo -u ${SERVICE_USER} bash -lc \"cd ${APP_DIR} && venv/bin/flask create-admin\""
    fi
else
    SUPERADMIN_CRIADO=0
    info "Você pode criar depois com: sudo -u ${SERVICE_USER} bash -lc \"cd ${APP_DIR} && venv/bin/flask create-admin\""
fi

# --------------------------------------------------------------------------
# 8. Serviço systemd (Gunicorn)
# --------------------------------------------------------------------------

STEP="Serviço systemd (Gunicorn)"
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

STEP="Nginx"
log "Configurando o Nginx..."
render_template "${TEMPLATES_DIR}/nginx.conf.tmpl" "/etc/nginx/sites-available/comanda"
ln -sf /etc/nginx/sites-available/comanda /etc/nginx/sites-enabled/comanda
rm -f /etc/nginx/sites-enabled/default
nginx -t || die "O Nginx recusou a configuração gerada (/etc/nginx/sites-available/comanda) — veja o erro acima. Isso não deveria acontecer com um domínio/IP válido; se persistir, é um bug no template (deploy/templates/nginx.conf.tmpl)."
systemctl reload nginx || systemctl restart nginx || die "O Nginx não recarregou nem reiniciou. Rode 'systemctl status nginx' e 'journalctl -u nginx -n 50' para diagnosticar."

# --------------------------------------------------------------------------
# 10. SSL
# --------------------------------------------------------------------------

STEP="SSL"

# Emite (ou reemite) o certificado via Let's Encrypt/certbot — usado tanto
# quando o provedor escolhido é o Let's Encrypt quanto como fallback
# automático se a ZeroSSL falhar (ver abaixo). Devolve 0/1; não imprime
# nada em caso de sucesso nem de falha, quem chama decide a mensagem.
issue_letsencrypt_cert() {
    certbot --nginx --non-interactive --agree-tos -m "${SSL_EMAIL}" "${CERT_DOMAINS[@]}" --redirect
}

# Controla se o "Acesse:" do resumo final oferece https:// — sem isso, o
# resumo prometia HTTPS mesmo quando a emissão do certificado falhava (nos
# dois provedores), levando a pessoa a testar uma URL que ainda não existe.
SSL_OK=0

if [ "$WANT_SSL" = "s" ]; then
    CERT_DOMAINS=("-d" "${DOMAIN}")
    [ -n "$WWW_ALIAS" ] && CERT_DOMAINS+=("-d" "${WWW_ALIAS}")

    if [ "$SSL_PROVIDER" = "letsencrypt" ]; then
        log "Emitindo certificado com Let's Encrypt (certbot)..."
        if issue_letsencrypt_cert; then
            SSL_OK=1
            info "Certificado emitido para ${SSL_NAMES}. A renovação automática já está agendada (certbot.timer)."
        else
            warn "A emissão do certificado falhou. Isso quase sempre significa que o DNS de ${SSL_NAMES} ainda não aponta para ${PUBLIC_IP:-este servidor} (o certbot usa esse endereço pra validar que o domínio é seu). Confira com 'getent hosts ${DOMAIN}' e, quando resolver certo, rode:"
            warn "  certbot --nginx ${CERT_DOMAINS[*]} --redirect"
        fi
    else
        log "Instalando acme.sh e emitindo certificado com ZeroSSL..."
        if [ ! -x "/root/.acme.sh/acme.sh" ]; then
            curl -fsSL https://get.acme.sh | sh -s email="${SSL_EMAIL}"
        fi
        ACME="/root/.acme.sh/acme.sh"
        "$ACME" --set-default-ca --server zerossl || true

        ZEROSSL_OK=1
        if ! "$ACME" --register-account -m "${SSL_EMAIL}" --server zerossl; then
            ZEROSSL_OK=0
            warn "Não consegui registrar a conta junto à ZeroSSL (erro comum: 'Cannot resolve _eab_kid')."
            warn "Isso normalmente é uma falha de conectividade DESTE SERVIDOR com a API da"
            warn "ZeroSSL (não tem relação com o DNS do seu domínio) — pode ser IPv6 mal"
            warn "configurado na VPS, firewall de saída ou instabilidade momentânea deles."
        fi

        mkdir -p "/etc/nginx/ssl/${CERT_NAME}"
        if [ "$ZEROSSL_OK" = "1" ] && "$ACME" --issue "${CERT_DOMAINS[@]}" --nginx; then
            "$ACME" --install-cert -d "${DOMAIN}" \
                --key-file       "/etc/nginx/ssl/${CERT_NAME}/privkey.pem" \
                --fullchain-file "/etc/nginx/ssl/${CERT_NAME}/fullchain.pem" \
                --reloadcmd      "systemctl reload nginx"
            render_template "${TEMPLATES_DIR}/nginx-ssl.conf.tmpl" "/etc/nginx/sites-available/comanda"
            nginx -t && systemctl reload nginx
            SSL_OK=1
            info "Certificado ZeroSSL emitido para ${SSL_NAMES} e instalado. acme.sh já agenda a renovação automática via cron."
        else
            if [ "$ZEROSSL_OK" = "1" ]; then
                warn "A emissão do certificado ZeroSSL falhou. Se a conta foi registrada mas a emissão em si não, geralmente é o DNS de ${SSL_NAMES} ainda não apontando para ${PUBLIC_IP:-este servidor} — confira com 'getent hosts ${DOMAIN}'."
            fi
            log "Tentando emitir com Let's Encrypt como alternativa (não depende da ZeroSSL)..."
            if issue_letsencrypt_cert; then
                SSL_OK=1
                info "Certificado emitido para ${SSL_NAMES} via Let's Encrypt (a ZeroSSL falhou, mas o site já está com HTTPS). A renovação automática já está agendada (certbot.timer)."
            else
                warn "A emissão automática também falhou pelo Let's Encrypt. Confira se o DNS de ${SSL_NAMES} já aponta para ${PUBLIC_IP:-este servidor} ('getent hosts ${DOMAIN}') e, quando resolver certo, rode um destes:"
                warn "  certbot --nginx ${CERT_DOMAINS[*]} --redirect        (Let's Encrypt — recomendado)"
                warn "  ${ACME} --issue ${CERT_DOMAINS[*]} --nginx           (ZeroSSL)"
            fi
        fi
    fi
fi

# O .env foi escrito na seção 6 com FORCE_HTTPS baseado na INTENÇÃO de usar
# SSL (WANT_SSL), antes de sabermos se a emissão do certificado realmente
# funcionou — e o serviço systemd já foi iniciado na seção 8, antes desta
# seção rodar. Se o certificado não saiu, deixar FORCE_HTTPS=true faz o
# Flask marcar o cookie de sessão como Secure (SESSION_COOKIE_SECURE), que
# o navegador simplesmente não envia de volta em HTTP puro — o login para
# de funcionar sem nenhum erro visível. Aqui corrigimos o .env para refletir
# o resultado real (SSL_OK) e reiniciamos o serviço só se o valor mudou.
STEP="Ajuste final do .env conforme o resultado do SSL"

DESIRED_FORCE_HTTPS="false"
[ "$SSL_OK" = "1" ] && DESIRED_FORCE_HTTPS="true"

if [ "$DESIRED_FORCE_HTTPS" != "$FORCE_HTTPS" ]; then
    if ! sed -i "s/^FORCE_HTTPS=.*/FORCE_HTTPS=${DESIRED_FORCE_HTTPS}/" "${APP_DIR}/.env"; then
        warn "Não consegui atualizar FORCE_HTTPS em ${APP_DIR}/.env (valor atual pode estar incorreto). Ajuste manualmente para FORCE_HTTPS=${DESIRED_FORCE_HTTPS} e rode: systemctl restart comanda"
    else
        FORCE_HTTPS="$DESIRED_FORCE_HTTPS"
        systemctl restart comanda || warn "FORCE_HTTPS foi atualizado no .env, mas não consegui reiniciar o serviço 'comanda'. Rode manualmente: systemctl restart comanda"
        if [ "$DESIRED_FORCE_HTTPS" = "false" ]; then
            warn "FORCE_HTTPS foi desligado em ${APP_DIR}/.env porque o certificado SSL não pôde ser emitido — sem isso, o login ficaria quebrado em HTTP (cookie de sessão marcado como só-HTTPS). Depois de resolver o SSL, rode o instalador de novo (ou emita o certificado manualmente e ajuste FORCE_HTTPS=true) para religar."
        else
            info "FORCE_HTTPS foi ativado em ${APP_DIR}/.env (HTTPS confirmado) e o serviço 'comanda' foi reiniciado."
        fi
    fi
fi

# --------------------------------------------------------------------------
# 11. Firewall (ufw)
# --------------------------------------------------------------------------

STEP="Firewall (ufw)"
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

STEP="Salvando configuração"
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
if [ "$SSL_OK" = "1" ]; then
    echo "  Acesse: https://${DOMAIN}/"
    [ -n "$WWW_ALIAS" ] && echo "          https://${WWW_ALIAS}/"
elif [ -n "$DOMAIN" ]; then
    echo "  Acesse: http://${DOMAIN}/"
    [ -n "$WWW_ALIAS" ] && echo "          http://${WWW_ALIAS}/"
    [ "$WANT_SSL" = "s" ] && warn "SSL foi solicitado mas a emissão do certificado falhou (veja os avisos acima) — o site está no ar só em HTTP por enquanto."
else
    echo "  Acesse: http://${SERVER_NAME}/"
fi
echo "  Logs da aplicação : journalctl -u comanda -f"
echo "  Reiniciar         : systemctl restart comanda"
echo "  Atualizar         : sudo bash ${APP_DIR}/deploy/update.sh"
if [ "$SUPERADMIN_CRIADO" = "0" ]; then
    echo "  Criar Super Admin : sudo -u ${SERVICE_USER} bash -lc \"cd ${APP_DIR} && venv/bin/flask create-admin\""
fi
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
