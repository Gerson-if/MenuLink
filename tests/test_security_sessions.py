"""
Seção "Segurança" (troca de senha, editar próprio perfil, sessões
ativas) — comum ao lojista e ao Super Admin. Ver
app/services/session_service.py, app/utils/session_guard.py,
app/controllers/lojista/security.py e app/controllers/admin/security.py.
"""

import re

from app.extensions import db
from app.models import User, UserSession


def _extract_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


def _post_with_csrf(client, url, data):
    # Pega o csrf da própria página de segurança (evita duplicar login_as
    # pra cada client aqui).
    page = client.get("/painel/configuracoes/seguranca")
    csrf = _extract_csrf(page.get_data(as_text=True))
    if csrf:
        data = {**data, "csrf_token": csrf}
    return client.post(url, data=data, follow_redirects=True)


class TestSessionTrackingOnLoginLogout:
    def test_login_creates_a_tracked_session(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            sessions = UserSession.query.filter_by(user_id=user.id, revoked_at=None).all()
            assert len(sessions) == 1
            assert sessions[0].revoked_at is None

    def test_logout_revokes_the_session(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.get("/logout")
        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            active = UserSession.query.filter_by(user_id=user.id, revoked_at=None).all()
            assert active == []


class TestRevokingSessionFromAnotherDevice:
    """
    Duas conexões HTTP concorrentes de verdade (device A e B) sempre têm
    contexto de app próprio no Flask real — só não têm dentro de um único
    teste que usa a fixture `app` (que mantém UM app_context aberto pro
    teste inteiro, ver conftest.py), porque aí os dois test_clients acabam
    reaproveitando o mesmo `flask.g` (current_user fica "colado" no
    primeiro login). Por isso os testes abaixo simulam o "outro
    dispositivo" direto no banco (UserSession) em vez de um segundo login
    HTTP de verdade — o que importa aqui é testar a revogação e o guard
    (session_guard.enforce_session_not_revoked), não a criação da sessão
    em si (já coberta em TestSessionTrackingOnLoginLogout)."""

    def test_revoking_own_current_session_logs_out_on_next_request(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            session_id = UserSession.query.filter_by(user_id=user.id, revoked_at=None).one().id

        _post_with_csrf(client, f"/painel/configuracoes/seguranca/sessoes/{session_id}/encerrar", {})

        resp = client.get("/painel/dashboard", follow_redirects=True)
        assert 'name="password"' in resp.get_data(as_text=True)

    def test_revoke_all_other_sessions_keeps_current_one_active(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            current_session_id = UserSession.query.filter_by(user_id=user.id, revoked_at=None).one().id
            # simula um "outro dispositivo" ativo, direto no banco
            other = UserSession(user_id=user.id, session_token="fake-other-device-token")
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        _post_with_csrf(client, "/painel/configuracoes/seguranca/sessoes/encerrar-outras", {})

        with app.app_context():
            current = db.session.get(UserSession, current_session_id)
            other = db.session.get(UserSession, other_id)
            assert current.revoked_at is None
            assert other.revoked_at is not None

        # a sessão atual (do client que fez a chamada) continua funcionando
        resp = client.get("/painel/dashboard", follow_redirects=True)
        assert "Categorias" in resp.get_data(as_text=True)


class TestChangePassword:
    def test_wrong_current_password_is_rejected(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = _post_with_csrf(
            client, "/painel/configuracoes/seguranca/senha",
            {"current_password": "senha-errada", "new_password": "novaSenha123", "confirm_password": "novaSenha123"},
        )
        assert "incorreta" in resp.get_data(as_text=True)

    def test_successful_change_allows_login_with_new_password(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        _post_with_csrf(
            client, "/painel/configuracoes/seguranca/senha",
            {"current_password": "lojista123", "new_password": "novaSenha123", "confirm_password": "novaSenha123"},
        )
        client.get("/logout")

        resp = login_as(client, "lojista@braseiroecia.com.br", "novaSenha123")
        assert "Categorias" in resp.get_data(as_text=True)


class TestUpdateProfile:
    def test_updates_name_and_email(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        _post_with_csrf(
            client, "/painel/configuracoes/seguranca/perfil",
            {"name": "Novo Nome", "email": "novoemail@braseiroecia.com.br"},
        )
        with app.app_context():
            user = User.query.filter_by(email="novoemail@braseiroecia.com.br").first()
            assert user is not None
            assert user.name == "Novo Nome"

    def test_rejects_email_already_used_by_another_account(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = _post_with_csrf(
            client, "/painel/configuracoes/seguranca/perfil",
            {"name": "Lojista", "email": "admin@cardapio.saas"},
        )
        assert "já está em uso" in resp.get_data(as_text=True)


class TestAdminSecurityPage:
    def test_page_renders_for_super_admin(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/seguranca")
        assert resp.status_code == 200
        assert "Trocar senha" in resp.get_data(as_text=True)

    def test_lojista_cannot_access_admin_security_page(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/seguranca")
        assert resp.status_code == 403
