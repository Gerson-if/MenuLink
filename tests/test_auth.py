from app.extensions import db
from app.models import Tenant, TenantStatus


class TestLogin:
    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_invalid_credentials_shows_error(self, client, login_as):
        resp = login_as(client, "admin@cardapio.saas", "senha-errada")
        assert resp.status_code == 200
        assert "inválidos" in resp.get_data(as_text=True)

    def test_oversized_password_is_rejected_without_crashing(self, client, login_as):
        """Regressão: bcrypt só aceita até 72 bytes de senha. Uma senha
        gigante (ou uma com 128 caracteres cheios de acento/emoji, que já
        passa dos 72 bytes em UTF-8) derrubava o login com 500 em vez de
        mostrar "credenciais inválidas" — ver app/utils/validators.py
        (password_max_bytes) e app/models/user.py."""
        resp = login_as(client, "admin@cardapio.saas", "x" * 500)
        assert resp.status_code == 200
        assert "muito longa" in resp.get_data(as_text=True)

        # 128 caracteres, mas em UTF-8 isso passa de 72 bytes por causa dos
        # acentos — Length(max=128) sozinho deixaria passar e quebraria no
        # bcrypt; password_max_bytes barra antes disso.
        resp = login_as(client, "admin@cardapio.saas", "á" * 128)
        assert resp.status_code == 200
        assert "muito longa" in resp.get_data(as_text=True)

    def test_super_admin_login_redirects_to_admin_dashboard(self, client, login_as):
        resp = login_as(client, "admin@cardapio.saas", "admin123")
        assert resp.status_code == 200
        assert "Métricas Core" in resp.get_data(as_text=True)

    def test_lojista_login_redirects_to_lojista_dashboard(self, client, login_as):
        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        assert resp.status_code == 200
        assert "Braseiro" in resp.get_data(as_text=True)

    def test_dashboard_requires_login(self, client):
        resp = client.get("/painel/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_next_param_redirects_to_the_originally_requested_page(self, client, login_as):
        # login_required (Flask-Login) manda pra /login?next=/painel/dashboard
        # quando um visitante não logado tenta acessar o painel direto —
        # depois do login, deve voltar pra lá.
        resp = client.get("/login?next=/painel/dashboard")
        assert resp.status_code == 200

    def test_open_redirect_via_next_param_is_blocked(self, client):
        resp = client.post(
            "/login?next=https://golpe-phishing.example.com",
            data={"email": "lojista@braseiroecia.com.br", "password": "lojista123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/")
        assert "golpe-phishing" not in resp.headers["Location"]

    def test_protocol_relative_next_param_is_blocked(self, client):
        resp = client.post(
            "/login?next=//golpe-phishing.example.com",
            data={"email": "lojista@braseiroecia.com.br", "password": "lojista123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "golpe-phishing" not in resp.headers["Location"]


class TestAuthorization:
    def test_super_admin_cannot_access_lojista_panel(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/painel/dashboard")
        assert resp.status_code == 403

    def test_lojista_cannot_access_admin_panel(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/dashboard")
        assert resp.status_code == 403

    def test_blocked_payment_tenant_can_login_but_only_reaches_lockout_page(self, app, client, login_as):
        # BLOCKED_PAYMENT é um caso especial: login é permitido, mas cai
        # direto na página de regularização de pendências (ver
        # tests/test_billing_lockout.py para a cobertura completa desse
        # fluxo) — nunca no painel normal.
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.BLOCKED_PAYMENT
            db.session.commit()

        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        body = resp.get_data(as_text=True)
        assert "Acesso bloqueado por pend" in body
        # não deve ter acessado o dashboard
        assert "Categorias" not in body

    def test_suspended_tenant_cannot_login(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.SUSPENDED
            db.session.commit()

        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        body = resp.get_data(as_text=True)
        assert "suspensa" in body
        assert "Categorias" not in body

    def test_blocked_tenant_public_menu_returns_404(self, app, client):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.SUSPENDED
            db.session.commit()

        resp = client.get("/loja/braseiro-cia")
        assert resp.status_code == 404


class TestPublicMenu:
    def test_active_tenant_menu_is_reachable(self, client):
        resp = client.get("/loja/braseiro-cia")
        assert resp.status_code == 200
        assert "Braseiro" in resp.get_data(as_text=True)

    def test_unknown_slug_returns_404(self, client):
        resp = client.get("/loja/nao-existe")
        assert resp.status_code == 404
