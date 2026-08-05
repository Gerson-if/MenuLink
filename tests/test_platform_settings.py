"""
Testes da tela de configurações do Asaas no painel do Super Admin
(`PlatformSettings`, `AsaasSettingsForm`, `PlatformSettingsService` e as
rotas em `app/controllers/admin/settings.py`).
"""

import pytest

from app.extensions import db
from app.models.platform_settings import PlatformSettings


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


class TestPlatformSettingsModel:
    def test_get_or_create_creates_singleton_row(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.id == 1
            assert settings.asaas_environment == "sandbox"
            assert settings.asaas_configured is False
            assert settings.asaas_webhook_configured is False

    def test_get_or_create_reuses_existing_row(self, app):
        with app.app_context():
            first = PlatformSettings.get_or_create()
            first.asaas_api_key = "chave-123"
            db.session.commit()

            second = PlatformSettings.get_or_create()
            assert second.id == first.id
            assert second.asaas_api_key == "chave-123"
            assert second.asaas_configured is True


class TestGetGatewayPrefersDatabase:
    def test_get_gateway_uses_db_key_over_env(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "chave-do-env"

            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-do-banco"
            settings.asaas_environment = "production"
            db.session.commit()

            from app.services.payment_gateway import get_gateway

            gateway = get_gateway()
            assert gateway.api_key == "chave-do-banco"
            assert "sandbox" not in gateway.base_url

    def test_get_gateway_falls_back_to_env_when_db_empty(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "chave-do-env"

            from app.services.payment_gateway import get_gateway

            gateway = get_gateway()
            assert gateway is not None
            assert gateway.api_key == "chave-do-env"


class TestAsaasSettingsRoutes:
    def test_requires_super_admin(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/configuracoes/asaas")
        assert resp.status_code in (302, 403)

    def test_anonymous_is_redirected(self, client):
        resp = client.get("/admin/configuracoes/asaas")
        assert resp.status_code == 302

    def test_get_renders_form(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/configuracoes/asaas")
        assert resp.status_code == 200
        assert "Cobrança" in resp.get_data(as_text=True)

    def test_get_prefills_current_api_key_and_webhook_token_for_reveal(self, app, client, login_as):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-secreta-atual"
            settings.asaas_webhook_token = "token-secreto-atual-1234567890"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        html = client.get("/admin/configuracoes/asaas").get_data(as_text=True)
        assert "chave-secreta-atual" in html
        assert "token-secreto-atual-1234567890" in html
        assert "password-toggle-btn" in html  # mascarado por padrão, com botão de revelar

    def test_post_saves_api_key_and_environment(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "production", "api_key": "nova-chave-123"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_api_key == "nova-chave-123"
            assert settings.asaas_environment == "production"

    def test_billing_type_defaults_to_pix(self, app):
        with app.app_context():
            assert PlatformSettings.get_or_create().asaas_billing_type == "PIX"

    def test_get_form_prefills_current_billing_type(self, app, client, login_as):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_billing_type = "BOLETO"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        html = client.get("/admin/configuracoes/asaas").get_data(as_text=True)
        assert '<option selected value="BOLETO">' in html or 'value="BOLETO" selected' in html

    def test_post_updates_billing_type(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "sandbox", "billing_type": "UNDEFINED"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            assert PlatformSettings.get_or_create().asaas_billing_type == "UNDEFINED"

    def test_post_without_billing_type_falls_back_to_pix(self, app, client, login_as):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_billing_type = "BOLETO"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        # Simula um POST direto (sem passar pelo <select>, que sempre
        # manda algum valor) — a defesa em PlatformSettingsService.
        # update_asaas precisa cair pro padrão seguro (PIX) em vez de
        # aceitar um valor fora da lista de opções.
        from app.services.platform_settings_service import PlatformSettingsService

        with app.app_context():
            PlatformSettingsService().update_asaas(environment="sandbox", billing_type="ALGO_INVALIDO")
            assert PlatformSettings.get_or_create().asaas_billing_type == "PIX"

    def test_post_blank_api_key_keeps_current_value(self, app, client, login_as):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-existente"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "sandbox", "api_key": ""},
            follow_redirects=True,
        )

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_api_key == "chave-existente"

    def test_post_clear_api_key_removes_it(self, app, client, login_as):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-existente"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "sandbox", "clear_api_key": "y"},
            follow_redirects=True,
        )

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_api_key is None

    def test_cannot_set_and_clear_api_key_at_the_same_time(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "sandbox", "api_key": "nova-chave", "clear_api_key": "y"},
        )
        assert resp.status_code == 200  # form re-renderizado com erro, não redireciona
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_api_key is None

    def test_cannot_set_and_clear_webhook_token_at_the_same_time(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/asaas",
            data={
                "environment": "sandbox",
                "webhook_token": "um-token-bem-comprido-123456",
                "clear_webhook_token": "y",
            },
        )
        assert resp.status_code == 200
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_webhook_token is None

    def test_webhook_token_too_short_is_rejected(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/asaas",
            data={"environment": "sandbox", "webhook_token": "curto"},
        )
        assert resp.status_code == 200
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_webhook_token is None

    def test_generate_token_route_creates_and_persists_token(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/asaas/gerar-token", follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.asaas_webhook_token is not None
            assert len(settings.asaas_webhook_token) > 20

    def test_test_connection_route_reports_error_when_not_configured(self, app, client, login_as):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = ""
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/asaas/testar", follow_redirects=True)
        assert resp.status_code == 200
        assert "Falha ao conectar" in resp.get_data(as_text=True)

    def test_test_connection_route_reports_success_when_ping_works(self, app, client, login_as, monkeypatch):
        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            return _FakeResponse(200, {"id": "acc_1"})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-valida"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/asaas/testar", follow_redirects=True)
        assert resp.status_code == 200
        assert "funcionando" in resp.get_data(as_text=True)

    def test_test_connection_route_reports_gateway_error(self, app, client, login_as, monkeypatch):
        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            return _FakeResponse(401, {"errors": [{"description": "Chave de API inválida"}]})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "chave-invalida"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/asaas/testar", follow_redirects=True)
        assert resp.status_code == 200
        assert "Falha ao conectar" in resp.get_data(as_text=True)


class TestWebhookUsesDbToken:
    def test_webhook_validates_against_db_stored_token(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = ""
            settings = PlatformSettings.get_or_create()
            settings.asaas_webhook_token = "token-do-banco"
            db.session.commit()

        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_1"}},
            headers={"asaas-access-token": "token-do-banco"},
        )
        assert resp.status_code == 200

    def test_webhook_rejects_wrong_token_when_db_configured(self, app, client):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_webhook_token = "token-do-banco"
            db.session.commit()

        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_1"}},
            headers={"asaas-access-token": "token-errado"},
        )
        assert resp.status_code == 401
