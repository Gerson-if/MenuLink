"""
Testes da notificação automática de status do pedido por WhatsApp
(WhatsApp Cloud API da Meta).

Como não há uma conta WhatsApp Business real conectada a este
ambiente, os testes que envolvem chamada HTTP mockam `requests.request`
(via monkeypatch) — mesmo padrão de test_asaas_integration.py: validam
que o NOSSO código monta a requisição certa e reage certo à resposta,
não testam a API da Meta em si.
"""

import json

import pytest

from app.extensions import db
from app.models import Plan, Tenant
from app.services.whatsapp_gateway import WhatsAppGateway, WhatsAppGatewayError
from app.services.whatsapp_notification_service import notify_order_status_change


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


def _create_product(app):
    from app.models import Category, Product

    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        category = Category(tenant_id=tenant.id, name="Carnes", slug="carnes", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(
            tenant_id=tenant.id, category_id=category.id, name="Costela na Brasa",
            slug="costela", price_cents=3000, is_active=True,
        )
        db.session.add(product)
        db.session.commit()
        return product.id


def _create_order_via_checkout(client, product_id, phone="67999990001"):
    payload = {
        "customer_name": "Cliente Teste",
        "customer_phone": phone,
        "delivery_type": "pickup",
        "payment_method": "cash",
        "items": [{"product_id": product_id, "quantity": 1}],
    }
    resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    return resp.get_json()


def _configure_whatsapp(app, *, allow_plan=True, template="pedido_saiu_entrega"):
    with app.app_context():
        from app.models.platform_settings import PlatformSettings

        settings = PlatformSettings.get_or_create()
        settings.whatsapp_access_token = "fake-token-123"
        settings.whatsapp_phone_number_id = "1234567890"
        settings.whatsapp_status_templates = {"out_for_delivery": template} if template else {}
        db.session.commit()

        if allow_plan:
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.plan.allow_whatsapp_notifications = True
            db.session.commit()


class TestWhatsAppGateway:
    def test_send_template_message_builds_expected_payload(self, app, monkeypatch):
        captured = {}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = kwargs.get("json")
            return _FakeResponse(200, {"messages": [{"id": "wamid.abc123"}]})

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            gateway = WhatsAppGateway(access_token="tok", phone_number_id="999")
            message_id = gateway.send_template_message(
                to="5567999990001", template_name="pedido_saiu_entrega", body_params=["Braseiro & Cia"],
            )

        assert message_id == "wamid.abc123"
        assert captured["method"] == "POST"
        assert captured["url"] == "https://graph.facebook.com/v20.0/999/messages"
        assert captured["headers"]["Authorization"] == "Bearer tok"
        payload = captured["json"]
        assert payload["to"] == "5567999990001"
        assert payload["template"]["name"] == "pedido_saiu_entrega"
        assert payload["template"]["components"][0]["parameters"][0]["text"] == "Braseiro & Cia"

    def test_send_template_message_raises_on_error_response(self, app, monkeypatch):
        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            return _FakeResponse(400, {"error": {"message": "Token inválido"}})

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            gateway = WhatsAppGateway(access_token="tok", phone_number_id="999")
            with pytest.raises(WhatsAppGatewayError, match="Token inválido"):
                gateway.send_template_message(to="5567999990001", template_name="x")

    def test_send_template_message_raises_on_connection_error(self, app, monkeypatch):
        import requests

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            gateway = WhatsAppGateway(access_token="tok", phone_number_id="999")
            with pytest.raises(WhatsAppGatewayError):
                gateway.send_template_message(to="5567999990001", template_name="x")

    def test_ping_hits_phone_number_id_endpoint(self, app, monkeypatch):
        captured = {}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["url"] = url
            return _FakeResponse(200, {"display_phone_number": "+55 67 99999-9998"})

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            WhatsAppGateway(access_token="tok", phone_number_id="999").ping()

        assert captured["url"] == "https://graph.facebook.com/v20.0/999"


class TestGetGateway:
    def test_returns_none_when_not_configured(self, app):
        with app.app_context():
            from app.services.whatsapp_gateway import get_gateway

            assert get_gateway() is None

    def test_returns_instance_when_configured(self, app):
        _configure_whatsapp(app)
        with app.app_context():
            from app.services.whatsapp_gateway import get_gateway

            gateway = get_gateway()
            assert gateway is not None
            assert gateway.phone_number_id == "1234567890"


class TestNotifyOrderStatusChange:
    def test_sends_message_when_fully_configured(self, app, client, monkeypatch):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)
        _configure_whatsapp(app)

        captured = {}

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse(200, {"messages": [{"id": "wamid.xyz"}]})

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            notify_order_status_change(order, "out_for_delivery")

        assert captured["json"]["to"] == "5567999990001"
        assert captured["json"]["template"]["name"] == "pedido_saiu_entrega"
        assert captured["json"]["template"]["components"][0]["parameters"][0]["text"] == "Braseiro & Cia"

    def test_noop_when_status_not_notifiable(self, app, client, monkeypatch):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)
        _configure_whatsapp(app, template=None)

        called = []
        monkeypatch.setattr(
            "app.services.whatsapp_gateway.requests.request",
            lambda *a, **k: called.append(1) or _FakeResponse(200, {"messages": [{"id": "x"}]}),
        )

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            notify_order_status_change(order, "pending")

        assert not called

    def test_noop_when_plan_does_not_allow(self, app, client, monkeypatch):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)
        _configure_whatsapp(app, allow_plan=False)

        called = []
        monkeypatch.setattr(
            "app.services.whatsapp_gateway.requests.request",
            lambda *a, **k: called.append(1) or _FakeResponse(200, {"messages": [{"id": "x"}]}),
        )

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            notify_order_status_change(order, "out_for_delivery")

        assert not called

    def test_noop_when_no_template_configured_for_status(self, app, client, monkeypatch):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)
        _configure_whatsapp(app, template=None)

        called = []
        monkeypatch.setattr(
            "app.services.whatsapp_gateway.requests.request",
            lambda *a, **k: called.append(1) or _FakeResponse(200, {"messages": [{"id": "x"}]}),
        )

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            notify_order_status_change(order, "out_for_delivery")

        assert not called

    def test_noop_when_whatsapp_not_configured_at_all(self, app, client):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            tenant = order.tenant
            tenant.plan.allow_whatsapp_notifications = True
            db.session.commit()
            # Não deve levantar mesmo sem nenhuma configuração de WhatsApp.
            notify_order_status_change(order, "out_for_delivery")

    def test_swallows_gateway_error_without_raising(self, app, client, monkeypatch):
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)
        _configure_whatsapp(app)

        def fake_request(method, url, headers=None, timeout=None, **kwargs):
            return _FakeResponse(400, {"error": {"message": "template não aprovado"}})

        monkeypatch.setattr("app.services.whatsapp_gateway.requests.request", fake_request)

        with app.app_context():
            from app.models import Order

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            # Não deve levantar mesmo com a Meta recusando o envio.
            notify_order_status_change(order, "out_for_delivery")


