"""
Aplica a revogação de sessão (ver SessionService/UserSession) a cada
request autenticado — registrado como before_request, ANTES de
load_tenant_from_session (ver app/__init__.py._register_request_hooks),
pra nem chegar a resolver o tenant de uma sessão que já foi encerrada.

Não afeta quem nunca teve uma UserSession rastreada (ex: sessão de
login antiga, de antes desta funcionalidade existir) — `get_current_session`
retorna None e o request segue normal; só é barrado quem tem uma
UserSession explicitamente revogada.
"""

from flask import flash, redirect, url_for
from flask_login import current_user, logout_user


def enforce_session_not_revoked():
    if not current_user.is_authenticated:
        return None

    from app.services.session_service import SessionService

    service = SessionService()
    if service.current_session_is_revoked():
        logout_user()
        flash("Esta sessão foi encerrada (por você, em outro dispositivo, ou por segurança). Faça login novamente.", "info")
        return redirect(url_for("auth.login"))

    service.touch_current_session()
    return None
