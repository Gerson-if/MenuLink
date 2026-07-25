import re

from app.extensions import db
from app.models import Category, Tenant


def _csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestPhoneValidation:
    def test_registration_rejects_short_phone(self, client):
        resp = client.get("/cadastro")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/cadastro",
            data={
                "owner_name": "Teste", "whatsapp_number": "123", "store_name": "Loja Teste",
                "slug": "loja-teste-fone", "email": "fone@teste.com", "password": "senha123", "csrf_token": csrf,
            },
        )
        assert "Telefone inválido" in resp.get_data(as_text=True)

    def test_registration_accepts_valid_phone_with_formatting(self, client):
        resp = client.get("/cadastro")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/cadastro",
            data={
                "owner_name": "Teste", "whatsapp_number": "(11) 99999-8888", "store_name": "Loja Fone Ok",
                "slug": "loja-fone-ok", "email": "foneok@teste.com", "password": "senha123",
                "password_confirm": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "criada com sucesso" in resp.get_data(as_text=True)

    def test_checkout_rejects_short_phone(self, app, client):
        import json

        from app.models import Product

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            category = Category(tenant_id=tenant.id, name="Cat Fone", slug="cat-fone", is_active=True)
            db.session.add(category)
            db.session.flush()
            product = Product(tenant_id=tenant.id, category_id=category.id, name="Prod Fone", slug="prod-fone", price_cents=1000, is_active=True)
            db.session.add(product)
            db.session.commit()
            product_id = product.id

        payload = {
            "customer_name": "Cliente", "customer_phone": "123",
            "delivery_type": "pickup", "payment_method": "cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        }
        resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 400


class TestBlankTextValidation:
    def test_category_name_of_only_spaces_is_rejected(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/categorias/nova")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post("/painel/categorias/nova", data={"name": "   ", "is_active": "y", "csrf_token": csrf})
        # DataRequired do WTForms já trata string só de espaço como campo
        # vazio e usa sua própria mensagem — nosso `not_blank` funciona como
        # camada extra de defesa para os poucos casos em que isso não se
        # aplicaria (ex: campo Optional preenchido só com espaços).
        assert "Informe o nome" in resp.get_data(as_text=True)

    def test_product_name_of_only_spaces_is_rejected(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/produtos/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/painel/produtos/novo",
            data={"name": "     ", "category_id": "0", "price": "10.00", "is_active": "y", "csrf_token": csrf},
        )
        assert "Informe o nome" in resp.get_data(as_text=True)


class TestSlugValidation:
    def test_menu_settings_rejects_invalid_slug_characters(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/painel/configuracoes/cardapio",
            data={"slug": "Loja Com Espaço!!", "pickup_enabled": "y", "csrf_token": csrf},
        )
        assert "letras minúsculas" in resp.get_data(as_text=True)


class TestCostPriceValidation:
    def test_cost_price_greater_than_sale_price_is_rejected(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/produtos/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/painel/produtos/novo",
            data={
                "name": "Produto Custo Invertido", "category_id": "0",
                "price": "10.00", "cost_price": "50.00", "is_active": "y", "csrf_token": csrf,
            },
        )
        assert "não pode ser maior que o preço de venda" in resp.get_data(as_text=True)
