"""
Conta bloqueada por inadimplência (BLOCKED_PAYMENT): o lojista consegue
logar e só acessa a página de regularização de pendências — o resto do
painel redireciona pra lá. Ver app/utils/decorators.py
(lojista_required/lojista_login_required) e
app/controllers/lojista/settings.py (billing_lockout).

O cardápio público de uma loja bloqueada mostra uma página amigável
(sem expor o motivo) em vez do menu — ver public_controller.store_home.
"""

from datetime import date, timedelta

from app.extensions import db
from app.models import Tenant, TenantStatus
from app.services.admin_billing_service import AdminBillingService


def _block_tenant(app, reason="Fatura em atraso"):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        tenant.status = TenantStatus.BLOCKED_PAYMENT
        tenant.blocked_reason = reason
        db.session.commit()
        return tenant.id


class TestBlockedLoginAllowed:
    def test_blocked_payment_tenant_can_log_in_and_lands_on_lockout_page(self, app, client, login_as):
        _block_tenant(app)
        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        assert resp.status_code == 200
        assert "Acesso bloqueado por pend" in resp.get_data(as_text=True)

    def test_suspended_tenant_still_cannot_log_in(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.SUSPENDED
            db.session.commit()

        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        assert "suspensa" in resp.get_data(as_text=True)
        # não deve ter uma sessão válida (ainda vê o form de login)
        assert 'name="password"' in resp.get_data(as_text=True)


class TestBlockedLojistaConfinedToLockoutPage:
    def test_dashboard_redirects_to_lockout(self, app, client, login_as):
        _block_tenant(app)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/dashboard", follow_redirects=True)
        assert "Acesso bloqueado por pend" in resp.get_data(as_text=True)

    def test_lockout_page_lists_open_invoices(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            service = AdminBillingService()
            service.create_invoice(tenant, amount_reais=79.90, due_date=date.today() - timedelta(days=3))
        _block_tenant(app)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/conta-bloqueada")
        html = resp.get_data(as_text=True)
        assert "79.90" in html

    def test_active_tenant_visiting_lockout_url_is_redirected_to_dashboard(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/conta-bloqueada", follow_redirects=True)
        assert "Acesso bloqueado" not in resp.get_data(as_text=True)


class TestPublicStoreUnavailablePage:
    def test_blocked_tenant_store_shows_friendly_message(self, app, client):
        _block_tenant(app)
        resp = client.get("/loja/braseiro-cia")
        assert resp.status_code == 404
        html = resp.get_data(as_text=True)
        assert "indispon" in html.lower()
        assert "pagamento" not in html.lower()
        assert "inadimpl" not in html.lower()

    def test_active_tenant_store_still_works(self, client):
        resp = client.get("/loja/braseiro-cia")
        assert resp.status_code == 200

    def test_unknown_slug_is_plain_404(self, client):
        resp = client.get("/loja/nao-existe-esse-slug")
        assert resp.status_code == 404
