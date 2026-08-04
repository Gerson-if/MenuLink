"""
Autenticação — login único por e-mail/senha, auto-cadastro de lojista e
recuperação de senha.
"""

from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, url_for, request
from flask_login import current_user, login_user, logout_user, login_required

from app.extensions import limiter
from app.forms.auth_forms import LoginForm
from app.forms.registration_forms import RegistrationForm
from app.forms.password_reset_forms import ForgotPasswordForm, ResetPasswordForm
from app.models import TenantStatus
from app.services.auth_service import AuthService, AuthError
from app.services.admin_tenant_service import AdminTenantService, TenantAdminError
from app.services.session_service import SessionService
from app.repositories.user_repository import UserRepository
from app.utils.tokens import generate_reset_token, verify_reset_token
from app.utils.mailer import send_password_reset_email

auth_bp = Blueprint("auth", __name__)


def _is_safe_redirect_target(target: str | None) -> bool:
    """`?next=` só é seguro se apontar pra um caminho relativo dentro do
    próprio site — sem isso, um link tipo /login?next=https://golpe.com
    (ou o equivalente "//golpe.com", que os navegadores tratam como
    absoluto também) usaria o domínio real do Menu Link pra redirecionar,
    depois do login de verdade, pra um site de phishing."""
    if not target:
        return False
    if not target.startswith("/") or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _redirect_url_for_user(user) -> str:
    if user.is_super_admin:
        return url_for("admin.dashboard")
    if user.tenant and user.tenant.status == TenantStatus.BLOCKED_PAYMENT:
        return url_for("lojista.billing_lockout")
    return url_for("lojista.dashboard")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])  # mitiga força bruta
def login():
    if current_user.is_authenticated:
        return redirect(_redirect_url_for_user(current_user))

    form = LoginForm()

    if form.validate_on_submit():
        try:
            user = AuthService().authenticate(form.email.data, form.password.data)
        except AuthError as exc:
            flash(str(exc), "danger")
        else:
            login_user(user, remember=form.remember_me.data)
            SessionService().start_session(user)
            flash(f"Bem-vindo(a), {user.name}!", "success")
            next_url = request.args.get("next")
            if _is_safe_redirect_target(next_url):
                return redirect(next_url)
            return redirect(_redirect_url_for_user(user))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    SessionService().revoke_current_session()
    logout_user()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/cadastro", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_redirect_url_for_user(current_user))

    form = RegistrationForm()

    if form.validate_on_submit():
        service = AdminTenantService()
        try:
            tenant = service.create(
                name=form.store_name.data,
                email=form.email.data,
                phone=None,
                whatsapp_number=form.whatsapp_number.data,
                plan_id=None,
                owner_name=form.owner_name.data,
                owner_email=form.email.data,
                owner_password=form.password.data,
                slug=form.slug.data,
            )
        except TenantAdminError as exc:
            # Erro de regra de negócio (e-mail já usado por outra loja
            # ou por outro login) não é uma validação de campo do
            # WTForms — mas do ponto de vista de quem preenche o
            # formulário, é claramente um problema do campo "e-mail".
            # Anexado aqui em form.email.errors (além do flash de
            # sempre) pra aparecer no lugar certo: tanto no formulário
            # tradicional (sem JS) quanto no assistente em passos
            # (register.html), que decide em qual passo abrir
            # justamente procurando erro de campo — sem isso, o
            # assistente reabriria sempre no passo 1, escondendo o
            # campo com o problema real atrás de mais 4 cliques.
            form.email.errors.append(str(exc))
            flash(str(exc), "danger")
        else:
            # Login automático — o lojista cai direto no painel, sem
            # precisar logar de novo logo após se cadastrar.
            owner = UserRepository().get_by_email(form.email.data)
            login_user(owner)
            SessionService().start_session(owner)
            flash(f"Loja '{tenant.name}' criada com sucesso! Bem-vindo(a) ao Menu Link.", "success")
            return redirect(url_for("lojista.dashboard"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/cadastro/verificar-slug")
def check_slug_availability():
    """Endpoint leve para checagem ao vivo (HTMX) de disponibilidade do
    slug durante o auto-cadastro."""
    import re

    from app.repositories.tenant_repository import TenantRepository

    slug = (request.args.get("slug") or "").strip().lower()
    valid_format = bool(re.fullmatch(r"[a-z0-9-]{3,150}", slug))

    if not valid_format:
        return '<p class="upload-error mt-1">Use apenas letras minúsculas, números e hífens (mínimo 3 caracteres).</p>'

    taken = TenantRepository().get_by_slug(slug) is not None
    if taken:
        return '<p class="upload-error mt-1">Esse endereço já está em uso. Tente outro.</p>'

    return '<p class="upload-meta mt-1" style="color: var(--sage);"><i class="bi bi-check-circle"></i> Endereço disponível.</p>'


@auth_bp.route("/recuperar-senha", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    sent = False

    if form.validate_on_submit():
        user = UserRepository().get_by_email(form.email.data)
        # Sempre mostra a mesma confirmação, exista ou não o e-mail —
        # evita que alguém descubra quais e-mails têm conta só tentando
        # recuperar senha (enumeração de usuários).
        if user is not None and user.is_active:
            token = generate_reset_token(user)
            send_password_reset_email(user, token)
        sent = True

    return render_template("auth/forgot_password.html", form=form, sent=sent)


@auth_bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = verify_reset_token(token)
    if user is None:
        flash("Este link de recuperação é inválido ou expirou. Solicite um novo.", "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        from app.extensions import db

        user.set_password(form.password.data)
        db.session.commit()
        flash("Senha redefinida com sucesso! Faça login com sua nova senha.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, token=token)
