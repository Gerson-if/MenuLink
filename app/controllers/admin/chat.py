from flask import flash, redirect, render_template, url_for
from flask_login import current_user

from app.forms.chat_forms import ChatMessageForm
from app.models import ChatSenderRole
from app.services.admin_tenant_service import AdminTenantService
from app.services.chat_service import ChatError, ChatService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/lojistas/<int:tenant_id>/mensagens")
@super_admin_required
def chat_messages_fragment(tenant_id):
    tenant = AdminTenantService().get_or_404(tenant_id)
    service = ChatService(tenant.id)
    service.mark_read(reader_role=ChatSenderRole.SUPER_ADMIN)
    return render_template(
        "admin/tenants/_chat_messages.html", messages=service.list_messages(), current_user_id=current_user.id,
    )


@admin_bp.route("/lojistas/<int:tenant_id>/mensagens/enviar", methods=["POST"])
@super_admin_required
def chat_send(tenant_id):
    tenant = AdminTenantService().get_or_404(tenant_id)
    form = ChatMessageForm()
    if form.validate_on_submit():
        try:
            ChatService(tenant.id).send_message(
                sender_user=current_user, sender_role=ChatSenderRole.SUPER_ADMIN, body=form.body.data,
            )
        except ChatError as exc:
            flash(str(exc), "danger")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id, _anchor="chat"))
