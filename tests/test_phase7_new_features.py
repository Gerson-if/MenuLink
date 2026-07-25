import io
import re

from PIL import Image

from app.extensions import db
from app.models import Banner, ComplementGroup, ComplementOption, Order, Product, Tenant, User


def _csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestSelfRegistration:
    def test_register_creates_trial_tenant_and_logs_in(self, app, client):
        resp = client.get("/cadastro")
        csrf = _csrf(resp.get_data(as_text=True))

        resp = client.post(
            "/cadastro",
            data={
                "owner_name": "Nova Dona", "whatsapp_number": "11988887777",
                "store_name": "Doceria Nova", "slug": "doceria-nova",
                "email": "dona@doceria.com", "password": "senha123",
                "password_confirm": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "criada com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            from app.models import TenantStatus

            tenant = Tenant.query.filter_by(slug="doceria-nova").first()
            assert tenant is not None
            assert tenant.status == TenantStatus.TRIAL

            owner = User.query.filter_by(email="dona@doceria.com").first()
            assert owner is not None
            assert owner.check_password("senha123")

    def test_register_rejects_duplicate_email(self, client):
        resp = client.get("/cadastro")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/cadastro",
            data={
                "owner_name": "X", "whatsapp_number": "11999998888", "store_name": "Y",
                "slug": "loja-y", "email": "contato@braseiroecia.com.br",  # já existe (seed)
                "password": "senha123", "password_confirm": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Já existe uma loja cadastrada" in resp.get_data(as_text=True)

    def test_slug_availability_endpoint(self, client):
        resp = client.get("/cadastro/verificar-slug?slug=braseiro-cia")
        assert "já está em uso" in resp.get_data(as_text=True)

        resp = client.get("/cadastro/verificar-slug?slug=disponivel-123")
        assert "disponível" in resp.get_data(as_text=True)

        resp = client.get("/cadastro/verificar-slug?slug=Com Espaco!")
        assert "minúsculas" in resp.get_data(as_text=True)


class TestPasswordRecovery:
    def test_forgot_password_shows_generic_message_for_unknown_email(self, client):
        resp = client.get("/recuperar-senha")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post("/recuperar-senha", data={"email": "nao-existe@x.com", "csrf_token": csrf}, follow_redirects=True)
        assert "cadastrado" in resp.get_data(as_text=True)

    def test_full_reset_flow_changes_password_and_invalidates_token(self, app, client):
        from app.utils.tokens import generate_reset_token

        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            token = generate_reset_token(user)

        resp = client.get(f"/redefinir-senha/{token}")
        assert resp.status_code == 200
        csrf = _csrf(resp.get_data(as_text=True))

        resp = client.post(
            f"/redefinir-senha/{token}",
            data={"password": "novaSenha999", "confirm_password": "novaSenha999", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "redefinida com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            assert user.check_password("novaSenha999")

        # token não pode ser reutilizado
        resp = client.get(f"/redefinir-senha/{token}", follow_redirects=True)
        assert "inválido ou expirou" in resp.get_data(as_text=True)

    def test_invalid_token_is_rejected(self, client):
        resp = client.get("/redefinir-senha/token-invalido-qualquer", follow_redirects=True)
        assert "inválido ou expirou" in resp.get_data(as_text=True)

    def test_mismatched_passwords_are_rejected(self, app, client):
        from app.utils.tokens import generate_reset_token

        with app.app_context():
            user = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            token = generate_reset_token(user)

        resp = client.get(f"/redefinir-senha/{token}")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            f"/redefinir-senha/{token}",
            data={"password": "senhaUm123", "confirm_password": "senhaDois456", "csrf_token": csrf},
        )
        assert "não coincidem" in resp.get_data(as_text=True)


class TestBanners:
    def test_create_toggle_and_delete_banner(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/banners/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        buf = io.BytesIO()
        Image.new("RGB", (300, 150), (200, 50, 10)).save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            "/painel/banners/novo",
            data={"title": "Promoção", "subtitle": "Só hoje", "link_url": "", "is_active": "y", "csrf_token": csrf, "image": (buf, "b.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert "criado com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            banner = Banner.query.filter_by(title="Promoção").first()
            assert banner is not None
            banner_id = banner.id
            assert banner.is_active is True

        resp = client.post(f"/painel/banners/{banner_id}/alternar", headers={"HX-Request": "true"})
        assert "Inativo" in resp.get_data(as_text=True)

        resp = client.post(f"/painel/banners/{banner_id}/excluir", follow_redirects=True)
        assert "excluído" in resp.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(Banner, banner_id) is None

    def test_active_banner_appears_on_public_menu(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/banners/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        buf = io.BytesIO()
        Image.new("RGB", (300, 150), (10, 50, 200)).save(buf, format="PNG")
        buf.seek(0)
        client.post(
            "/painel/banners/novo",
            data={"title": "Banner Publico Teste", "subtitle": "", "link_url": "", "is_active": "y", "csrf_token": csrf, "image": (buf, "b.png")},
            content_type="multipart/form-data",
        )
        client.get("/logout")

        resp = client.get("/loja/braseiro-cia")
        assert "Banner Publico Teste" in resp.get_data(as_text=True)


class TestComplements:
    def test_create_variation_group_with_option(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/categorias/nova")
        csrf = _csrf(resp.get_data(as_text=True))
        client.post("/painel/categorias/nova", data={"name": "Bebidas", "is_active": "y", "csrf_token": csrf})

        resp = client.get("/painel/produtos/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        client.post("/painel/produtos/novo", data={"name": "Suco Natural", "category_id": "1", "price": "10.00", "is_active": "y", "csrf_token": csrf})

        with app.app_context():
            product_id = Product.query.filter_by(name="Suco Natural").first().id

        resp = client.post(
            f"/painel/produtos/{product_id}/complementos",
            data={"name": "Tamanho", "is_variation": "y", "is_required": "y", "single_choice": "y", "csrf_token": csrf},
            headers={"HX-Request": "true"},
        )
        assert "Tamanho" in resp.get_data(as_text=True)

        with app.app_context():
            group = ComplementGroup.query.filter_by(product_id=product_id).first()
            assert group is not None
            assert group.is_variation is True
            group_id = group.id

        resp = client.post(
            f"/painel/produtos/{product_id}/complementos/{group_id}/opcoes",
            data={"name": "Grande", "extra_price": "4.00", "csrf_token": csrf},
            headers={"HX-Request": "true"},
        )
        assert "Grande" in resp.get_data(as_text=True)

        with app.app_context():
            option = ComplementOption.query.filter_by(group_id=group_id).first()
            assert option.extra_price_cents == 400

        resp = client.post(
            f"/painel/produtos/{product_id}/complementos/opcoes/{option.id}/excluir",
            headers={"HX-Request": "true"},
        )
        with app.app_context():
            assert db.session.get(ComplementOption, option.id) is None

    def test_lojista_cannot_manage_complements_of_other_tenants_product(self, app, client, login_as):
        from app.models import Category, Plan, TenantStatus, UserRole

        with app.app_context():
            plan = Plan.query.first()
            other_tenant = Tenant(name="Outra Loja Comp", slug="outra-loja-comp", email="outra3@x.com", plan_id=plan.id, status=TenantStatus.ACTIVE)
            db.session.add(other_tenant)
            db.session.flush()
            category = Category(tenant_id=other_tenant.id, name="Cat", slug="cat", is_active=True)
            db.session.add(category)
            db.session.flush()
            product = Product(tenant_id=other_tenant.id, category_id=category.id, name="Produto Alheio", slug="produto-alheio", price_cents=1000, is_active=True)
            db.session.add(product)
            db.session.commit()
            other_product_id = product.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            f"/painel/produtos/{other_product_id}/complementos",
            data={"name": "Hack", "is_variation": "", "is_required": "", "single_choice": ""},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404


class TestOrderRevertAndQuickActions:
    def _create_product_and_order(self, app, client, price_cents=2000):
        with app.app_context():
            from app.models import Category

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            category = Category(tenant_id=tenant.id, name="Cat Revert", slug="cat-revert", is_active=True)
            db.session.add(category)
            db.session.flush()
            product = Product(tenant_id=tenant.id, category_id=category.id, name="Prod Revert", slug="prod-revert", price_cents=price_cents, is_active=True)
            db.session.add(product)
            db.session.commit()
            product_id = product.id

        import json

        payload = {
            "customer_name": "Cliente Revert", "customer_phone": "67999992222",
            "delivery_type": "pickup", "payment_method": "cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        }
        resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
        return resp.get_json()["order_id"]

    def test_revert_status_goes_back_one_step(self, app, client, login_as):
        order_id = self._create_product_and_order(app, client)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        client.post(f"/painel/pedidos/{order_id}/status", data={"new_status": "confirmed"})
        client.post(f"/painel/pedidos/{order_id}/status", data={"new_status": "preparing"})

        resp = client.post(f"/painel/pedidos/{order_id}/voltar", follow_redirects=True)
        assert "voltou para" in resp.get_data(as_text=True)

        with app.app_context():
            order = db.session.get(Order, order_id)
            assert order.status.value == "confirmed"

    def test_cannot_revert_pending_order(self, app, client, login_as):
        order_id = self._create_product_and_order(app, client)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get(f"/painel/pedidos/{order_id}")
        assert "Voltar status" not in resp.get_data(as_text=True)

    def test_accept_order_confirms_it(self, app, client, login_as):
        order_id = self._create_product_and_order(app, client)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        client.post(f"/painel/pedidos/{order_id}/aceitar", follow_redirects=True)
        with app.app_context():
            assert db.session.get(Order, order_id).status.value == "confirmed"

    def test_reject_order_cancels_it(self, app, client, login_as):
        order_id = self._create_product_and_order(app, client)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        client.post(f"/painel/pedidos/{order_id}/rejeitar", follow_redirects=True)
        with app.app_context():
            assert db.session.get(Order, order_id).status.value == "canceled"


class TestFreeDeliveryThreshold:
    def test_delivery_fee_waived_above_threshold(self, app, client):
        import json

        from app.models import Category

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.delivery_enabled = True
            tenant.delivery_fee_cents = 700
            tenant.free_delivery_above_cents = 5000
            category = Category(tenant_id=tenant.id, name="Cat Frete", slug="cat-frete", is_active=True)
            db.session.add(category)
            db.session.flush()
            product = Product(tenant_id=tenant.id, category_id=category.id, name="Produto Frete", slug="produto-frete", price_cents=3000, is_active=True)
            db.session.add(product)
            db.session.commit()
            product_id = product.id

        payload = {
            "customer_name": "Ana", "customer_phone": "67999993333", "delivery_type": "delivery",
            "address_street": "Rua", "address_number": "1", "address_neighborhood": "B",
            "payment_method": "cash", "items": [{"product_id": product_id, "quantity": 2}],  # 60 reais
        }
        resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
        data = resp.get_json()
        assert data["total_cents"] == 6000  # sem taxa, pois >= 50

    def test_delivery_fee_applies_below_threshold(self, app, client):
        import json

        from app.models import Category

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.delivery_enabled = True
            tenant.delivery_fee_cents = 700
            tenant.free_delivery_above_cents = 5000
            category = Category(tenant_id=tenant.id, name="Cat Frete2", slug="cat-frete2", is_active=True)
            db.session.add(category)
            db.session.flush()
            product = Product(tenant_id=tenant.id, category_id=category.id, name="Produto Frete2", slug="produto-frete2", price_cents=1000, is_active=True)
            db.session.add(product)
            db.session.commit()
            product_id = product.id

        payload = {
            "customer_name": "Ana", "customer_phone": "67999994444", "delivery_type": "delivery",
            "address_street": "Rua", "address_number": "1", "address_neighborhood": "B",
            "payment_method": "cash", "items": [{"product_id": product_id, "quantity": 1}],  # 10 reais
        }
        resp = client.post("/loja/braseiro-cia/pedido", data=json.dumps(payload), content_type="application/json")
        data = resp.get_json()
        assert data["total_cents"] == 1700  # 10 + taxa de 7


class TestProductCostAndMargin:
    def test_margin_percent_calculation(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            product = Product(tenant_id=tenant.id, name="Prod Margem", slug="prod-margem", price_cents=10000, cost_price_cents=6000, is_active=True)
            db.session.add(product)
            db.session.commit()
            assert product.margin_percent == 40.0

    def test_margin_percent_is_none_without_cost(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            product = Product(tenant_id=tenant.id, name="Prod Sem Custo", slug="prod-sem-custo", price_cents=5000, is_active=True)
            db.session.add(product)
            db.session.commit()
            assert product.margin_percent is None


class TestStoreSettings:
    def test_update_store_info(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/loja")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/painel/configuracoes/loja",
            data={"name": "Braseiro & Cia Renovado", "whatsapp_number": "5567988889999", "phone": "", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "atualizados" in resp.get_data(as_text=True)
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.name == "Braseiro & Cia Renovado"

    def test_menu_settings_requires_at_least_one_receiving_method(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio")
        csrf = _csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/painel/configuracoes/cardapio",
            data={"slug": "braseiro-cia", "csrf_token": csrf},  # nenhum checkbox marcado
            follow_redirects=True,
        )
        assert "Habilite ao menos uma forma" in resp.get_data(as_text=True)

    def test_checkout_settings_update(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/checkout")
        csrf = _csrf(resp.get_data(as_text=True))
        client.post(
            "/painel/configuracoes/checkout",
            data={
                "delivery_fee": "12.50", "free_delivery_above": "80.00", "min_order": "20.00",
                "accept_pix": "y", "accept_card": "y", "accept_cash": "y", "accept_other": "y",
                "csrf_token": csrf,
            },
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.delivery_fee_cents == 1250
            assert tenant.free_delivery_above_cents == 8000
            assert tenant.min_order_cents == 2000
