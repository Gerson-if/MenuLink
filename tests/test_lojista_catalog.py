import io

from PIL import Image

from app.extensions import db
from app.models import Plan, Tenant, TenantStatus, User, UserRole, BillingCycle


def _make_test_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), color=(200, 50, 10)).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class TestCategoryCRUD:
    def test_create_and_list_category(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/categorias/nova")
        assert resp.status_code == 200

        resp = client.post(
            "/painel/categorias/nova",
            data={"name": "Espetos", "is_active": "y"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Categoria criada com sucesso" in body

        resp = client.get("/painel/categorias")
        assert "Espetos" in resp.get_data(as_text=True)

    def test_edit_category(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Bebidas", "is_active": "y"})

        from app.models import Category

        category = Category.query.filter_by(name="Bebidas").first()
        resp = client.post(
            f"/painel/categorias/{category.id}/editar",
            data={"name": "Bebidas Geladas", "is_active": "y"},
            follow_redirects=True,
        )
        assert "Bebidas Geladas" in resp.get_data(as_text=True)

    def test_delete_category_does_not_delete_products(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Sobremesas", "is_active": "y"})

        from app.models import Category, Product

        with app.app_context():
            category = Category.query.filter_by(name="Sobremesas").first()
            category_id = category.id
            tenant = category.tenant
            product = Product(
                tenant_id=tenant.id, category_id=category.id, name="Pudim",
                slug="pudim", price_cents=1500,
            )
            db.session.add(product)
            db.session.commit()
            product_id = product.id

        client.post(f"/painel/categorias/{category_id}/excluir", follow_redirects=True)

        with app.app_context():
            product = db.session.get(Product, product_id)
            assert product is not None
            assert product.category_id is None


class TestProductCRUD:
    def test_create_product(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.post(
            "/painel/produtos/novo",
            data={"name": "Picanha na Brasa", "category_id": "0", "price": "89.90", "description": "Suculenta", "is_active": "y"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Picanha na Brasa" in resp.get_data(as_text=True)

    def test_product_price_stored_in_cents(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Suco Natural", "category_id": "0", "price": "12.50", "is_active": "y"},
        )
        from app.models import Product

        with app.app_context():
            product = Product.query.filter_by(name="Suco Natural").first()
            assert product.price_cents == 1250

    def test_upload_image_sets_as_primary_and_can_be_replaced(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Costela", "category_id": "0", "price": "70.00", "is_active": "y"},
        )
        from app.models import Product

        with app.app_context():
            product = Product.query.filter_by(name="Costela").first()
            product_id = product.id

        img_bytes = _make_test_image_bytes()
        resp = client.post(
            f"/painel/produtos/{product_id}/imagens",
            data={"image": (io.BytesIO(img_bytes), "foto1.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert "Imagem enviada com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            from app.models import ProductImage

            images = ProductImage.query.filter_by(product_id=product_id).all()
            assert len(images) == 1
            assert images[0].is_primary is True

        # segunda imagem não deve virar principal automaticamente
        resp = client.post(
            f"/painel/produtos/{product_id}/imagens",
            data={"image": (io.BytesIO(_make_test_image_bytes()), "foto2.png")},
            content_type="multipart/form-data",
        )
        with app.app_context():
            from app.models import ProductImage

            images = ProductImage.query.filter_by(product_id=product_id).order_by(ProductImage.display_order).all()
            assert len(images) == 2
            assert images[0].is_primary is True
            assert images[1].is_primary is False

            # tornar a segunda principal
            second_id = images[1].id

        client.post(f"/painel/produtos/{product_id}/imagens/{second_id}/principal")
        with app.app_context():
            from app.models import ProductImage

            images = ProductImage.query.filter_by(product_id=product_id).order_by(ProductImage.display_order).all()
            primary_flags = {img.id: img.is_primary for img in images}
            assert primary_flags[second_id] is True
            assert sum(primary_flags.values()) == 1  # só uma principal

    def test_replace_image_keeps_same_record_but_new_file(self, app, client, login_as):
        # Clicar numa foto já enviada troca o ARQUIVO no lugar (mesmo
        # registro, mesma posição/flag de principal) — ver
        # ProductService.replace_image e o botão "Trocar foto" em
        # _image_gallery.html.
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Frango Assado", "category_id": "0", "price": "45.00", "is_active": "y"},
        )
        from app.models import Product, ProductImage

        with app.app_context():
            product_id = Product.query.filter_by(name="Frango Assado").first().id

        client.post(
            f"/painel/produtos/{product_id}/imagens",
            data={"image": (io.BytesIO(_make_test_image_bytes()), "original.png")},
            content_type="multipart/form-data",
        )
        with app.app_context():
            image = ProductImage.query.filter_by(product_id=product_id).first()
            image_id = image.id
            original_path = image.file_path
            assert image.is_primary is True

        resp = client.post(
            f"/painel/produtos/{product_id}/imagens/{image_id}/substituir",
            data={"image": (io.BytesIO(_make_test_image_bytes()), "nova-foto.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert "Foto atualizada" in resp.get_data(as_text=True)

        with app.app_context():
            images = ProductImage.query.filter_by(product_id=product_id).all()
            assert len(images) == 1  # continua sendo o MESMO registro, não um novo
            assert images[0].id == image_id
            assert images[0].is_primary is True  # continua principal
            assert images[0].file_path != original_path  # mas o arquivo mudou

    def test_replace_image_of_another_tenant_is_404(self, app, client, login_as):
        from app.models import Product, ProductImage

        with app.app_context():
            plan = Plan.query.first()
            other_tenant = Tenant(
                name="Outra Loja", slug="outra-loja-img", email="outra-img@example.com",
                plan_id=plan.id, status=TenantStatus.ACTIVE,
            )
            db.session.add(other_tenant)
            db.session.commit()
            other_product = Product(
                tenant_id=other_tenant.id, name="Produto Alheio", slug="produto-alheio",
                price_cents=1000, is_active=True,
            )
            db.session.add(other_product)
            db.session.commit()
            other_image = ProductImage(
                tenant_id=other_tenant.id, product_id=other_product.id,
                file_path="uploads/tenant_x/products/1/fake.webp", is_primary=True, display_order=0,
            )
            db.session.add(other_image)
            db.session.commit()
            other_product_id = other_product.id
            other_image_id = other_image.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            f"/painel/produtos/{other_product_id}/imagens/{other_image_id}/substituir",
            data={"image": (io.BytesIO(_make_test_image_bytes()), "hack.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404

    def test_invalid_image_is_rejected(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Linguiça", "category_id": "0", "price": "25.00", "is_active": "y"},
        )
        from app.models import Product

        with app.app_context():
            product_id = Product.query.filter_by(name="Linguiça").first().id

        fake_file = io.BytesIO(b"isso claramente nao e uma imagem")
        resp = client.post(
            f"/painel/produtos/{product_id}/imagens",
            data={"image": (fake_file, "malicioso.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "não é uma imagem válida" in resp.get_data(as_text=True)


class TestMultiTenantIsolation:
    def test_lojista_cannot_see_or_edit_other_tenant_category(self, app, client, login_as):
        # cria uma segunda loja com sua própria categoria
        with app.app_context():
            plan = Plan.query.first()
            other_tenant = Tenant(
                name="Pizzaria do Zé", slug="pizzaria-do-ze", email="ze@pizzaria.com",
                plan_id=plan.id, status=TenantStatus.ACTIVE,
            )
            db.session.add(other_tenant)
            db.session.flush()

            other_owner = User(name="Zé", email="ze@pizzaria.com.br", role=UserRole.OWNER, tenant_id=other_tenant.id)
            other_owner.set_password("senha123")
            db.session.add(other_owner)
            db.session.commit()

        login_as(client, "ze@pizzaria.com.br", "senha123")
        client.post("/painel/categorias/nova", data={"name": "Pizzas", "is_active": "y"})

        from app.models import Category

        with app.app_context():
            other_category = Category.query.filter_by(name="Pizzas").first()
            other_category_id = other_category.id

        client.get("/logout")

        # lojista do Braseiro & Cia tenta acessar a categoria da Pizzaria -> 404
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get(f"/painel/categorias/{other_category_id}/editar")
        assert resp.status_code == 404

        # e não aparece na listagem dele
        resp = client.get("/painel/categorias")
        assert "Pizzas" not in resp.get_data(as_text=True)


class TestPlanLimits:
    def test_category_limit_is_enforced(self, app, client, login_as):
        with app.app_context():
            limited_plan = Plan(name="Mini", slug="mini", price_cents=0, billing_cycle=BillingCycle.MONTHLY, max_categories=1)
            db.session.add(limited_plan)
            db.session.flush()

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.plan_id = limited_plan.id
            db.session.commit()

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Categoria 1", "is_active": "y"})
        resp = client.post("/painel/categorias/nova", data={"name": "Categoria 2", "is_active": "y"}, follow_redirects=True)

        assert "plano permite no máximo 1 categorias" in resp.get_data(as_text=True)

        from app.models import Category

        with app.app_context():
            assert Category.query.count() == 1
