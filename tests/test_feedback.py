"""
Sugestões e reportes de erro — ver app/models/feedback.py,
app/services/feedback_service.py e os três controllers envolvidos
(público, lojista e admin).
"""

import re

from app.extensions import db
from app.models import Feedback, FeedbackStatus, Tenant, User
from app.services.feedback_service import FeedbackError, FeedbackService


def _extract_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


def _post_with_csrf(client, url, data, referer_url=None):
    page = client.get(referer_url or url)
    csrf = _extract_csrf(page.get_data(as_text=True))
    if csrf:
        data = {**data, "csrf_token": csrf}
    return client.post(url, data=data, follow_redirects=True)


class TestFeedbackServiceCore:
    def test_submit_suggestion_anonymous(self, app):
        with app.app_context():
            feedback = FeedbackService().submit(
                kind="suggestion", message="Seria legal ter modo escuro.", name="Visitante", email="v@example.com",
            )
            assert feedback.id is not None
            assert feedback.tenant_id is None
            assert feedback.submitted_by_user_id is None
            assert feedback.status == FeedbackStatus.NEW

    def test_submit_bug_report_from_lojista(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            feedback = FeedbackService().submit(
                kind="bug_report", message="O botão de salvar não responde.", tenant_id=tenant.id, submitted_by_user=owner,
            )
            assert feedback.tenant_id == tenant.id
            assert feedback.submitted_by_user_id == owner.id

    def test_rejects_blank_message(self, app):
        with app.app_context():
            try:
                FeedbackService().submit(kind="suggestion", message="   ")
                assert False, "deveria ter levantado FeedbackError"
            except FeedbackError:
                pass

    def test_rejects_invalid_kind(self, app):
        with app.app_context():
            try:
                FeedbackService().submit(kind="invalido", message="Mensagem válida")
                assert False, "deveria ter levantado FeedbackError"
            except FeedbackError:
                pass

    def test_update_status_and_notes(self, app):
        with app.app_context():
            feedback = FeedbackService().submit(kind="suggestion", message="Ideia legal")
            FeedbackService().update_status(feedback, status="resolved", admin_notes="Feito na v2")
            db.session.refresh(feedback)
            assert feedback.status == FeedbackStatus.RESOLVED
            assert feedback.admin_notes == "Feito na v2"

    def test_new_count(self, app):
        with app.app_context():
            FeedbackService().submit(kind="suggestion", message="Um")
            f2 = FeedbackService().submit(kind="bug_report", message="Dois")
            assert FeedbackService.new_count() == 2
            FeedbackService().update_status(f2, status="archived")
            assert FeedbackService.new_count() == 1


class TestPublicFeedbackRoute:
    def test_submit_via_landing_form(self, app, client):
        resp = _post_with_csrf(
            client, "/sugestoes",
            {"kind": "suggestion", "name": "Cliente Curioso", "email": "curioso@example.com", "message": "Adoraria ver relatórios em PDF."},
        )
        assert resp.status_code == 200
        assert "Recebemos sua mensagem".encode() in resp.data or "obrigado".encode() in resp.data

        with app.app_context():
            saved = Feedback.query.filter_by(message="Adoraria ver relatórios em PDF.").first()
            assert saved is not None
            assert saved.tenant_id is None
            assert saved.name == "Cliente Curioso"

    def test_blank_message_shows_validation_error(self, app, client):
        resp = _post_with_csrf(client, "/sugestoes", {"kind": "suggestion", "message": ""})
        assert resp.status_code == 200
        with app.app_context():
            assert Feedback.query.count() == 0


class TestLojistaFeedbackRoute:
    def test_lojista_can_submit_and_see_history(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = _post_with_csrf(
            client, "/painel/sugestoes", {"kind": "bug_report", "message": "O upload de imagem falhou hoje."},
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            saved = Feedback.query.filter_by(message="O upload de imagem falhou hoje.").first()
            assert saved is not None
            assert saved.tenant_id == tenant.id
            assert saved.submitted_by_user_id is not None

        # A própria mensagem enviada aparece no histórico da mesma tela.
        history_page = client.get("/painel/sugestoes")
        assert "O upload de imagem falhou hoje.".encode() in history_page.data

    def test_requires_login(self, client):
        resp = client.get("/painel/sugestoes", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)


class TestAdminFeedbackRoute:
    def test_admin_sees_list_and_can_update_status(self, app, client, login_as):
        with app.app_context():
            feedback = FeedbackService().submit(kind="suggestion", message="Painel poderia ter atalhos de teclado.")
            feedback_id = feedback.id

        login_as(client, "admin@cardapio.saas", "admin123")
        list_page = client.get("/admin/sugestoes")
        assert list_page.status_code == 200
        assert "atalhos de teclado".encode() in list_page.data

        resp = _post_with_csrf(
            client, f"/admin/sugestoes/{feedback_id}/status",
            {"status": "resolved", "admin_notes": "Adicionado no roadmap"},
            referer_url="/admin/sugestoes",
        )
        assert resp.status_code == 200

        with app.app_context():
            updated = db.session.get(Feedback, feedback_id)
            assert updated.status == FeedbackStatus.RESOLVED
            assert updated.admin_notes == "Adicionado no roadmap"

    def test_requires_super_admin(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/sugestoes", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)
