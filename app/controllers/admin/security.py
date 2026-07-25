from flask import flash, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import limiter
from app.forms.security_forms import ChangePasswordForm, MyProfileForm
from app.services.auth_service import AuthError, AuthService
from app.services.session_service import SessionService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/seguranca")
@super_admin_required
def settings_security():
    password_form = ChangePasswordForm()
    profile_form = MyProfileForm(obj=current_user)
    sessions = SessionService().list_active_sessions(current_user.id)
    current_session = SessionService().get_current_session()
    return render_template(
        "admin/settings/security.html",
        password_form=password_form, profile_form=profile_form,
        sessions=sessions, current_session_id=current_session.id if current_session else None,
    )


@admin_bp.route("/seguranca/senha", methods=["POST"])
@super_admin_required
@limiter.limit("10 per minute")  # mitiga força bruta na senha atual
def settings_security_change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            AuthService().change_password(
                current_user, current_password=form.current_password.data, new_password=form.new_password.data,
            )
        except AuthError as exc:
            flash(str(exc), "danger")
        else:
            flash("Senha alterada com sucesso.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
    return redirect(url_for("admin.settings_security"))


@admin_bp.route("/seguranca/perfil", methods=["POST"])
@super_admin_required
def settings_security_update_profile():
    form = MyProfileForm()
    if form.validate_on_submit():
        try:
            AuthService().update_own_profile(current_user, name=form.name.data, email=form.email.data)
        except AuthError as exc:
            flash(str(exc), "danger")
        else:
            flash("Dados atualizados com sucesso.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
    return redirect(url_for("admin.settings_security"))


@admin_bp.route("/seguranca/sessoes/<int:session_id>/encerrar", methods=["POST"])
@super_admin_required
def settings_security_revoke_session(session_id):
    service = SessionService()
    current_session = service.get_current_session()
    service.revoke_session(session_id, user_id=current_user.id)

    if current_session and current_session.id == session_id:
        from app.controllers.auth_controller import logout

        return logout()

    flash("Sessão encerrada.", "success")
    return redirect(url_for("admin.settings_security"))


@admin_bp.route("/seguranca/sessoes/encerrar-outras", methods=["POST"])
@super_admin_required
def settings_security_revoke_other_sessions():
    count = SessionService().revoke_all_other_sessions(current_user.id)
    flash(f"{count} outra(s) sessão(ões) encerrada(s).", "success")
    return redirect(url_for("admin.settings_security"))
