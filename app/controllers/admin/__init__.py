"""
Painel do Super Administrador — Blueprint único (`admin`), rotas
divididas em módulos por responsabilidade (dashboard, lojistas, planos,
cobrança), no mesmo padrão do pacote `lojista`.
"""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.controllers.admin import dashboard  # noqa: E402,F401
from app.controllers.admin import tenants  # noqa: E402,F401
from app.controllers.admin import plans  # noqa: E402,F401
from app.controllers.admin import billing  # noqa: E402,F401
from app.controllers.admin import settings  # noqa: E402,F401
from app.controllers.admin import security  # noqa: E402,F401
from app.controllers.admin import chat  # noqa: E402,F401
