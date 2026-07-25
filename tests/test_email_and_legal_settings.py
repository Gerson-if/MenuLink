"""
Testes da integração de e-mail (SMTP) e do conteúdo editável de Termos de
Uso / Política de Privacidade — ver app/models/platform_settings.py,
app/services/email_service.py, app/services/platform_settings_service.py
e app/controllers/admin/settings.py.
"""

import re

from app.extensions import db
from app.models.platform_settings import PlatformSettings
from app.services.email_service import EmailError, EmailService


def _extract_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


def _post_with_csrf(client, url, data, referer_url):
    page = client.get(referer_url)
    csrf = _extract_csrf(page.get_data(as_text=True))
    if csrf:
        data = {**data, "csrf_token": csrf}
    return client.post(url, data=data, follow_redirects=True)


class TestPlatformSettingsEmailModel:
    def test_email_configured_false_by_default(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.email_configured is False

    def test_email_configured_requires_host_and_from_email(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.smtp_host = "smtp.example.com"
            db.session.commit()
            assert settings.email_configured is False  # falta from_email

            settings.smtp_from_email = "no-reply@example.com"
            db.session.commit()
            assert settings.email_configured is True


class TestEmailServiceValidation:
    def test_send_raises_when_not_configured(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            service = EmailService(settings)
            try:
                service.send(to="a@b.com", subject="Oi", body_text="Oi")
                assert False, "deveria ter levantado EmailError"
            except EmailError as exc:
                assert "configurado" in str(exc)

    def test_send_wraps_connection_failure(self, app):
        """Host inexistente deve virar EmailError com mensagem legível,
        nunca uma exceção crua do smtplib vazando pro chamador."""
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.smtp_host = "host-que-nao-existe.invalid"
            settings.smtp_port = 25
            settings.smtp_from_email = "no-reply@example.com"
            settings.smtp_encryption = "none"
            db.session.commit()

            service = EmailService(settings)
            try:
                service.send(to="a@b.com", subject="Oi", body_text="Oi")
                assert False, "deveria ter levantado EmailError"
            except EmailError:
                pass


class TestEmailSettingsRoutes:
    def test_settings_email_requires_super_admin(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/configuracoes/email")
        assert resp.status_code == 403

    def test_settings_email_page_renders(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/configuracoes/email")
        assert resp.status_code == 200
        assert "Servidor SMTP" in resp.get_data(as_text=True)

    def test_save_email_settings(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = _post_with_csrf(
            client, "/admin/configuracoes/email",
            {
                "host": "smtp.example.com", "port": "587", "encryption": "tls",
                "username": "no-reply@example.com", "password": "segredo123",
                "from_email": "no-reply@example.com", "from_name": "Comanda",
            },
            "/admin/configuracoes/email",
        )
        assert resp.status_code == 200
        assert "atualizadas" in resp.get_data(as_text=True)

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.smtp_host == "smtp.example.com"
            assert settings.smtp_from_email == "no-reply@example.com"
            assert settings.email_configured is True

    def test_send_test_email_without_config_shows_error(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = _post_with_csrf(
            client, "/admin/configuracoes/email/testar", {"to": "admin@cardapio.saas"},
            "/admin/configuracoes/email",
        )
        assert resp.status_code == 200
        assert "Falha ao enviar" in resp.get_data(as_text=True)


class TestLegalContentSettings:
    def test_default_terms_page_shows_hardcoded_text(self, client):
        resp = client.get("/termos-de-uso")
        assert "regulam o acesso e uso da plataforma Comanda" in resp.get_data(as_text=True)

    def test_custom_terms_content_overrides_default(self, app, client):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.terms_content = "<p>Texto customizado de teste dos termos.</p>"
            from datetime import date

            settings.terms_updated_at = date(2026, 1, 1)
            db.session.commit()

        resp = client.get("/termos-de-uso")
        html = resp.get_data(as_text=True)
        assert "Texto customizado de teste dos termos." in html
        assert "01/01/2026" in html
        assert "regulam o acesso e uso da plataforma Comanda" not in html

    def test_settings_legal_requires_super_admin(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/configuracoes/legal")
        assert resp.status_code == 403

    def test_save_legal_content(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = _post_with_csrf(
            client, "/admin/configuracoes/legal",
            {"terms_content": "<p>Novos termos.</p>", "privacy_content": ""},
            "/admin/configuracoes/legal",
        )
        assert resp.status_code == 200
        assert "atualizados" in resp.get_data(as_text=True)

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.terms_content == "<p>Novos termos.</p>"
            assert settings.privacy_content is None  # em branco = volta ao padrão

    def test_store_menu_terms_and_privacy_links_open_in_same_tab(self, app, client):
        """Antes, esses links tinham target="_blank" e abriam uma aba nova
        a cada clique, empilhando abas no navegador do cliente final."""
        with app.app_context():
            from app.models import Tenant

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            slug = tenant.slug

        resp = client.get(f"/loja/{slug}")
        html = resp.get_data(as_text=True)

        for url_fragment in ["politica-de-privacidade", "termos-de-uso"]:
            match = re.search(rf'<a href="[^"]*{url_fragment}[^"]*"([^>]*)>', html)
            assert match is not None, f"link para {url_fragment} não encontrado"
            assert 'target="_blank"' not in match.group(1)
