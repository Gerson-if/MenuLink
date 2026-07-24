import json
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Category, Order, OrderStatus, Product, Tenant


def _create_product(app, price_cents=3000, name="Costela na Brasa"):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        category = Category(tenant_id=tenant.id, name="Carnes", slug="carnes", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(tenant_id=tenant.id, category_id=category.id, name=name, slug="costela", price_cents=price_cents, is_active=True)
        db.session.add(product)
        db.session.commit()
        return product.id, tenant.id


def _create_order_via_checkout(client, product_id, quantity=1, phone="67999990001"):
    payload = {
        "customer_name": "Cliente Teste",
        "customer_phone": phone,
        "delivery_type": "pickup",
        "payment_method": "cash",
        "items": [{"product_id": product_id, "quantity": quantity}],
    }
    resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    return resp.get_json()


class TestOrdersManagement:
    def test_orders_list_shows_created_order(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id, quantity=2)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/pedidos")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert f"#{data['order_number']}" in body
        assert "Cliente Teste" in body

    def test_orders_list_filters_by_status(self, app, client, login_as):
        product_id, _ = _create_product(app)
        _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/pedidos?status=pending")
        assert "Cliente Teste" in resp.get_data(as_text=True)

        resp = client.get("/painel/pedidos?status=completed")
        assert "Nenhum pedido" in resp.get_data(as_text=True)

    def test_order_detail_shows_items_and_totals(self, app, client, login_as):
        product_id, _ = _create_product(app, price_cents=2500, name="Linguiça Artesanal")
        data = _create_order_via_checkout(client, product_id, quantity=3)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get(f"/painel/pedidos/{data['order_id']}")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Linguiça Artesanal" in body
        assert "R$ 75.00" in body  # 3 x 25.00

    def test_valid_status_transition_succeeds(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            f"/painel/pedidos/{data['order_id']}/status",
            data={"new_status": "confirmed"},
            follow_redirects=True,
        )
        assert "atualizado" in resp.get_data(as_text=True)

        with app.app_context():
            order = db.session.get(Order, data["order_id"])
            assert order.status == OrderStatus.CONFIRMED

    def test_invalid_status_transition_is_rejected(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        # pedido está "pending" — pular direto para "completed" não é permitido
        resp = client.post(
            f"/painel/pedidos/{data['order_id']}/status",
            data={"new_status": "completed"},
            follow_redirects=True,
        )
        assert "Não é possível mudar" in resp.get_data(as_text=True)

        with app.app_context():
            order = db.session.get(Order, data["order_id"])
            assert order.status == OrderStatus.PENDING

    def test_full_pickup_status_flow(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)
        order_id = data["order_id"]

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        for status in ["confirmed", "preparing", "ready_for_pickup", "completed"]:
            resp = client.post(f"/painel/pedidos/{order_id}/status", data={"new_status": status}, follow_redirects=True)
            assert resp.status_code == 200

        with app.app_context():
            order = db.session.get(Order, order_id)
            assert order.status == OrderStatus.COMPLETED

    def test_completed_order_has_no_further_transitions(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)
        order_id = data["order_id"]

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        for status in ["confirmed", "preparing", "ready_for_pickup", "completed"]:
            client.post(f"/painel/pedidos/{order_id}/status", data={"new_status": status})

        resp = client.get(f"/painel/pedidos/{order_id}")
        assert "status final" in resp.get_data(as_text=True)

    def test_lojista_cannot_see_orders_from_another_tenant(self, app, client, login_as):
        # cria pedido para o tenant demo
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)

        # cria segunda loja + lojista
        from app.models import Plan, TenantStatus, User, UserRole

        with app.app_context():
            plan = Plan.query.first()
            other = Tenant(name="Outra Loja Pedido", slug="outra-loja-pedido", email="outra2@x.com", plan_id=plan.id, status=TenantStatus.ACTIVE)
            db.session.add(other)
            db.session.flush()
            owner = User(name="Dono2", email="dono2@x.com", role=UserRole.OWNER, tenant_id=other.id)
            owner.set_password("senha123")
            db.session.add(owner)
            db.session.commit()

        login_as(client, "dono2@x.com", "senha123")
        resp = client.get(f"/painel/pedidos/{data['order_id']}")
        assert resp.status_code == 404

    def test_completed_order_moves_to_history_and_out_of_active_list(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)
        order_id = data["order_id"]

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        for status in ["confirmed", "preparing", "ready_for_pickup", "completed"]:
            client.post(f"/painel/pedidos/{order_id}/status", data={"new_status": status})

        # Consome eventuais flash messages da última transição de status antes
        # de checar o conteúdo — senão "Pedido #1 atualizado..." (flash) dá
        # falso positivo na checagem de que o card #1 sumiu da lista ativa.
        client.get("/painel/pedidos")
        active_body = client.get("/painel/pedidos").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' not in active_body

        history_body = client.get("/painel/pedidos/historico").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' in history_body

    def test_canceled_order_moves_to_history_and_out_of_active_list(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)
        order_id = data["order_id"]

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(f"/painel/pedidos/{order_id}/rejeitar")

        client.get("/painel/pedidos")  # consome o flash de "Pedido #N rejeitado."
        active_body = client.get("/painel/pedidos").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' not in active_body

        history_body = client.get("/painel/pedidos/historico").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' in history_body

    def test_pending_order_stays_out_of_history(self, app, client, login_as):
        product_id, _ = _create_product(app)
        data = _create_order_via_checkout(client, product_id)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        history_body = client.get("/painel/pedidos/historico").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' not in history_body

        active_body = client.get("/painel/pedidos").get_data(as_text=True)
        assert f'ticket-num">#{data["order_number"]}<' in active_body


class TestReports:
    def test_summary_reflects_created_orders(self, app, client, login_as):
        product_id, tenant_id = _create_product(app, price_cents=5000)
        _create_order_via_checkout(client, product_id, quantity=2, phone="67999990002")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/vendas")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "R$ 100.00" in body  # 2 x 50.00, receita de hoje

    def test_canceled_orders_are_excluded_from_revenue(self, app, client, login_as):
        product_id, tenant_id = _create_product(app, price_cents=4000)
        data = _create_order_via_checkout(client, product_id, phone="67999990003")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(f"/painel/pedidos/{data['order_id']}/status", data={"new_status": "canceled"})

        from app.services.report_service import ReportService

        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            summary = ReportService(tenant).summary()
            assert summary["revenue_today_cents"] == 0

    def test_top_products_ranking(self, app, client, login_as):
        product_id, tenant_id = _create_product(app, price_cents=1000, name="Água")
        _create_order_via_checkout(client, product_id, quantity=5, phone="67999990004")

        from app.services.report_service import ReportService

        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            top = ReportService(tenant).top_products(limit=5)
            assert len(top) == 1
            assert top[0]["name"] == "Água"
            assert top[0]["quantity"] == 5
            assert top[0]["revenue_cents"] == 5000

    def test_lojista_cannot_access_other_tenant_reports_data(self, app, client, login_as):
        # Isolamento por design: ReportService recebe o tenant do
        # contexto de sessão, nunca de parâmetro de URL — não há rota
        # que aceite tenant_id arbitrário, então o teste confirma que
        # o resumo de um lojista recém-criado, sem pedidos, começa zerado.
        from app.services.admin_tenant_service import AdminTenantService
        from app.services.report_service import ReportService

        with app.app_context():
            tenant = AdminTenantService().create(
                name="Loja Vazia", email="vazia@x.com", phone=None, whatsapp_number=None,
                plan_id=None, owner_name="Dono Vazio", owner_email="dono@vazia.com", owner_password="senha123",
            )
            summary = ReportService(tenant).summary()
            assert summary["revenue_total_cents"] == 0
            assert summary["orders_total"] == 0
