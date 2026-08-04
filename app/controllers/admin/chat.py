from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.forms.chat_forms import ChatMessageForm
from app.models import ChatSenderRole
from app.services.admin_tenant_service import AdminTenantService
from app.services.chat_service import ChatError, ChatService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/suporte")
@admin_bp.route("/suporte/<int:tenant_id>")
@super_admin_required
def support_inbox(tenant_id=None):
    """Central de Suporte — seção exclusiva para conversar com os
    lojistas, separada da tela de gestão da loja (plano, cobrança,
    status etc. ficam em admin.tenants_detail). Só marca as mensagens
    como lidas quando uma conversa é de fato aberta aqui — diferente do
    comportamento antigo, que marcava como lida s empre que o admin
    abria a página de gestão da loja por qualquer outro motivo,
    fazendo mensagens "sumirem" sem ninguém ter realmente lido."""
    conversations = ChatService.list_conversations_for_admin()

    active_tenant = None
    chat_messages = []
    chat_form = None
    if tenant_id is not None:
        active_tenant = AdminTenantService().get_or_404(tenant_id)
        chat_service = ChatService(active_tenant.id)
        chat_service.mark_read(reader_role=ChatSenderRole.SUPER_ADMIN)
        chat_messages = chat_service.list_messages()
        chat_form = ChatMessageForm()

    return render_template(
        "admin/support/index.html",
        conversations=conversations,
        active_tenant=active_tenant,
        # A partial reaproveitada (admin/tenants/_chat_messages.html, a
        # mesma usada pelo polling HTMX) espera a variável `messages` —
        # esse é o 4º bug de chat achado nesta rodada (os outros três:
        # mark_read disparando em qualquer GET de tenants_detail, o
        # fragmento de polling não recebendo `tenant`, e este aqui). O
        # nome errado (`chat_messages`) já existia no código antigo, que
        # nunca de fato mostrava o histórico de mensagens no painel do
        # admin — só passava despercebido porque a maioria das
        # conversas de teste tinha poucas mensagens e ninguém reparou
        # que a lista vinha sempre vazia.
        messages=chat_messages,
        chat_form=chat_form,
    )


@admin_bp.route("/lojistas/<int:tenant_id>/mensagens")
@super_admin_required
def chat_messages_fragment(tenant_id):
    """BUG CORRIGIDO: esta é a rota que o hx-trigger="every 5s" do painel
    do admin chama a cada 5 segundos para atualizar a conversa sozinha.
    O template (_chat_messages.html) usa `tenant.name` e `tenant.id`
    (este último dentro do próprio hx-get de auto-atualização) — mas
    `tenant` nunca era passado pro render_template aqui, só existia como
    variável local da função. Cada poll de 5s quebrava silenciosamente
    (erro do Jinja/url_for ao montar a URL com um tenant_id inválido),
    então o chat do admin NUNCA se atualizava sozinho — só ao dar F5 na
    página inteira. Isso explica por que "as mensagens não chegavam para
    o Super Admin": elas chegavam no banco, só não apareciam na tela sem
    um reload manual completo."""
    tenant = AdminTenantService().get_or_404(tenant_id)
    service = ChatService(tenant.id)
    service.mark_read(reader_role=ChatSenderRole.SUPER_ADMIN)
    return render_template(
        "admin/tenants/_chat_messages.html", messages=service.list_messages(), current_user_id=current_user.id,
        tenant=tenant,
    )


@admin_bp.route("/lojistas/<int:tenant_id>/mensagens/enviar", methods=["POST"])
@super_admin_required
def chat_send(tenant_id):
    """Mesma lógica de progressive enhancement do lado lojista (ver
    app/controllers/lojista/chat.py:chat_send) — com htmx (`HX-Request`
    no header), devolve o fragmento atualizado em vez de redirecionar,
    pra evitar a navegação de página inteira que "arrancava" o Super
    Admin do lugar onde estava."""
    tenant = AdminTenantService().get_or_404(tenant_id)
    form = ChatMessageForm()
    is_htmx = bool(request.headers.get("HX-Request"))
    send_error = None
    if form.validate_on_submit():
        try:
            ChatService(tenant.id).send_message(
                sender_user=current_user, sender_role=ChatSenderRole.SUPER_ADMIN,
                body=form.body.data, attachment=request.files.get("attachment"),
            )
        except ChatError as exc:
            send_error = str(exc)
            if not is_htmx:
                flash(send_error, "danger")
    else:
        errors = [error for field_errors in form.errors.values() for error in field_errors]
        send_error = " ".join(errors) or None
        if not is_htmx:
            for error in errors:
                flash(error, "danger")

    if is_htmx:
        service = ChatService(tenant.id)
        service.mark_read(reader_role=ChatSenderRole.SUPER_ADMIN)
        return render_template(
            "admin/tenants/_chat_messages.html", messages=service.list_messages(),
            current_user_id=current_user.id, tenant=tenant, send_error=send_error,
        )
    return redirect(url_for("admin.support_inbox", tenant_id=tenant.id))
