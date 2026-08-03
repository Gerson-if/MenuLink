"""
Chat de suporte entre lojista e Super Admin — um fio por tenant. Ver
app/services/chat_service.py, app/controllers/lojista/chat.py e
app/controllers/admin/chat.py.
"""

import re

from app.extensions import db
from app.models import ChatMessage, ChatSenderRole, Tenant, TenantStatus, UserRole
from app.services.chat_service import ChatError, ChatService


def _extract_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


def _post_with_csrf(client, url, data, referer_url):
    page = client.get(referer_url)
    csrf = _extract_csrf(page.get_data(as_text=True))
    if csrf:
        data = {**data, "csrf_token": csrf}
    return client.post(url, data=data, follow_redirects=True)


class TestChatServiceCore:
    def test_send_and_list_messages(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            service.send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Oi, preciso de ajuda")

            messages = service.list_messages()
            assert len(messages) == 1
            assert messages[0].body == "Oi, preciso de ajuda"
            assert messages[0].sender_role == ChatSenderRole.LOJISTA

    def test_rejects_blank_message(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            try:
                service.send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="   ")
                assert False, "deveria ter levantado ChatError"
            except ChatError:
                pass

    def test_unread_count_and_mark_read(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            admin = User.query.filter_by(email="admin@cardapio.saas").first()
            service = ChatService(tenant.id)
            service.send_message(sender_user=admin, sender_role=ChatSenderRole.SUPER_ADMIN, body="Tudo bem?")

            assert service.unread_count(reader_role=ChatSenderRole.LOJISTA) == 1
            assert service.unread_count(reader_role=ChatSenderRole.SUPER_ADMIN) == 0

            service.mark_read(reader_role=ChatSenderRole.LOJISTA)
            assert service.unread_count(reader_role=ChatSenderRole.LOJISTA) == 0

    def test_tenant_ids_with_unread_for_admin(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            ChatService(tenant.id).send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Socorro")

            unread = ChatService.tenant_ids_with_unread_messages_for_admin()
            assert tenant.id in unread


class TestLojistaChatRoutes:
    def test_chat_page_renders(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/suporte")
        assert resp.status_code == 200
        assert "Suporte Menu Link" in resp.get_data(as_text=True)

    def test_lojista_can_send_message(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        _post_with_csrf(
            client, "/painel/suporte/enviar", {"body": "Minha impressora não funciona"}, "/painel/suporte",
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            messages = ChatService(tenant.id).list_messages()
            assert any("impressora" in m.body for m in messages)

    def test_visiting_chat_page_marks_admin_messages_as_read(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            admin = User.query.filter_by(email="admin@cardapio.saas").first()
            ChatService(tenant.id).send_message(sender_user=admin, sender_role=ChatSenderRole.SUPER_ADMIN, body="Oi!")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.get("/painel/suporte")

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert ChatService(tenant.id).unread_count(reader_role=ChatSenderRole.LOJISTA) == 0


class TestAdminChatRoutes:
    def test_tenant_detail_links_to_support_inbox_instead_of_embedding_chat(self, app, client, login_as):
        """A conversa agora mora na Central de Suporte (admin.support_inbox),
        separada da gestão da loja — ver app/controllers/admin/chat.py."""
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get(f"/admin/lojistas/{tenant_id}")
        html = resp.get_data(as_text=True)
        assert "Conversa com" not in html
        assert f"/admin/suporte/{tenant_id}" in html

    def test_viewing_tenant_detail_does_not_mark_chat_as_read(self, app, client, login_as):
        """Bug corrigido: antes, só abrir a página de gestão da loja (pra
        mudar o plano, por exemplo) já marcava a conversa como lida sem o
        admin nunca ter aberto o chat de verdade — fazendo mensagens
        novas "sumirem" do indicador sem ninguém ter lido."""
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            ChatService(tenant.id).send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Oi, preciso de ajuda")
            tenant_id = tenant.id

        login_as(client, "admin@cardapio.saas", "admin123")
        client.get(f"/admin/lojistas/{tenant_id}")  # visita a página de gestão da loja, não o chat

        with app.app_context():
            assert tenant_id in ChatService.tenant_ids_with_unread_messages_for_admin()

    def test_support_inbox_lists_conversation_and_marks_read(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            ChatService(tenant.id).send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Oi, preciso de ajuda")
            tenant_id = tenant.id

        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/suporte")
        assert "Braseiro" in resp.get_data(as_text=True)

        resp = client.get(f"/admin/suporte/{tenant_id}")
        html = resp.get_data(as_text=True)
        assert "Conversa com" in html
        assert "Oi, preciso de ajuda" in html

        with app.app_context():
            assert tenant_id not in ChatService.tenant_ids_with_unread_messages_for_admin()

    def test_support_inbox_lists_every_tenant_as_a_contact_even_without_messages(self, app, client, login_as):
        """A Central de Suporte funciona como uma lista de contatos: o
        Super Admin precisa conseguir puxar assunto com qualquer lojista,
        não só responder quem já escreveu primeiro — ver
        ChatService.list_conversations_for_admin."""
        with app.app_context():
            from app.models import Plan, User

            plan = Plan.query.first()
            tenant = Tenant(
                name="Loja Sem Conversa", slug="loja-sem-conversa", email="semconversa@x.com",
                plan_id=plan.id, status=TenantStatus.ACTIVE, delivery_enabled=True,
            )
            db.session.add(tenant)
            db.session.flush()
            owner = User(name="Dono Sem Conversa", email="dono-sem-conversa@x.com", role=UserRole.OWNER, tenant_id=tenant.id)
            owner.set_password("senha123")
            db.session.add(owner)
            db.session.commit()
            tenant_id = tenant.id

        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/suporte")
        html = resp.get_data(as_text=True)
        assert "Loja Sem Conversa" in html
        assert "Nenhuma mensagem ainda" in html

        # entra na conversa vazia e manda a primeira mensagem, como um
        # contato novo — sem que o lojista precise escrever primeiro
        _post_with_csrf(
            client, f"/admin/lojistas/{tenant_id}/mensagens/enviar", {"body": "Oi! Qualquer coisa é só chamar."},
            f"/admin/suporte/{tenant_id}",
        )
        with app.app_context():
            messages = ChatService(tenant_id).list_messages()
            assert any("Qualquer coisa" in m.body for m in messages)

    def test_admin_can_reply(self, app, client, login_as):
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")
        _post_with_csrf(
            client, f"/admin/lojistas/{tenant_id}/mensagens/enviar", {"body": "Já vou verificar"},
            f"/admin/suporte/{tenant_id}",
        )
        with app.app_context():
            messages = ChatService(tenant_id).list_messages()
            assert any("verificar" in m.body for m in messages)

    def test_admin_chat_messages_fragment_does_not_error(self, app, client, login_as):
        """Bug corrigido: o fragmento chamado pelo polling HTMX do admin
        (hx-trigger="every 5s") não passava `tenant` pro template, que usa
        tenant.name/tenant.id — cada poll de 5s quebrava silenciosamente e
        o chat do admin nunca se atualizava sozinho sem um F5 manual."""
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get(f"/admin/lojistas/{tenant_id}/mensagens")
        assert resp.status_code == 200

    def test_tenants_list_flags_unread_messages(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            ChatService(tenant.id).send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Preciso de ajuda urgente")

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/lojistas")
        html = resp.get_data(as_text=True)
        assert "Mensagem nova" in html

    def test_lojista_cannot_access_admin_chat_route(self, app, client, login_as):
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get(f"/admin/lojistas/{tenant_id}/mensagens")
        assert resp.status_code == 403
