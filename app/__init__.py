"""
Application factory.

Uso:
    from app import create_app
    app = create_app("development")
"""

import os

from flask import Flask, jsonify, render_template, request

from app.config import config_by_name
from app.extensions import db, migrate, login_manager, bcrypt, csrf, ma, limiter


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_by_name[config_name])

    if config_name == "production":
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError(
                "DATABASE_URL não configurada. Defina uma URL de conexão "
                "PostgreSQL válida em produção, ex: "
                "postgresql+psycopg2://user:pass@host:5432/dbname"
            )
        if not app.config.get("SECRET_KEY") or app.config.get("SECRET_KEY") == "troque-esta-chave-em-producao":
            raise RuntimeError(
                "SECRET_KEY não configurada (ou deixada no valor padrão de "
                "desenvolvimento) em produção. Defina uma chave aleatória e "
                "secreta, ex: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )

        # Essencial quando a aplicação roda atrás de um proxy reverso
        # (nginx, Render, Railway, Fly.io, um load balancer, etc.): sem
        # isso, o Flask não sabe que a conexão original do navegador foi
        # HTTPS (só vê a conexão interna HTTP do proxy para o gunicorn),
        # o que quebra geração de URL absoluta (https vs http) e pode
        # interferir no cookie de sessão marcado como "Secure".
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

        # RATELIMIT_STORAGE_URI cai em "memory://" por padrão (ver
        # config.py) — cada processo do gunicorn conta as tentativas de
        # login separadamente. Com `gunicorn -w N` (N processos, o normal
        # em produção), o limite efetivo de tentativas de força bruta
        # vira N vezes o configurado (ex.: "10 por minuto" com 4 workers
        # deixa passar até 40/minuto por IP). Não é um erro fatal — o
        # rate limit ainda existe, só fica mais fraco — então só avisamos
        # no log em vez de bloquear a inicialização.
        if app.config.get("RATELIMIT_STORAGE_URI", "memory://").startswith("memory://"):
            import logging

            logging.getLogger(__name__).warning(
                "RATELIMIT_STORAGE_URI não configurado (usando memory://) em "
                "produção — o limite de tentativas de login é por PROCESSO, "
                "não compartilhado entre os workers do gunicorn. Configure "
                "RATELIMIT_STORAGE_URI=redis://... para um limite de força "
                "bruta efetivo com mais de 1 worker."
            )

    # Garante que a pasta instance/ e a pasta de uploads existam
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    _register_extensions(app)
    _register_blueprints(app)
    _register_cli_commands(app)
    _register_request_hooks(app)
    _register_error_handlers(app)
    _register_context_processors(app)
    _register_template_filters(app)

    return app


def _register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    ma.init_app(app)
    limiter.init_app(app)

    # Import necessário aqui (depois do db.init_app) para registrar o
    # user_loader sem causar import circular.
    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    from app.controllers.auth_controller import auth_bp
    from app.controllers.admin import admin_bp
    from app.controllers.lojista import lojista_bp
    from app.controllers.public_controller import public_bp
    from app.controllers.feedback_public_controller import feedback_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(lojista_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(feedback_bp)

    from app.controllers.webhooks_controller import webhooks_bp

    app.register_blueprint(webhooks_bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/.well-known/assetlinks.json")
    def android_asset_links():
        """Digital Asset Links — a Play Store exige isso pra confirmar
        que o app Android (TWA, gerado com Bubblewrap/PWABuilder a
        partir de app/static/manifest.json) e este domínio pertencem ao
        mesmo dono, e assim abrir em tela cheia sem barra de endereço
        (sem isso, o Android some com a barra de navegação nativa mas
        deixa a barra de URL do Chrome visível por segurança).

        Preencha ANDROID_TWA_PACKAGE_NAME e ANDROID_TWA_SHA256_FINGERPRINTS
        (ver app/config.py) com os dados do Play Console DEPOIS de gerar
        o pacote Android — até lá, responde uma lista vazia (formato
        válido, só diz "nenhum app Android verificado ainda")."""
        package_name = app.config.get("ANDROID_TWA_PACKAGE_NAME", "")
        fingerprints = app.config.get("ANDROID_TWA_SHA256_FINGERPRINTS", [])
        if not package_name or not fingerprints:
            return jsonify([])
        return jsonify([
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": package_name,
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        ])

    @app.get("/")
    def index():
        from datetime import datetime

        from app.models.platform_settings import PlatformSettings
        from app.repositories.plan_repository import PlanRepository

        plans = PlanRepository().list_active_for_landing()
        landing = PlatformSettings.get_or_create().landing_content_or_default
        return render_template(
            "marketing/landing.html", plans=plans, current_year=datetime.now().year, landing=landing,
        )

    def _store_tenant_from_query():
        """Se o link veio do cardápio público de uma loja (ver
        public/store_menu.html, que manda ?loja=<slug>), devolve o
        tenant pra a página legal linkar de volta pro cardápio da loja
        em vez da landing page da Comanda — quem está lendo aqui é o
        cliente final daquela loja, não um lojista em potencial olhando
        o site institucional."""
        from app.models import Tenant

        slug = request.args.get("loja", "").strip()
        return Tenant.query.filter_by(slug=slug).first() if slug else None

    @app.get("/termos-de-uso")
    def terms_of_use():
        from app.models.platform_settings import PlatformSettings

        settings = PlatformSettings.get_or_create()
        updated_at = settings.terms_updated_at.strftime("%d/%m/%Y") if settings.terms_content and settings.terms_updated_at else "24 de julho de 2026"
        return render_template(
            "marketing/terms.html", updated_at=updated_at, content=settings.terms_content_or_default,
            store_tenant=_store_tenant_from_query(),
        )

    @app.get("/politica-de-privacidade")
    def privacy_policy():
        from app.models.platform_settings import PlatformSettings

        settings = PlatformSettings.get_or_create()
        updated_at = settings.privacy_updated_at.strftime("%d/%m/%Y") if settings.privacy_content and settings.privacy_updated_at else "24 de julho de 2026"
        return render_template(
            "marketing/privacy.html", updated_at=updated_at, content=settings.privacy_content_or_default,
            store_tenant=_store_tenant_from_query(),
        )


def _register_request_hooks(app: Flask) -> None:
    from app.utils.session_guard import enforce_session_not_revoked
    from app.utils.tenant_context import load_tenant_from_session

    # enforce_session_not_revoked primeiro: se a sessão foi encerrada,
    # nem faz sentido resolver o tenant dela em seguida.
    app.before_request(enforce_session_not_revoked)
    app.before_request(load_tenant_from_session)

    # Cabeçalhos de segurança em toda resposta — mitigam clickjacking
    # (X-Frame-Options/frame-ancestors), MIME-sniffing (nosniff), e
    # vazamento de URL/query string via header Referer para sites de
    # terceiro (Referrer-Policy). Não usamos uma CSP restritiva (sem
    # 'unsafe-inline') porque o app depende hoje de <script> e atributos
    # inline (onclick/oninput/onchange) espalhados pelos templates —
    # travar isso sem uma migração para nonce/hash quebraria a maior
    # parte da interface. Ainda assim, a CSP abaixo já fecha vetores
    # reais: bloqueia <object>/<embed> de terceiros, restringe de onde
    # scripts/estilos podem carregar (mesma origem) e impede que a
    # própria aplicação seja embutida em <iframe> de outro site.
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            # 'unsafe-eval' é obrigatório aqui: o Alpine.js (usado em toda a
            # interface — sidebar, gavetas, modal de confirmação, toasts de
            # notificação, x-data="{...}" espalhado pelos templates) compila
            # cada expressão (x-show, @click, x-init...) com `new Function()`
            # por baixo dos panos; SEM 'unsafe-eval' o navegador bloqueia
            # essa compilação e o Alpine para de funcionar por completo —
            # foi exatamente isso que quebrou toast/gaveta/modal/toggle de
            # senha na primeira versão desta CSP (faltava esta permissão).
            # Existe uma build do Alpine sem eval (@alpinejs/csp), mas exige
            # reescrever x-data pra métodos nomeados em vez de objetos
            # inline em todos os templates — fora do escopo agora.
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "media-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'self';",
        )
        # HSTS só faz sentido (e só é seguro anunciar) quando a conexão
        # já está garantidamente em HTTPS — em dev/testing (HTTP puro)
        # anunciar isso faria o navegador recusar tentativas futuras em
        # HTTP simples, inclusive localhost.
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template

    from app.extensions import db

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        # Garante que uma transação de banco quebrada não deixe a conexão
        # em estado inconsistente para o próximo request (o mesmo worker
        # do gunicorn é reutilizado entre requisições).
        db.session.rollback()
        app.logger.exception("Erro interno não tratado: %s", error)
        return render_template("errors/500.html"), 500


def _register_context_processors(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        from app.utils.tenant_context import get_current_tenant

        return {"current_tenant": get_current_tenant(), "platform_settings": _get_platform_settings_safely()}


def _get_platform_settings_safely():
    """
    Usado só para a cor de destaque customizável do painel
    administrativo (ver layouts/admin_panel.html e lojista_panel.html).
    Envolvido em try/except porque este context processor roda em TODA
    requisição, inclusive antes de `platform_settings` existir no banco
    (instalação nova, migração pendente) — nesse caso o painel
    simplesmente usa a cor padrão do design system, sem quebrar a página.
    """
    try:
        from app.models.platform_settings import PlatformSettings

        return PlatformSettings.get_or_create()
    except Exception:
        return None


def _register_template_filters(app: Flask) -> None:
    import base64

    @app.template_filter("b64encode")
    def b64encode_filter(value: str) -> str:
        """Usado para montar favicons SVG inline como data URI (ver
        public/store_menu.html) — base64 evita ter que escapar manualmente
        caracteres reservados de URL como o "#" das cores hex do SVG."""
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @app.template_global("lojista_unread_chat_count")
    def lojista_unread_chat_count() -> int:
        """Selo de mensagens não lidas na sidebar do lojista (ver
        layouts/lojista_panel.html) — template_global (não context
        processor) de propósito: só roda quando o próprio template do
        painel do lojista chama, não em toda requisição do app inteiro."""
        from app.models import ChatSenderRole
        from app.services.chat_service import ChatService
        from app.utils.tenant_context import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None:
            return 0
        return ChatService(tenant.id).unread_count(reader_role=ChatSenderRole.LOJISTA)

    @app.template_global("admin_unread_chat_count")
    def admin_unread_chat_count() -> int:
        """Selo de mensagens não lidas na sidebar do Super Admin (ver
        layouts/admin_panel.html) — soma quantas lojas têm pelo menos uma
        mensagem nova, mesmo critério já usado na listagem de lojistas
        (ChatService.tenant_ids_with_unread_messages_for_admin)."""
        from app.services.chat_service import ChatService

        return len(ChatService.tenant_ids_with_unread_messages_for_admin())

    @app.template_global("admin_new_feedback_count")
    def admin_new_feedback_count() -> int:
        """Selo de sugestões/reportes ainda não tratados na sidebar do
        Super Admin (ver layouts/admin_panel.html) — mesma ideia do selo
        de chat não lido, ver admin_unread_chat_count acima."""
        from app.services.feedback_service import FeedbackService

        return FeedbackService.new_count()

    @app.template_global("cheapest_plan_name")
    def cheapest_plan_name() -> str | None:
        """Nome do plano mais barato disponível para contratação — usado
        na barra chamativa de fim de teste (ver layouts/lojista_panel.html)
        pra personalizar a chamada ("Assine o plano X agora") em vez de um
        genérico "assine um plano". None se não houver nenhum plano ativo
        cadastrado ainda."""
        from app.repositories.plan_repository import PlanRepository

        plan = PlanRepository().get_cheapest_active()
        return plan.name if plan else None

    @app.template_global("chat_day_label")
    def chat_day_label(moment) -> str:
        """"Hoje"/"Ontem"/data — usado pelos separadores de dia no chat de
        suporte (estilo WhatsApp), tanto do lado do lojista quanto do
        Super Admin. `moment` é o `created_at` (timezone-aware) de uma
        ChatMessage; comparamos só a data local do servidor."""
        from datetime import date, timedelta

        moment_date = moment.date()
        today = date.today()
        if moment_date == today:
            return "Hoje"
        if moment_date == today - timedelta(days=1):
            return "Ontem"
        return moment_date.strftime("%d/%m/%Y")

    @app.template_global("static_versioned")
    def static_versioned(filename: str) -> str:
        """URL de um arquivo em static/ com `?v=<mtime>` — permite ligar
        cache de longa duração no navegador (ver SEND_FILE_MAX_AGE_DEFAULT
        em ProductionConfig) sem risco de servir uma versão antiga depois
        de um deploy: a URL muda sozinha quando o arquivo muda, então o
        navegador busca de novo em vez de reaproveitar o cache."""
        import os

        from flask import url_for

        absolute_path = os.path.join(app.static_folder, filename)
        try:
            version = int(os.path.getmtime(absolute_path))
        except OSError:
            version = 0
        return f"{url_for('static', filename=filename)}?v={version}"


def _register_cli_commands(app: Flask) -> None:
    from app.cli import register_cli

    register_cli(app)
