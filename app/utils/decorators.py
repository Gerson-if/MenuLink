"""
Decorators de autorização, usados nos controllers.

`login_required` (do Flask-Login) garante apenas que existe alguém
logado. Estes decorators aqui adicionam a checagem de PAPEL e, no caso do
lojista, de STATUS DA CONTA (uma conta bloqueada por inadimplência ou
suspensa não pode acessar o painel mesmo estando com a senha correta).
"""

from functools import wraps

from flask import abort, redirect, url_for
from flask_login import current_user, login_required

from app.models import TenantStatus


def super_admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_super_admin:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def lojista_required(view_func):
    """Exige um usuário lojista (owner/staff) com uma conta em status que
    permita acesso ao painel completo (trial ou active).

    Contas suspensas ou canceladas são barradas com 403 — situações que
    só o Super Admin resolve, não fazem sentido oferecer autoatendimento.
    Contas bloqueadas por inadimplência (BLOCKED_PAYMENT) são REDIRECIONADAS
    — não barradas — para `lojista.billing_lockout`, a única página que
    esse status pode acessar (ver `lojista_login_required` abaixo): o
    lojista consegue ver o que deve e pagar direto por ali, sem precisar
    de suporte, mas não acessa o resto do painel enquanto não regularizar."""

    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.is_super_admin:
            abort(403)

        tenant = current_user.tenant
        if tenant is None or tenant.status in (TenantStatus.SUSPENDED, TenantStatus.CANCELED):
            abort(403, description="Conta suspensa ou cancelada. Fale com o suporte.")
        if tenant.status == TenantStatus.BLOCKED_PAYMENT:
            return redirect(url_for("lojista.billing_lockout"))

        return view_func(*args, **kwargs)

    return wrapped


def lojista_login_required(view_func):
    """Como `lojista_required`, mas SEM redirecionar contas
    BLOCKED_PAYMENT — uso exclusivo da própria página de regularização
    de pendências (`lojista.billing_lockout`) e das ações de pagamento
    nela. Nenhuma outra rota do painel deve usar este decorator."""

    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.is_super_admin:
            abort(403)

        tenant = current_user.tenant
        if tenant is None or tenant.status in (TenantStatus.SUSPENDED, TenantStatus.CANCELED):
            abort(403, description="Conta suspensa ou cancelada. Fale com o suporte.")

        return view_func(*args, **kwargs)

    return wrapped
