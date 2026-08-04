"""
Chat de suporte entre lojista e Super Admin — um fio por tenant. Ver
app/services/chat_service.py, app/controllers/lojista/chat.py e
app/controllers/admin/chat.py.
"""

import io
import os
import re

from PIL import Image
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import ChatMessage, ChatSenderRole, Tenant, TenantStatus, UserRole
from app.services.chat_service import ChatError, ChatService


def _make_image_file_storage(filename="comprovante.png") -> FileStorage:
    buf = io.BytesIO()
    Image.new("RGB", (60, 60), color=(20, 120, 200)).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=filename, content_type="image/png")


def _absolute_upload_path(app, relative_path: str) -> str:
    """`relative_path` (ex: "uploads/tenant_1/chat/xyz.webp") é relativo a
    UPLOAD_FOLDER, cujo valor JÁ representa a pasta "uploads" em si (ver
    app/utils/uploads.py) — não usa `app.static_folder` porque em
    TestingConfig UPLOAD_FOLDER aponta pra um diretório temporário
    separado de static/ (ver app/config.py)."""
    remainder = relative_path.split("/", 1)[1]  # remove o prefixo "uploads/"
    return os.path.join(app.config["UPLOAD_FOLDER"], *remainder.split("/"))


def _make_pdf_file_storage(filename="comprovante.pdf") -> FileStorage:
    # PDF mínimo só pra bater na checagem de assinatura mágica (%PDF) — o
    # conteúdo em si não precisa ser um PDF "de verdade" pro propósito
    # deste teste, só os primeiros bytes (ver save_chat_attachment).
    content = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    return FileStorage(stream=io.BytesIO(content), filename=filename, content_type="application/pdf")


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

    def test_send_message_with_only_attachment_succeeds(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            message = service.send_message(
                sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="",
                attachment=_make_image_file_storage(),
            )
            assert message.body == ""
            assert message.attachment_path is not None
            assert message.has_attachment is True

    def test_send_message_without_body_or_attachment_raises_chat_error_in_portuguese(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            try:
                service.send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="", attachment=None)
                assert False, "deveria ter levantado ChatError"
            except ChatError as exc:
                assert "anexe" in str(exc).lower() or "escreva" in str(exc).lower()

    def test_oversized_attachment_is_rejected(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            huge = FileStorage(
                stream=io.BytesIO(b"0" * (9 * 1024 * 1024)), filename="grande.png", content_type="image/png",
            )
            try:
                service.send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="", attachment=huge)
                assert False, "deveria ter levantado ChatError"
            except ChatError as exc:
                assert "grande" in str(exc).lower()

    def test_wrong_type_attachment_is_rejected_with_clear_message(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            bogus = FileStorage(
                stream=io.BytesIO(b"conteudo qualquer"), filename="planilha.txt", content_type="text/plain",
            )
            try:
                service.send_message(sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="", attachment=bogus)
                assert False, "deveria ter levantado ChatError"
            except ChatError as exc:
                assert "não suportado" in str(exc).lower()

    def test_image_attachment_is_stored_under_tenant_chat_folder(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            message = service.send_message(
                sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="Segue o comprovante",
                attachment=_make_image_file_storage(),
            )
            assert message.attachment_mimetype == "image/webp"
            assert message.attachment_path.startswith(f"uploads/tenant_{tenant.id}/chat/")
            assert os.path.isfile(_absolute_upload_path(app, message.attachment_path))

    def test_pdf_attachment_is_accepted(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            from app.models import User

            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            service = ChatService(tenant.id)
            message = service.send_message(
                sender_user=owner, sender_role=ChatSenderRole.LOJISTA, body="",
                attachment=_make_pdf_file_storage(),
            )
            assert message.attachment_mimetype == "application/pdf"
            assert message.attachment_path.endswith(".pdf")
            assert os.path.isfile(_absolute_upload_path(app, message.attachment_path))

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

    def test_htmx_send_returns_fragment_instead_of_redirect(self, app, client, login_as):
        """Fix do "fica voltando pra cima" no celular: com o header
        HX-Request (mandado pelo htmx em toda requisição que ele faz), o
        envio devolve direto o fragmento atualizado (200) em vez de um
        redirect (302) — sem isso o htmx nunca conseguiria trocar
        #chat-messages no lugar, e o app cairia de volta no
        comportamento de navegação de página inteira."""
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/suporte/enviar", data={"body": "Mensagem mandada via htmx"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Mensagem mandada via htmx" in html
        assert 'id="chat-messages"' in html

    def test_non_htmx_send_still_redirects(self, app, client, login_as):
        """Fallback sem JS/htmx continua funcionando como antes — sem o
        header HX-Request, o servidor não sabe que quem chamou consegue
        lidar com um fragmento parcial, então mantém o
        POST+redirect+GET tradicional."""
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        page = client.get("/painel/suporte")
        csrf = _extract_csrf(page.get_data(as_text=True))
        data = {"body": "Mensagem sem htmx"}
        if csrf:
            data["csrf_token"] = csrf
        resp = client.post("/painel/suporte/enviar", data=data, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/painel/suporte")

    def test_send_message_with_attachment_via_route(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        img_buf = io.BytesIO()
        Image.new("RGB", (40, 40), color=(200, 100, 10)).save(img_buf, format="PNG")
        img_buf.seek(0)
        resp = client.post(
            "/painel/suporte/enviar",
            data={"body": "", "attachment": (img_buf, "comprovante.png")},
            content_type="multipart/form-data",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "chat-attachment-thumb" in html
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            messages = ChatService(tenant.id).list_messages()
            assert any(m.has_attachment for m in messages)


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

    def test_admin_htmx_send_returns_fragment_instead_of_redirect(self, app, client, login_as):
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/mensagens/enviar", data={"body": "Resposta via htmx"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Resposta via htmx" in html
        assert 'id="chat-messages"' in html

    def test_admin_non_htmx_send_still_redirects(self, app, client, login_as):
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")
        page = client.get(f"/admin/suporte/{tenant_id}")
        csrf = _extract_csrf(page.get_data(as_text=True))
        data = {"body": "Resposta sem htmx"}
        if csrf:
            data["csrf_token"] = csrf
        resp = client.post(f"/admin/lojistas/{tenant_id}/mensagens/enviar", data=data, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/admin/suporte/{tenant_id}")

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
