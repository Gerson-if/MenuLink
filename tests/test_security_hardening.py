"""
Testes de regressão para os itens corrigidos numa auditoria de
segurança: cabeçalhos HTTP de proteção, segredos de integração
criptografados em repouso, CSRF no logout do cliente do cardápio
público, e escape de entrada do usuário em e-mail HTML.
"""

from unittest.mock import patch

import sqlalchemy as sa

from app.extensions import db
from app.models.platform_settings import PlatformSettings
from app.models.tenant import TenantStatus
from app.utils.mailer import send_password_reset_email


class TestSecurityHeaders:
    def test_response_carries_hardening_headers(self, client):
        resp = client.get("/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")

    def test_csp_allows_unsafe_eval_for_alpine(self, client):
        # Regressão: a primeira versão da CSP (script-src 'self'
        # 'unsafe-inline', sem 'unsafe-eval') quebrava o Alpine.js por
        # completo em toda a interface — toast de notificação não sumia
        # mais, botão de fechar (x) parava de responder, gaveta lateral
        # não abria — porque o Alpine compila cada expressão x-data/
        # x-show/@click com `new Function()`, bloqueado sem essa
        # permissão. Ver comentário em app/__init__.py:set_security_headers.
        resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "'unsafe-eval'" in csp

    def test_hsts_absent_without_https_cookie(self, client):
        # TestingConfig roda sem HTTPS (SESSION_COOKIE_SECURE=False) —
        # anunciar HSTS aqui faria o navegador recusar HTTP simples
        # (inclusive localhost em dev), então não deve aparecer.
        resp = client.get("/login")
        assert "Strict-Transport-Security" not in resp.headers


class TestSecretsEncryptedAtRest:
    def test_asaas_api_key_is_encrypted_in_the_database_column(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = "sk_test_super_secreta_123"
            db.session.commit()
            db.session.expire_all()

            # Round-trip pela ORM continua transparente...
            reloaded = PlatformSettings.get_or_create()
            assert reloaded.asaas_api_key == "sk_test_super_secreta_123"

            # ...mas o valor bruto gravado no banco não é o texto puro.
            raw = db.session.execute(
                sa.text("SELECT asaas_api_key FROM platform_settings WHERE id = :id"),
                {"id": settings.id},
            ).scalar()
            assert raw != "sk_test_super_secreta_123"
            assert raw is not None and len(raw) > len("sk_test_super_secreta_123")

    def test_empty_secret_stays_empty(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.asaas_api_key = None
            db.session.commit()
            db.session.expire_all()
            assert PlatformSettings.get_or_create().asaas_api_key is None


class TestCustomerLogoutCsrf:
    def test_logout_without_csrf_token_is_rejected_when_csrf_enabled(self, app):
        # TestingConfig desliga CSRF globalmente (WTF_CSRF_ENABLED=False)
        # pra não exigir token em toda chamada de teste do resto da
        # suíte — aqui ligamos de propósito só pra confirmar que a
        # checagem manual do endpoint (igual create_order/customer_login)
        # realmente existe e bloqueia sem o cabeçalho X-CSRFToken.
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            with app.app_context():
                from app.models.tenant import Tenant

                tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
                tenant.status = TenantStatus.ACTIVE
                db.session.commit()

            client = app.test_client()
            resp = client.post("/loja/braseiro-cia/sair")
            assert resp.status_code == 400
        finally:
            app.config["WTF_CSRF_ENABLED"] = False


class TestPasswordResetEmailEscapesName:
    def test_malicious_display_name_is_escaped_in_html_body(self, app):
        with app.app_context():
            from app.models import User

            user = User(name="<img src=x onerror=alert(1)>", email="attacker@example.com")

            captured = {}

            def _fake_send(*, to, subject, body_text, body_html=None):
                captured["body_html"] = body_html

            with patch("app.utils.mailer.EmailService") as MockService:
                instance = MockService.return_value
                instance.is_configured = True
                instance.send.side_effect = _fake_send

                send_password_reset_email(user, "faketoken")

            assert "<img src=x onerror" not in captured["body_html"]
            assert "&lt;img src=x onerror" in captured["body_html"]
