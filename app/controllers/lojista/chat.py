from flask import flash, redirect, render_template, url_for
from flask_login import current_user

from app.forms.chat_forms import ChatMessageForm
from app.models import ChatSenderRole
from app.services.chat_service import ChatError, ChatService
from app.utils.decorators import lojista_login_required
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp

# Usa lojista_login_required (não lojista_required) DE PROPÓSITO: uma
# loja BLOCKED_PAYMENT (inadimplente ou com o período de teste + tolerância
# esgotados — ver app/services/trial_service.py) é redirecionada pra
# "conta bloqueada" no resto do painel, mas precisa continuar falando
# com o suporte pra regularizar a situação; só SUSPENDED/CANCELED
# (decisão manual do Super Admin) continuam sem acesso a nada.


@lojista_bp.route("/suporte")
@lojista_login_required
def chat():
    tenant = get_current_tenant()
    service = ChatService(tenant.id)
    service.mark_read(reader_role=ChatSenderRole.LOJISTA)
    return render_template(
        "lojista/chat.html", messages=service.list_messages(), form=ChatMessageForm(), current_user_id=current_user.id,
    )


@lojista_bp.route("/suporte/mensagens")
@lojista_login_required
def chat_messages_fragment():
    """Fragmento reaproveitado tanto pelo polling HTMX (hx-trigger="every
    5s") quanto pelo próprio envio — mantém a lista sempre atualizada e
    marca como lida qualquer mensagem nova do Super Admin assim que
    aparece na tela."""
    tenant = get_current_tenant()
    service = ChatService(tenant.id)
    service.mark_read(reader_role=ChatSenderRole.LOJISTA)
    return render_template(
        "lojista/_chat_messages.html", messages=service.list_messages(), current_user_id=current_user.id,
    )


@lojista_bp.route("/suporte/enviar", methods=["POST"])
@lojista_login_required
def chat_send():
    tenant = get_current_tenant()
    form = ChatMessageForm()
    if form.validate_on_submit():
        try:
            ChatService(tenant.id).send_message(
                sender_user=current_user, sender_role=ChatSenderRole.LOJISTA, body=form.body.data,
            )
        except ChatError as exc:
            flash(str(exc), "danger")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
    return redirect(url_for("lojista.chat"))
