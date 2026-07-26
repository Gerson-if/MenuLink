"""
CPF/CNPJ do lojista — necessário para a integração com o Asaas gerar
cobrança de verdade (a API do Asaas exige `cpfCnpj` ao criar o cliente
de cobrança, ver AsaasGateway.ensure_customer). Cobre: validação de
formato, o lojista conseguir editar isso em Configurações da loja, o
valor chegando certo no payload do Asaas, e o Super Admin não conseguir
gerar cobrança pra quem ainda não cadastrou.
"""

import re

from app.extensions import db
from app.models import Tenant
from app.services.admin_billing_service import AdminBillingService, BillingError
from app.services.tenant_settings_service import TenantSettingsService
from app.utils.validators import _is_valid_cnpj, _is_valid_cpf, only_digits


def _tenant():
    return Tenant.query.filter_by(slug="braseiro-cia").first()


def _csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestCpfCnpjChecksum:
    def test_accepts_known_valid_cpf(self):
        assert _is_valid_cpf(only_digits("529.982.247-25")) is True

    def test_rejects_cpf_with_all_same_digits(self):
        assert _is_valid_cpf("11111111111") is False

    def test_rejects_cpf_with_wrong_check_digits(self):
        assert _is_valid_cpf("12345678900") is False

    def test_accepts_known_valid_cnpj(self):
        assert _is_valid_cnpj(only_digits("11.222.333/0001-81")) is True

    def test_rejects_cnpj_with_wrong_check_digits(self):
        assert _is_valid_cnpj("11222333000199") is False


class TestUpdateStoreInfoPersistsDocument:
    def test_service_stores_only_digits(self, app):
        with app.app_context():
            tenant = _tenant()
            TenantSettingsService(tenant).update_store_info(
                name=tenant.name, whatsapp_number="", phone="",
                legal_name="Braseiro & Cia LTDA", document="11.222.333/0001-81",
            )

            assert tenant.legal_name == "Braseiro & Cia LTDA"
            assert tenant.document == "11222333000181"

    def test_blank_document_clears_the_field(self, app):
        with app.app_context():
            tenant = _tenant()
            tenant.document = "11222333000181"
            db.session.commit()

            TenantSettingsService(tenant).update_store_info(
                name=tenant.name, whatsapp_number="", phone="", document="",
            )

            assert tenant.document is None

    def test_lojista_can_update_document_via_settings_route(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/loja")
        csrf = _csrf(resp.get_data(as_text=True))

        resp = client.post(
            "/painel/configuracoes/loja",
            data={
                "name": "Braseiro & Cia", "whatsapp_number": "", "phone": "",
                "legal_name": "Braseiro & Cia LTDA", "document": "529.982.247-25",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = _tenant()
            assert tenant.document == "52998224725"
            assert tenant.legal_name == "Braseiro & Cia LTDA"

    def test_invalid_document_is_rejected_by_the_form(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/loja")
        csrf = _csrf(resp.get_data(as_text=True))

        resp = client.post(
            "/painel/configuracoes/loja",
            data={
                "name": "Braseiro & Cia", "whatsapp_number": "", "phone": "",
                "document": "123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "CPF" in resp.get_data(as_text=True) or "CNPJ" in resp.get_data(as_text=True)

        with app.app_context():
            assert _tenant().document is None


class TestAsaasPayloadIncludesDocument:
    def test_ensure_customer_sends_cpf_cnpj_when_present(self, app, monkeypatch):
        from app.services.payment_gateway.asaas_gateway import AsaasGateway

        captured = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {"id": "cus_doc_123"}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse()

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = _tenant()
            tenant.asaas_customer_id = None
            tenant.document = "11222333000181"
            db.session.commit()

            AsaasGateway(api_key="fake", environment="sandbox").ensure_customer(tenant)

            assert captured["json"]["cpfCnpj"] == "11222333000181"

    def test_ensure_customer_omits_cpf_cnpj_when_absent(self, app, monkeypatch):
        from app.services.payment_gateway.asaas_gateway import AsaasGateway

        captured = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {"id": "cus_nodoc_123"}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse()

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            tenant = _tenant()
            tenant.asaas_customer_id = None
            tenant.document = None
            db.session.commit()

            AsaasGateway(api_key="fake", environment="sandbox").ensure_customer(tenant)

            assert "cpfCnpj" not in captured["json"]


class TestGenerateAsaasChargeRequiresDocument:
    def test_refuses_when_tenant_has_no_document(self, app):
        from datetime import date, timedelta

        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = _tenant()
            tenant.document = None
            db.session.commit()

            service = AdminBillingService()
            invoice = service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() + timedelta(days=5))

            import pytest

            with pytest.raises(BillingError, match="CPF/CNPJ"):
                service.generate_asaas_charge(invoice)

    def test_succeeds_when_tenant_has_document(self, app, monkeypatch):
        from datetime import date, timedelta

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            class _FakeResponse:
                status_code = 200

                def json(self):
                    if "/customers" in url:
                        return {"id": "cus_ok"}
                    return {"id": "pay_ok", "invoiceUrl": "https://sandbox.asaas.com/i/pay_ok", "status": "PENDING"}

            return _FakeResponse()

        monkeypatch.setattr("app.services.payment_gateway.asaas_gateway.requests.request", fake_request)

        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = _tenant()
            tenant.document = "11222333000181"
            db.session.commit()

            service = AdminBillingService()
            invoice = service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() + timedelta(days=5))

            result = service.generate_asaas_charge(invoice)

            assert result.asaas_payment_id == "pay_ok"
