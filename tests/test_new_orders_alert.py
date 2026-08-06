"""
Alerta de pedido novo (badge + som, ver app/static/js/comanda.js e
app/__init__.py:lojista_pending_orders_count) e destaque de pedido
"parado" no mesmo status por tempo demais (ver
app/__init__.py:order_stale_level e Tenant.stale_order_alert_minutes).
"""

import json
from datetime import datetime, timedelta, timezone

from flask import g

from app.extensions import db
from app.models import Category, DeliveryType, Order, OrderStatus, PaymentMethod, Product, Tenant


def _create_product(app, price_cents=3000, name="Costela na Brasa"):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        category = Category(tenant_id=tenant.id, name="Carnes", slug="carnes", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(
            tenant_id=tenant.id, category_id=category.id, name=name, slug="costela",
            price_cents=price_cents, is_active=True,
        )
        db.session.add(product)
        db.session.commit()
        return product.id, tenant.id


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


class TestPendingOrdersCountEndpoint:
    def test_returns_pending_count_as_json(self, app, client, login_as):
        product_id, _ = _create_product(app)
        _create_order_via_checkout(client, product_id)
        _create_order_via_checkout(client, product_id, phone="67999990002")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/pedidos/novos/contagem")
        assert resp.status_code == 200
        assert resp.get_json() == {"count": 2, "active_count": 2}

    def test_requires_login(self, client):
        resp = client.get("/painel/pedidos/novos/contagem", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)


class TestSidebarBadge:
    def test_badge_shows_pending_count_and_polling_url(self, app, client, login_as):
        product_id, _ = _create_product(app)
        _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/dashboard")
        html = resp.get_data(as_text=True)
        assert 'id="new-orders-badge"' in html
        assert 'data-new-orders-count-url="/painel/pedidos/novos/contagem"' in html
        # o selo aparece com o número, não escondido, quando há pendentes
        assert 'id="new-orders-badge" class="status-badge status-blocked ms-1" >1<' in html.replace("\n", "").replace("  ", " ")

    def test_badge_hidden_when_no_pending_orders(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/dashboard")
        html = resp.get_data(as_text=True)
        assert 'id="new-orders-badge" class="status-badge status-blocked ms-1" style="display: none;"' in html


class TestOrderStaleLevel:
    def _order(self, app, tenant_id, product_id, minutes_ago, status="preparing"):
        with app.app_context():
            order = Order(
                tenant_id=tenant_id, order_number=1, customer_name="Cliente", customer_phone="67999990001",
                delivery_type=DeliveryType.PICKUP, payment_method=PaymentMethod.CASH,
                status=OrderStatus(status), subtotal_cents=1000, total_cents=1000,
            )
            db.session.add(order)
            db.session.commit()
            order.updated_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            db.session.commit()
            return order.id

    def test_ok_below_threshold(self, app):
        product_id, tenant_id = _create_product(app)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            tenant.stale_order_alert_minutes = 20
            db.session.commit()
        order_id = self._order(app, tenant_id, product_id, minutes_ago=5)

        # Chama o template_global diretamente, simulando o contexto de uma
        # requisição autenticada do lojista (g.tenant já resolvido).
        with app.test_request_context("/painel/pedidos"):
            g.tenant = db.session.get(Tenant, tenant_id)
            order = db.session.get(Order, order_id)
            fn = app.jinja_env.globals["order_stale_level"]
            assert fn(order) == "ok"

    def test_warning_between_threshold_and_double(self, app):
        product_id, tenant_id = _create_product(app)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            tenant.stale_order_alert_minutes = 20
            db.session.commit()
        order_id = self._order(app, tenant_id, product_id, minutes_ago=25)

        with app.test_request_context("/painel/pedidos"):
            g.tenant = db.session.get(Tenant, tenant_id)
            order = db.session.get(Order, order_id)
            fn = app.jinja_env.globals["order_stale_level"]
            assert fn(order) == "warning"

    def test_danger_past_double_threshold(self, app):
        product_id, tenant_id = _create_product(app)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            tenant.stale_order_alert_minutes = 20
            db.session.commit()
        order_id = self._order(app, tenant_id, product_id, minutes_ago=45)

        with app.test_request_context("/painel/pedidos"):
            g.tenant = db.session.get(Tenant, tenant_id)
            order = db.session.get(Order, order_id)
            fn = app.jinja_env.globals["order_stale_level"]
            assert fn(order) == "danger"

    def test_final_status_is_always_ok(self, app):
        product_id, tenant_id = _create_product(app)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            tenant.stale_order_alert_minutes = 20
            db.session.commit()
        order_id = self._order(app, tenant_id, product_id, minutes_ago=999, status="completed")

        with app.test_request_context("/painel/pedidos"):
            g.tenant = db.session.get(Tenant, tenant_id)
            order = db.session.get(Order, order_id)
            fn = app.jinja_env.globals["order_stale_level"]
            assert fn(order) == "ok"

    def test_stale_order_gets_highlighted_in_list_page(self, app, client, login_as):
        product_id, tenant_id = _create_product(app)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            tenant.stale_order_alert_minutes = 20
            db.session.commit()
        self._order(app, tenant_id, product_id, minutes_ago=45)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/pedidos")
        html = resp.get_data(as_text=True)
        assert "ticket-stale-danger" in html
        assert "Parado há muito tempo neste status" in html


class TestStaleOrderMinutesSetting:
    def test_saves_configured_threshold(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/cardapio",
            data={
                "slug": "braseiro-cia", "pickup_enabled": "y", "delivery_enabled": "",
                "show_price_from_label": "y", "notes_placeholder": "", "stale_order_alert_minutes": "35",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.stale_order_alert_minutes == 35

    def test_rejects_out_of_range_value(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/cardapio",
            data={
                "slug": "braseiro-cia", "pickup_enabled": "y", "delivery_enabled": "",
                "show_price_from_label": "y", "notes_placeholder": "", "stale_order_alert_minutes": "9999",
            },
            follow_redirects=True,
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.stale_order_alert_minutes == 20  # não mudou, valor default