class TestOrderStatusUpdateTriggersNotification(object):
    def test_update_status_does_not_break_without_whatsapp_configured(self, app, client, login_as):
        """Garantia central da feature: sem NADA de WhatsApp configurado
        (caso comum, padrão de qualquer instalação nova), mudar o status
        de um pedido continua funcionando normalmente."""
        product_id = _create_product(app)
        order_data = _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(f"/painel/pedidos/{order_data['order_id']}/aceitar", follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.models import Order, OrderStatus

            order = Order.query.filter_by(order_number=order_data["order_number"]).first()
            assert order.status == OrderStatus.CONFIRMED


class TestAdminWhatsAppSettings:
    def test_lojista_cannot_access_whatsapp_settings(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/configuracoes/whatsapp")
        assert resp.status_code == 403

    def test_super_admin_can_save_whatsapp_settings(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/configuracoes/whatsapp")
        assert resp.status_code == 200

        resp = client.post(
            "/admin/configuracoes/whatsapp",
            data={
                "access_token": "novo-token-abc", "phone_number_id": "111222333",
                "template_confirmed": "pedido_confirmado", "template_preparing": "",
                "template_out_for_delivery": "pedido_saiu_entrega", "template_ready_for_pickup": "",
                "template_completed": "", "template_canceled": "",
            },
            follow_redirects=True,
        )
        assert "Configurações do WhatsApp atualizadas" in resp.get_data(as_text=True)

        with app.app_context():
            from app.models.platform_settings import PlatformSettings

            settings = PlatformSettings.get_or_create()
            assert settings.whatsapp_access_token == "novo-token-abc"
            assert settings.whatsapp_phone_number_id == "111222333"
            assert settings.whatsapp_status_templates == {
                "confirmed": "pedido_confirmado", "out_for_delivery": "pedido_saiu_entrega",
            }

    def test_test_connection_route_reports_failure_when_unconfigured(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/whatsapp/testar", follow_redirects=True)
        assert "Falha ao conectar" in resp.get_data(as_text=True)


class TestPlanWhatsAppToggle:
    def test_plan_form_saves_allow_whatsapp_notifications(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/planos/novo",
            data={
                "name": "Turbo", "description": "", "price": "49.90",
                "billing_cycle": "monthly", "max_categories": "", "max_products": "",
                "max_images_per_product": "5", "allow_whatsapp_notifications": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        plan = Plan.query.filter_by(name="Turbo").first()
        assert plan.allow_whatsapp_notifications is True
