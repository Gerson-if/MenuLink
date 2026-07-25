import re

from app.extensions import db
from app.models import Tenant, TenantStatus
from app.services.platform_settings_service import PlatformSettingsService


def _extract_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


class TestSupportWhatsappSetting:
    def test_super_admin_saves_support_whatsapp(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        page = client.get("/admin/configuracoes/aparencia")
        csrf = _extract_csrf(page.get_data(as_text=True))
        data = {"support_whatsapp": "5567999998888"}
        if csrf:
            data["csrf_token"] = csrf

        client.post("/admin/configuracoes/aparencia", data=data, follow_redirects=True)

        with app.app_context():
            settings = PlatformSettingsService().settings
            assert settings.support_whatsapp == "5567999998888"

    def test_invalid_number_is_rejected(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        page = client.get("/admin/configuracoes/aparencia")
        csrf = _extract_csrf(page.get_data(as_text=True))
        data = {"support_whatsapp": "abc"}
        if csrf:
            data["csrf_token"] = csrf

        resp = client.post("/admin/configuracoes/aparencia", data=data, follow_redirects=True)
        assert "Telefone inválido" in resp.get_data(as_text=True)


class TestSupportWhatsappShownToLojista:
    def test_appears_on_billing_lockout_page(self, app, client, login_as):
        with app.app_context():
            PlatformSettingsService().update_admin_appearance(support_whatsapp="5567999998888")
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.BLOCKED_PAYMENT
            db.session.commit()

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/conta-bloqueada")
        assert "https://wa.me/5567999998888" in resp.get_data(as_text=True)

    def test_hidden_when_not_configured(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/assinatura")
        assert "wa.me" not in resp.get_data(as_text=True)
