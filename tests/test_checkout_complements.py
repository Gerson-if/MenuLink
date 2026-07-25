import json

from app.extensions import db
from app.models import Category, ComplementGroup, ComplementOption, Order, Product, Tenant
from app.services.complement_service import ComplementService


def _create_product_with_complements(app):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        tenant.whatsapp_number = "5567999998888"
        category = Category(tenant_id=tenant.id, name="Espetos", slug="espetos-cx", is_active=True)
        db.session.add(category)
        db.session.flush()

        product = Product(
            tenant_id=tenant.id, category_id=category.id, name="Espeto Misto",
            slug="espeto-misto-cx", price_cents=2000, is_active=True,
        )
        db.session.add(product)
        db.session.flush()

        size_group = ComplementGroup(
            tenant_id=tenant.id, product_id=product.id, name="Tamanho",
            is_variation=True, is_required=True, single_choice=True, display_order=0,
        )
        sauce_group = ComplementGroup(
            tenant_id=tenant.id, product_id=product.id, name="Molhos",
            is_variation=False, is_required=False, single_choice=False, display_order=1,
        )
        drinks_group = ComplementGroup(
            tenant_id=tenant.id, product_id=product.id, name="Bebidas",
            is_variation=False, is_required=False, single_choice=False, allow_quantity=True, display_order=2,
        )
        db.session.add_all([size_group, sauce_group, drinks_group])
        db.session.flush()

        big = ComplementOption(tenant_id=tenant.id, group_id=size_group.id, name="Grande", extra_price_cents=500, display_order=0)
        small = ComplementOption(tenant_id=tenant.id, group_id=size_group.id, name="Pequeno", extra_price_cents=0, display_order=1)
        bbq = ComplementOption(tenant_id=tenant.id, group_id=sauce_group.id, name="Barbecue", extra_price_cents=200, display_order=0)
        coke = ComplementOption(tenant_id=tenant.id, group_id=drinks_group.id, name="Coca-Cola 2L", extra_price_cents=600, display_order=0)
        db.session.add_all([big, small, bbq, coke])
        db.session.commit()

        return {
            "product_id": product.id, "tenant_id": tenant.id,
            "size_group_id": size_group.id, "sauce_group_id": sauce_group.id, "drinks_group_id": drinks_group.id,
            "big_id": big.id, "small_id": small.id, "bbq_id": bbq.id, "coke_id": coke.id,
        }


def _checkout(client, product_id, option_ids, quantity=1, phone="67999991234", options=None):
    """`option_ids`: lista simples de ids, cada um com quantidade 1 (caso
    comum). Pra testar quantidade por opção, passe `options` diretamente
    como lista de {"option_id": ..., "quantity": ...} (tem prioridade
    sobre `option_ids`)."""
    resolved_options = options if options is not None else [{"option_id": oid, "quantity": 1} for oid in option_ids]
    payload = {
        "customer_name": "Cliente Complementos", "customer_phone": phone,
        "delivery_type": "pickup", "payment_method": "pix",
        "items": [{"product_id": product_id, "quantity": quantity, "options": resolved_options}],
    }
    return client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")


