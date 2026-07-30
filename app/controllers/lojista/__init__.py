"""
Painel do Lojista — Blueprint único (`lojista`), rotas divididas em
módulos por responsabilidade (dashboard, categorias, produtos, imagens)
para manter cada arquivo pequeno e focado.

Todas as rotas aqui são protegidas por @lojista_required (login +
conta ativa) e operam sempre sobre `get_current_tenant()` — nunca sobre
um tenant_id vindo de parâmetro de URL ou de formulário, para que seja
impossível um lojista manipular dado de outro tenant trocando um ID no
request.
"""

from flask import Blueprint

lojista_bp = Blueprint("lojista", __name__, url_prefix="/painel")

# Importados por último, de propósito: cada submódulo faz
# `from app.controllers.lojista import lojista_bp` e registra suas rotas
# via decorator. Se importássemos no topo, teríamos import circular.
from app.controllers.lojista import dashboard  # noqa: E402,F401
from app.controllers.lojista import categories  # noqa: E402,F401
from app.controllers.lojista import products  # noqa: E402,F401
from app.controllers.lojista import images  # noqa: E402,F401
from app.controllers.lojista import orders  # noqa: E402,F401
from app.controllers.lojista import customers  # noqa: E402,F401
from app.controllers.lojista import reports  # noqa: E402,F401
from app.controllers.lojista import banners  # noqa: E402,F401
from app.controllers.lojista import settings  # noqa: E402,F401
from app.controllers.lojista import complements  # noqa: E402,F401
from app.controllers.lojista import security  # noqa: E402,F401
from app.controllers.lojista import chat  # noqa: E402,F401
from app.controllers.lojista import feedback  # noqa: E402,F401
