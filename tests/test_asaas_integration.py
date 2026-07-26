"""
Testes da integração com o Asaas.

Como não há uma conta Asaas real conectada a este ambiente, os testes
que envolvem chamadas HTTP mockam `requests.request` (via
monkeypatch) para simular respostas da API do Asaas, validando que o
NOSSO código monta a requisição certa e interpreta a resposta certa —
não testam a API do Asaas em si (isso só um teste contra o sandbox
deles poderia garantir, ver aviso no topo de asaas_gateway.py).
"""

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Invoice, InvoiceStatus, Tenant, TenantStatus
from app.services.payment_gateway.asaas_gateway import AsaasGateway
from app.services.payment_gateway.base import PaymentGatewayError


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


class TestGatewayFactory:
    def test_get_gateway_returns_none_when_not_configured(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = ""
            from app.services.payment_gateway import get_gateway

            assert get_gateway() is None

    def test_get_gateway_returns_instance_when_configured(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key-123"
            from app.services.payment_gateway import get_gateway

            gateway = get_gateway()
            assert gateway is not None
            assert gateway.api_key == "fake-key-123"

    def test_get_gateway_defaults_to_pix_billing_type(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key-123"
            from app.services.payment_gateway import get_gateway

            assert get_gateway().billing_type == "PIX"

    def test_get_gateway_uses_configured_billing_type(self, app):
        with app.app_context():
            from app.models.platform_settings import PlatformSettings

            app.config["ASAAS_API_KEY"] = "fake-key-123"
            settings = PlatformSettings.get_or_create()
            settings.asaas_billing_type = "BOLETO"
            db.session.commit()

            from app.services.payment_gateway import get_gateway

            assert get_gateway().billing_type == "BOLETO"


class TestAsaasGatewayBillingTypeDefaultsToPix:
    def test_defaults_to_pix_when_not_passed(self):
        gateway = AsaasGateway(api_key="fake")
        assert gateway.billing_type == "PIX"

    def test_falls_back_to_pix_on_unknown_value(self):
        gateway = AsaasGateway(api_key="fake", billing_type="SOMETHING_INVALID")
        assert gateway.billing_type == "PIX"

    def test_accepts_a_valid_alternative(self):
        gateway = AsaasGateway(api_key="fake", billing_type="BOLETO")
        assert gateway.billing_type == "BOLETO"

    def test_create_charge_payload_uses_configured_billing_type(self, app, monkeypatch):
        captured = {}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["json"] = kwargs.get("json")
            if "/customers" in url:
                return _FakeResponse(200, {"id": "cus_pix"})
            return _FakeResponse(200, {"id": "pay_pix", "invoiceUrl": "https://sandbox.asaas.com/i/pay_pix", "status": "PENDING"})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            from datetime import date, timedelta

            from app.models import Invoice, InvoiceStatus, Plan, Subscription, SubscriptionStatus

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = None
            plan = Plan.query.first()
            subscription = Subscription(
                tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
                current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
            )
            db.session.add(subscription)
            db.session.flush()
            invoice = Invoice(
                tenant_id=tenant.id, subscription_id=subscription.id,
                amount_cents=4990, status=InvoiceStatus.PENDING, due_date=date.today() + timedelta(days=5),
            )
            db.session.add(invoice)
            db.session.commit()

            gateway = AsaasGateway(api_key="fake", billing_type="PIX")
            gateway.create_charge(tenant, invoice)

            # `captured["json"]` guarda o payload da ÚLTIMA chamada feita
            # (a de /payments, já que /customers acontece primeiro dentro
            # de create_charge) — é nela que billingType é enviado.
            assert captured["json"]["billingType"] == "PIX"


class TestAsaasGatewayRequests:
    def test_ensure_customer_creates_when_missing(self, app, monkeypatch):
        captured = {}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _FakeResponse(200, {"id": "cus_123456"})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = None

            gateway = AsaasGateway(api_key="fake", environment="sandbox")
            customer_id = gateway.ensure_customer(tenant)

            assert customer_id == "cus_123456"
            assert tenant.asaas_customer_id == "cus_123456"
            assert captured["method"] == "POST"
            assert "/customers" in captured["url"]
            assert captured["json"]["externalReference"] == str(tenant.id)

    def test_ensure_customer_reuses_existing_id_without_calling_api(self, app, monkeypatch):
        def fake_request(*args, **kwargs):
            raise AssertionError("não deveria chamar a API se já existe asaas_customer_id")

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = "cus_ja_existe"

            gateway = AsaasGateway(api_key="fake", environment="sandbox")
            customer_id = gateway.ensure_customer(tenant)
            assert customer_id == "cus_ja_existe"

    def test_create_charge_builds_correct_payload(self, app, monkeypatch):
        calls = []

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            calls.append({"method": method, "url": url, "json": kwargs.get("json")})
            if "/customers" in url:
                return _FakeResponse(200, {"id": "cus_999"})
            return _FakeResponse(200, {"id": "pay_888", "invoiceUrl": "https://sandbox.asaas.com/i/pay_888", "status": "PENDING"})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = None

            from app.models import Plan, Subscription, SubscriptionStatus

            plan = Plan.query.first()
            subscription = Subscription(
                tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
                current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
            )
            db.session.add(subscription)
            db.session.flush()
            invoice = Invoice(
                tenant_id=tenant.id, subscription_id=subscription.id,
                amount_cents=4990, status=InvoiceStatus.PENDING, due_date=date.today() + timedelta(days=5),
            )
            db.session.add(invoice)
            db.session.commit()

            gateway = AsaasGateway(api_key="fake", environment="sandbox")
            charge = gateway.create_charge(tenant, invoice)

            assert charge["id"] == "pay_888"
            assert charge["payment_url"] == "https://sandbox.asaas.com/i/pay_888"

            payment_call = next(c for c in calls if "/payments" in c["url"])
            assert payment_call["json"]["value"] == 49.90
            assert payment_call["json"]["externalReference"] == str(invoice.id)
            assert payment_call["json"]["customer"] == "cus_999"

    def test_gateway_error_on_http_error_response(self, app, monkeypatch):
        def fake_request(*args, **kwargs):
            return _FakeResponse(400, {"errors": [{"description": "CPF/CNPJ inválido"}]})

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = None

            gateway = AsaasGateway(api_key="fake", environment="sandbox")
            with pytest.raises(PaymentGatewayError, match="CPF/CNPJ inválido"):
                gateway.ensure_customer(tenant)

    def test_gateway_error_on_connection_failure(self, app, monkeypatch):
        import requests

        def fake_request(*args, **kwargs):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.asaas_customer_id = None

            gateway = AsaasGateway(api_key="fake", environment="sandbox")
            with pytest.raises(PaymentGatewayError):
                gateway.ensure_customer(tenant)

    def test_sandbox_vs_production_base_url(self):
        sandbox = AsaasGateway(api_key="x", environment="sandbox")
        production = AsaasGateway(api_key="x", environment="production")
        assert "sandbox" in sandbox.base_url
        assert "sandbox" not in production.base_url
        assert production.base_url.startswith("https://api.asaas.com")


class TestAdminBillingServiceAsaasIntegration:
    def test_generate_charge_fails_gracefully_when_not_configured(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            app.config["ASAAS_API_KEY"] = ""

            from app.services.admin_billing_service import AdminBillingService, BillingError
            from app.models import Plan, Subscription, SubscriptionStatus

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            plan = Plan.query.first()
            subscription = Subscription(
                tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
                current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
            )
            db.session.add(subscription)
            db.session.flush()
            invoice = Invoice(tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=1000, status=InvoiceStatus.PENDING, due_date=date.today())
            db.session.add(invoice)
            db.session.commit()

            with pytest.raises(BillingError, match="não está configurada"):
                AdminBillingService().generate_asaas_charge(invoice)

    def test_invoice_detail_shows_not_configured_notice(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            app.config["ASAAS_API_KEY"] = ""
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        resp = client.get(f"/admin/lojistas/{tenant_id}")
        assert "não está configurada" in resp.get_data(as_text=True)


class TestAsaasWebhook:
    def test_webhook_without_token_configured_is_refused(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = ""
        resp = client.post("/webhooks/asaas", json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_1"}})
        assert resp.status_code == 503

    def test_webhook_with_wrong_token_is_rejected(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = "segredo-correto"
        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_1"}},
            headers={"asaas-access-token": "token-errado"},
        )
        assert resp.status_code == 401

    def test_webhook_marks_invoice_paid_and_unblocks_tenant(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = "segredo-correto"

            from app.models import Plan, Subscription, SubscriptionStatus

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.BLOCKED_PAYMENT
            tenant.blocked_reason = "Fatura vencida"
            plan = Plan.query.first()
            subscription = Subscription(
                tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
                current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
            )
            db.session.add(subscription)
            db.session.flush()
            invoice = Invoice(
                tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=4990,
                status=InvoiceStatus.PENDING, due_date=date.today(), asaas_payment_id="pay_webhook_1",
            )
            db.session.add(invoice)
            db.session.commit()
            invoice_id = invoice.id

        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_webhook_1", "status": "CONFIRMED"}},
            headers={"asaas-access-token": "segredo-correto"},
        )
        assert resp.status_code == 200

        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            assert invoice.status == InvoiceStatus.PAID

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.status == TenantStatus.ACTIVE
            assert tenant.blocked_reason is None

    def test_webhook_ignores_unknown_payment_id(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = "segredo-correto"
        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_nao_existe"}},
            headers={"asaas-access-token": "segredo-correto"},
        )
        assert resp.status_code == 200  # reconhece, não quebra, só não faz nada

    def test_webhook_ignores_non_payment_events(self, app, client):
        with app.app_context():
            app.config["ASAAS_WEBHOOK_TOKEN"] = "segredo-correto"

            from app.models import Plan, Subscription, SubscriptionStatus

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            plan = Plan.query.first()
            subscription = Subscription(
                tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
                current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
            )
            db.session.add(subscription)
            db.session.flush()
            invoice = Invoice(
                tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=4990,
                status=InvoiceStatus.PENDING, due_date=date.today(), asaas_payment_id="pay_created_only",
            )
            db.session.add(invoice)
            db.session.commit()
            invoice_id = invoice.id

        resp = client.post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_CREATED", "payment": {"id": "pay_created_only"}},
            headers={"asaas-access-token": "segredo-correto"},
        )
        assert resp.status_code == 200

        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            assert invoice.status == InvoiceStatus.PENDING  # não mudou