class TestComplementsInCheckout:
    def test_menu_data_exposes_complement_groups(self, app, client):
        ids = _create_product_with_complements(app)
        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        assert "Tamanho" in html
        assert "Molhos" in html
        assert "Grande" in html

    def test_required_group_without_selection_is_rejected(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [])
        assert resp.status_code == 400
        assert "Tamanho" in resp.get_json()["error"]

    def test_valid_selection_computes_correct_price(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [ids["big_id"], ids["bbq_id"]], quantity=2)
        assert resp.status_code == 201
        data = resp.get_json()
        # (2000 base + 500 tamanho + 200 molho) * 2 = 5400
        assert data["total_cents"] == 5400

        with app.app_context():
            order = db.session.get(Order, data["order_id"])
            item = order.items[0]
            assert item.unit_price_cents == 2700
            choice_pairs = {(c.group_name, c.option_name) for c in item.choices}
            assert ("Tamanho", "Grande") in choice_pairs
            assert ("Molhos", "Barbecue") in choice_pairs

    def test_single_choice_group_rejects_two_selections(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [ids["big_id"], ids["small_id"]])
        assert resp.status_code == 400
        assert "uma opção" in resp.get_json()["error"]

    def test_nonexistent_option_id_is_rejected(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [999999])
        assert resp.status_code == 400

    def test_option_from_another_tenant_is_rejected(self, app, client):
        ids = _create_product_with_complements(app)

        with app.app_context():
            from app.models import Plan, TenantStatus

            plan = Plan.query.first()
            other_tenant = Tenant(name="Outra Loja Cx", slug="outra-loja-cx", email="outracx@x.com", plan_id=plan.id, status=TenantStatus.ACTIVE)
            db.session.add(other_tenant)
            db.session.flush()
            other_category = Category(tenant_id=other_tenant.id, name="Cat", slug="cat-cx", is_active=True)
            db.session.add(other_category)
            db.session.flush()
            other_product = Product(tenant_id=other_tenant.id, category_id=other_category.id, name="Produto Outro", slug="produto-outro-cx", price_cents=1000, is_active=True)
            db.session.add(other_product)
            db.session.flush()
            other_group = ComplementGroup(tenant_id=other_tenant.id, product_id=other_product.id, name="G", is_variation=False, is_required=False, single_choice=False)
            db.session.add(other_group)
            db.session.flush()
            other_option = ComplementOption(tenant_id=other_tenant.id, group_id=other_group.id, name="Barato", extra_price_cents=0)
            db.session.add(other_option)
            db.session.commit()
            other_option_id = other_option.id

        # tenta comprar o "Espeto Misto" da loja braseiro-cia usando o id
        # de uma opção que pertence a um produto de outra loja
        resp = _checkout(client, ids["product_id"], [other_option_id])
        assert resp.status_code == 400

    def test_optional_group_can_be_skipped(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [ids["small_id"]])  # só o obrigatório, sem molho
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["total_cents"] == 2000  # base + 0 (pequeno) + nenhum molho

    def test_whatsapp_message_includes_choices(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(client, ids["product_id"], [ids["big_id"], ids["bbq_id"]])
        data = resp.get_json()

        import urllib.parse

        message = urllib.parse.unquote(data["whatsapp_url"].split("text=")[1])
        assert "Tamanho: Grande" in message
        assert "Molhos: Barbecue" in message

    def test_inactive_option_is_treated_as_unavailable(self, app, client):
        ids = _create_product_with_complements(app)
        with app.app_context():
            option = db.session.get(ComplementOption, ids["bbq_id"])
            option.is_active = False
            db.session.commit()

        resp = _checkout(client, ids["product_id"], [ids["big_id"], ids["bbq_id"]])
        assert resp.status_code == 400


class TestQuantityPerOption:
    """allow_quantity — ver ComplementGroup.allow_quantity: em grupos
    normais cada opção só pode ser escolhida com quantidade 1; em
    grupos com allow_quantity o cliente escolhe quantas unidades quer,
    e esse preço NÃO escala com a quantidade do item (é somado uma
    única vez por linha do carrinho — ver order_service._resolve_items)."""

    def test_quantity_greater_than_one_rejected_in_normal_group(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(
            client, ids["product_id"], None,
            options=[
                {"option_id": ids["small_id"], "quantity": 1},
                {"option_id": ids["bbq_id"], "quantity": 2},
            ],
        )
        assert resp.status_code == 400
        assert "Molhos" in resp.get_json()["error"]

    def test_drink_quantity_does_not_scale_with_item_quantity(self, app, client):
        """O bug reportado: 5 lanches + 1 Coca 2L (R$6) tinha virado
        5 lanches + 5 Cocas. Preço correto: 5x(2000+500 tamanho) + 1x600."""
        ids = _create_product_with_complements(app)
        resp = _checkout(
            client, ids["product_id"], None, quantity=5,
            options=[
                {"option_id": ids["small_id"], "quantity": 1},
                {"option_id": ids["coke_id"], "quantity": 1},
            ],
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["total_cents"] == 5 * 2000 + 600

        with app.app_context():
            order = db.session.get(Order, data["order_id"])
            item = order.items[0]
            assert item.unit_price_cents == 2000  # sem o refrigerante embutido
            assert item.subtotal_cents == 5 * 2000 + 600
            coke_choice = next(c for c in item.choices if c.option_name == "Coca-Cola 2L")
            assert coke_choice.quantity == 1

    def test_two_units_of_the_same_drink(self, app, client):
        ids = _create_product_with_complements(app)
        resp = _checkout(
            client, ids["product_id"], None, quantity=3,
            options=[
                {"option_id": ids["small_id"], "quantity": 1},
                {"option_id": ids["coke_id"], "quantity": 2},
            ],
        )
        assert resp.status_code == 201
        data = resp.get_json()
        # 3 lanches (sem extra por unidade) + 2 Cocas de R$6
        assert data["total_cents"] == 3 * 2000 + 2 * 600

        with app.app_context():
            order = db.session.get(Order, data["order_id"])
            coke_choice = next(c for c in order.items[0].choices if c.option_name == "Coca-Cola 2L")
            assert coke_choice.quantity == 2

    def test_per_unit_complement_still_scales_with_item_quantity(self, app, client):
        """Comportamento existente preservado: molho (grupo normal) é
        por unidade do produto — 4 lanches com barbecue cobram o molho
        4 vezes."""
        ids = _create_product_with_complements(app)
        resp = _checkout(
            client, ids["product_id"], [ids["small_id"], ids["bbq_id"]], quantity=4,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["total_cents"] == 4 * (2000 + 200)


class TestComplementServiceAllowQuantity:
    def _tenant_and_product(self, app):
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        category = Category(tenant_id=tenant.id, name="Cat Svc", slug="cat-svc-cx", is_active=True)
        db.session.add(category)
        db.session.flush()
        product = Product(tenant_id=tenant.id, category_id=category.id, name="P Svc", slug="p-svc-cx", price_cents=1000, is_active=True)
        db.session.add(product)
        db.session.flush()
        return tenant, product

    def test_allow_quantity_kept_on_multi_choice_complement_group(self, app):
        with app.app_context():
            tenant, product = self._tenant_and_product(app)
            group = ComplementService(tenant).create_group(
                product, name="Bebidas", is_variation=False, is_required=False,
                single_choice=False, allow_quantity=True,
            )
            assert group.allow_quantity is True

    def test_allow_quantity_forced_false_on_variation(self, app):
        with app.app_context():
            tenant, product = self._tenant_and_product(app)
            group = ComplementService(tenant).create_group(
                product, name="Tamanho", is_variation=True, is_required=True,
                single_choice=False, allow_quantity=True,
            )
            assert group.allow_quantity is False
            assert group.single_choice is True  # variação sempre força escolha única, já era assim

    def test_allow_quantity_forced_false_on_single_choice(self, app):
        with app.app_context():
            tenant, product = self._tenant_and_product(app)
            group = ComplementService(tenant).create_group(
                product, name="Ponto da carne", is_variation=False, is_required=True,
                single_choice=True, allow_quantity=True,
            )
            assert group.allow_quantity is False
