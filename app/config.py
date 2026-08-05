"""
Configurações da aplicação.

A escolha do ambiente é feita pela variável de ambiente FLASK_ENV
(development | testing | production), lida no factory `create_app`.

- DevelopmentConfig -> SQLite local, pronto para rodar sem nenhuma
  infraestrutura externa (bom para testes rápidos e onboarding).
- ProductionConfig  -> PostgreSQL, exige variáveis de ambiente reais.
- TestingConfig     -> SQLite em memória, usado pelo pytest.
"""

import os
import tempfile
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


class BaseConfig:
    """Configurações comuns a todos os ambientes."""

    # --- Segredos ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

    # --- SQLAlchemy ---
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # evita erro de conexão "stale" em produção
    }

    # --- Sessão / cookies ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    # --- Upload de imagens/vídeo ---
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads")
    # Precisa ser >= MAX_VIDEO_BYTES (app/utils/uploads.py) — esse é o teto
    # de tamanho de REQUEST do Flask/Werkzeug, aplicado antes do nosso
    # próprio código rodar. Se ficasse menor que MAX_VIDEO_BYTES (como
    # estava: 8MB aqui vs 25MB lá), um vídeo entre 8 e 25MB nunca chegava a
    # cair na validação com mensagem amigável — o Werkzeug já cortava a
    # conexão antes com um 413 genérico.
    MAX_CONTENT_LENGTH = 26 * 1024 * 1024  # 26 MB por request
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

    # --- Multi-tenant ---
    # Estratégia adotada: banco compartilhado (shared database, shared schema)
    # com isolamento por coluna `tenant_id` em todas as tabelas de domínio da
    # loja. É a estratégia recomendada para SaaS com muitos tenants pequenos/
    # médios (evita explosão operacional de N schemas ou N bancos).
    TENANT_HEADER = "X-Tenant-Slug"  # usado em modo API, se necessário

    # --- Paginação ---
    ITEMS_PER_PAGE = 20

    # --- Rate limiting (login) ---
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # --- WhatsApp / integrações ---
    WHATSAPP_DEFAULT_COUNTRY_CODE = "55"

    # --- Asaas (cobrança das faturas dos lojistas) ---
    # Deixe ASAAS_API_KEY vazia para manter a integração desligada (o
    # sistema continua funcionando normalmente com lançamento manual de
    # fatura pelo Super Admin — ver AdminBillingService). Preencha para
    # habilitar geração de cobrança real (boleto/Pix/cartão) e liberação
    # automática via webhook quando o pagamento for confirmado.
    ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "")
    ASAAS_ENVIRONMENT = os.environ.get("ASAAS_ENVIRONMENT", "sandbox")  # "sandbox" ou "production"
    # Token que o Asaas deve enviar de volta no cabeçalho do webhook
    # (configurado manualmente no painel do Asaas ao cadastrar a URL de
    # webhook) — sem isso, qualquer um poderia forjar uma notificação de
    # "pagamento confirmado" e liberar uma loja bloqueada de graça.
    ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN", "")

    # --- App Android (TWA) publicada na Play Store ---
    # Preencha depois de gerar o pacote Android (ex: com o Bubblewrap ou
    # o PWABuilder) e obter o certificado de assinatura no Play Console
    # — ver /.well-known/assetlinks.json em app/__init__.py. Enquanto
    # ficarem vazias, o arquivo responde uma lista vazia (válido, só diz
    # "nenhum app Android verificado ainda" — não quebra nada).
    ANDROID_TWA_PACKAGE_NAME = os.environ.get("ANDROID_TWA_PACKAGE_NAME", "")
    ANDROID_TWA_SHA256_FINGERPRINTS = [
        fp.strip()
        for fp in os.environ.get("ANDROID_TWA_SHA256_FINGERPRINTS", "").split(",")
        if fp.strip()
    ]


class DevelopmentConfig(BaseConfig):
    """Ambiente local de desenvolvimento — SQLite, pronto pra uso imediato."""

    DEBUG = True
    # `os.environ.get("DATABASE_URL", fallback)` sozinho NÃO cai no
    # fallback quando a variável existe só que vazia (ex: ".env" copiado
    # de ".env.example" com a linha "DATABASE_URL=" sem valor) — o `or`
    # cobre esse caso, como o comentário no .env promete ("pode deixar
    # em branco, cai no default").
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'dev.db')}"
    SESSION_COOKIE_SECURE = False


class TestingConfig(BaseConfig):
    """Ambiente de testes automatizados — SQLite em memória."""

    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False

    # Uploads de teste vão para uma pasta temporária, nunca para
    # app/static/uploads/ — evita sujar o repositório com imagens de teste.
    UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "cardapio_saas_test_uploads")


class ProductionConfig(BaseConfig):
    """Ambiente de produção — PostgreSQL obrigatório."""

    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    # Por padrão, exige HTTPS para o cookie de sessão (correto e mais
    # seguro). Se você está subindo em produção SEM HTTPS configurado
    # ainda (ex: testando direto no IP do servidor, sem proxy/certificado),
    # o login vai parecer "não funcionar" — o navegador recebe o cookie
    # marcado como Secure e recusa guardá-lo numa conexão HTTP simples.
    # Nesse caso, defina FORCE_HTTPS=false temporariamente até configurar
    # HTTPS de verdade (ex: com um proxy reverso + Let's Encrypt).
    SESSION_COOKIE_SECURE = os.environ.get("FORCE_HTTPS", "true").lower() != "false"
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"

    # Cache de 1 ano para /static/* no navegador — seguro porque todo
    # asset estático importado em base.html usa static_versioned()
    # (app/__init__.py), que muda a URL sozinha quando o arquivo muda.
    # Sem isso, cada navegação entre páginas (app multi-página, sem SPA)
    # faz o navegador reconferir com o servidor se o CSS/JS mudou.
    SEND_FILE_MAX_AGE_DEFAULT = 31536000


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
