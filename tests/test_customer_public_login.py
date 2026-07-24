import json

from app.extensions import db
from app.models import Category, Product, Tenant


def _create_product_and_order(app, client, tenant_slug="braseiro-cia", phone="67999990000"):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug=tenant_slug).first()
        category = Category(tenant_id=tenant.id, name="Espetos", slug="espetos-login-test", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(
            tenant_id=tenant.id, category_id=category.id, name="Espeto Teste Login",
            slug="espeto-teste-login", price_cents=3000, is_active=True,
        )
        db.session.add(product)
        db.session.commit()
        product_id = product.id

    payload = {
        "customer_name": "Cliente Teste", "customer_phone": phone,
        "delivery_type": "pickup", "payment_method": "pix",
        "items": [{"product_id": product_id, "quantity": 2}],
    }
    resp = client.post(f"/loja/{tenant_slug}/pedido", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    return resp.get_json()


class TestCustomerLogin:
    def test_login_with_existing_phone_returns_orders(self, app, client):
        _create_product_and_order(app, client, phone="67999990001")

        resp = client.post(
            "/loja/braseiro-cia/entrar",
            data=json.dumps({"phone": "67999990001"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Cliente Teste"

        resp = client.get("/loja/braseiro-cia/meus-pedidos")
        data = resp.get_json()
        assert data["logged_in"] is True
        assert len(data["orders"]) == 1
        assert data["orders"][0]["total_cents"] == 6000

    def test_login_with_unknown_phone_is_rejected_without_creating_customer(self, app, client):
        resp = client.post(
            "/loja/braseiro-cia/entrar",
            data=json.dumps({"phone": "67900000000"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

        with app.app_context():
            from app.models import Customer

            assert Customer.query.filter_by(phone="67900000000").first() is None

    def test_logout_clears_session(self, app, client):
        _create_product_and_order(app, client, phone="67999990002")
        client.post("/loja/braseiro-cia/entrar", data=json.dumps({"phone": "67999990002"}), content_type="application/json")

        client.post("/loja/braseiro-cia/sair")

        resp = client.get("/loja/braseiro-cia/meus-pedidos")
        assert resp.get_json()["logged_in"] is False

    def test_session_does_not_leak_across_tenants(self, app, client):
        _create_product_and_order(app, client, tenant_slug="braseiro-cia", phone="67999990003")
        client.post("/loja/braseiro-cia/entrar", data=json.dumps({"phone": "67999990003"}), content_type="application/json")

        with app.app_context():
            other_tenant = Tenant.query.filter(Tenant.slug != "braseiro-cia").first()

        if other_tenant is not None:
            resp = client.get(f"/loja/{other_tenant.slug}/meus-pedidos")
            assert resp.get_json()["logged_in"] is False


class TestCustomerSelfService:
    """
    'Meus Dados': o cliente pode editar nome/telefone ou remover o próprio
    cadastro direto pelo cardápio público, sem precisar pedir pro lojista.
    """

    def test_customer_can_update_own_name_and_phone(self, app, client):
        _create_product_and_order(app, client, phone="67999990010")
        client.post("/loja/braseiro-cia/entrar", data=json.dumps({"phone": "67999990010"}), content_type="application/json")

        resp = client.post(
            "/loja/braseiro-cia/meus-dados",
            data=json.dumps({"name": "Cliente Renomeado", "phone": "67999990011"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Cliente Renomeado"

        # a sessão segue válida com o telefone novo (login "migra" junto)
        resp = client.get("/loja/braseiro-cia/meus-pedidos")
        data = resp.get_json()
        assert data["logged_in"] is True
        assert data["name"] == "Cliente Renomeado"

    def test_update_profile_requires_login(self, client):
        resp = client.post(
            "/loja/braseiro-cia/meus-dados",
            data=json.dumps({"name": "Sem Login", "phone": "67999990012"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_update_profile_rejects_phone_already_used_by_another_customer(self, app, client):
        data = _create_product_and_order(app, client, phone="67999990020")

        with app.app_context():
            from app.models import Order

            order = db.session.get(Order, data["order_id"])
            product_id = order.items[0].product_id

        payload = {
            "customer_name": "Outro Cliente", "customer_phone": "67999990021",
            "delivery_type": "pickup", "payment_method": "pix",
            "items": [{"product_id": product_id, "quantity": 1}],
        }
        resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 201

        client.post("/loja/braseiro-cia/entrar", data=json.dumps({"phone": "67999990020"}), content_type="application/json")

        resp = client.post(
            "/loja/braseiro-cia/meus-dados",
            data=json.dumps({"name": "Cliente Teste", "phone": "67999990021"}),
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_customer_can_delete_own_account(self, app, client):
        _create_product_and_order(app, client, phone="67999990030")
        client.post("/loja/braseiro-cia/entrar", data=json.dumps({"phone": "67999990030"}), content_type="application/json")

        resp = client.post("/loja/braseiro-cia/conta/remover")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        with app.app_context():
            from app.models import Customer

            assert Customer.query.filter_by(phone="67999990030").first() is None

        # sessão foi encerrada junto
        resp = client.get("/loja/braseiro-cia/meus-pedidos")
        assert resp.get_json()["logged_in"] is False

    def test_delete_account_requires_login(self, client):
        resp = client.post("/loja/braseiro-cia/conta/remover")
        assert resp.status_code == 401
