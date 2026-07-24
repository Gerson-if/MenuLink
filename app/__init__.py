"""
Application factory.

Uso:
    from app import create_app
    app = create_app("development")
"""

import os

from flask import Flask, render_template

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(lojista_bp)
    app.register_blueprint(public_bp)

    from app.controllers.webhooks_controller import webhooks_bp

    app.register_blueprint(webhooks_bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

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


def _register_request_hooks(app: Flask) -> None:
    from app.utils.tenant_context import load_tenant_from_session

    app.before_request(load_tenant_from_session)


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
