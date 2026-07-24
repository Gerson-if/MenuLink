import json

from app.extensions import db
from app.models import Category, Customer, Order, Product, Tenant


def _create_product(app, name="Espeto"):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        category = Category(tenant_id=tenant.id, name="Espetos", slug="espetos-cli-test", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(tenant_id=tenant.id, category_id=category.id, name=name, slug="espeto-cli-test", price_cents=2500, is_active=True)
        db.session.add(product)
        db.session.commit()
        return product.id


def _create_order(client, product_id, phone, name="Cliente Teste", quantity=1):
    payload = {
        "customer_name": name, "customer_phone": phone,
        "delivery_type": "pickup", "payment_method": "pix",
        "items": [{"product_id": product_id, "quantity": quantity}],
    }
    resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    return resp.get_json()


class TestLojistaCustomersManagement:
    def test_customers_list_shows_order_stats(self, app, client, login_as):
        product_id = _create_product(app)
        _create_order(client, product_id, phone="67999991111", name="Ana")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/clientes")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Ana" in body

    def test_top_customer_badge_shown_for_highest_spender(self, app, client, login_as):
        product_id = _create_product(app)
        _create_order(client, product_id, phone="67999992222", name="Cliente Grande", quantity=5)
        _create_order(client, product_id, phone="67999993333", name="Cliente Pequeno", quantity=1)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/clientes?sort=spent")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Top cliente" in body
        # Garante que o maior gastador aparece antes do menor na ordenação por gasto
        assert body.index("Cliente Grande") < body.index("Cliente Pequeno")

    def test_lojista_can_remove_customer(self, app, client, login_as):
        product_id = _create_product(app)
        _create_order(client, product_id, phone="67999994444", name="Cliente Removivel")

        with app.app_context():
            from app.utils.phone import normalize_br_phone

            customer = Customer.query.filter_by(phone=normalize_br_phone("67999994444")).first()
            customer_id = customer.id
            order = Order.query.filter_by(customer_id=customer_id).first()
            order_id = order.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(f"/painel/clientes/{customer_id}/remover", follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assert db.session.get(Customer, customer_id) is None
            # o pedido continua existindo no histórico, só perde o vínculo
            remaining_order = db.session.get(Order, order_id)
            assert remaining_order is not None
            assert remaining_order.customer_id is None

    def test_lojista_cannot_remove_customer_from_another_tenant(self, app, client, login_as):
        product_id = _create_product(app)
        _create_order(client, product_id, phone="67999995555", name="Cliente Outra Loja")

        from app.models import Plan, TenantStatus, User, UserRole

        with app.app_context():
            from app.utils.phone import normalize_br_phone

            customer = Customer.query.filter_by(phone=normalize_br_phone("67999995555")).first()
            customer_id = customer.id
            plan = Plan.query.first()
            other = Tenant(name="Outra Loja Cli", slug="outra-loja-cli", email="outracli@x.com", plan_id=plan.id, status=TenantStatus.ACTIVE)
            db.session.add(other)
            db.session.flush()
            owner = User(name="Dono3", email="dono3@x.com", role=UserRole.OWNER, tenant_id=other.id)
            owner.set_password("senha123")
            db.session.add(owner)
            db.session.commit()

        login_as(client, "dono3@x.com", "senha123")
        resp = client.post(f"/painel/clientes/{customer_id}/remover")
        assert resp.status_code == 404

        with app.app_context():
            assert db.session.get(Customer, customer_id) is not None
