from app.extensions import db
from app.models import Category, Product, Tenant


class TestHtmxInteractions:
    def test_toggle_category_via_htmx_flips_status(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Toggle Test", "is_active": "y"})

        with app.app_context():
            category = Category.query.filter_by(name="Toggle Test").first()
            category_id = category.id
            assert category.is_active is True

        resp = client.post(
            f"/painel/categorias/{category_id}/alternar",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "Inativa" in resp.get_data(as_text=True)

        with app.app_context():
            category = db.session.get(Category, category_id)
            assert category.is_active is False

    def test_toggle_product_via_htmx_flips_status(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Toggle Produto", "category_id": "0", "price": "10.00", "is_active": "y"},
        )
        with app.app_context():
            product = Product.query.filter_by(name="Toggle Produto").first()
            product_id = product.id

        resp = client.post(f"/painel/produtos/{product_id}/alternar", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Inativo" in resp.get_data(as_text=True)

    def test_delete_category_via_htmx_returns_empty_body_for_row_removal(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Delete Via Htmx", "is_active": "y"})

        with app.app_context():
            category_id = Category.query.filter_by(name="Delete Via Htmx").first().id

        resp = client.post(
            f"/painel/categorias/{category_id}/excluir",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == ""

        with app.app_context():
            assert db.session.get(Category, category_id) is None

    def test_delete_category_without_htmx_still_redirects(self, app, client, login_as):
        # Fallback tradicional (sem cabeçalho HX-Request) continua funcionando.
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Delete Sem Htmx", "is_active": "y"})

        with app.app_context():
            category_id = Category.query.filter_by(name="Delete Sem Htmx").first().id

        resp = client.post(f"/painel/categorias/{category_id}/excluir", follow_redirects=True)
        assert resp.status_code == 200
        assert "Categoria excluída" in resp.get_data(as_text=True)

    def test_image_upload_via_htmx_returns_gallery_fragment_with_message(self, app, client, login_as):
        import io

        from PIL import Image

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Produto Com Foto Htmx", "category_id": "0", "price": "15.00", "is_active": "y"},
        )
        with app.app_context():
            product_id = Product.query.filter_by(name="Produto Com Foto Htmx").first().id

        buf = io.BytesIO()
        Image.new("RGB", (40, 40), (10, 10, 10)).save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            f"/painel/produtos/{product_id}/imagens",
            data={"image": (buf, "foto.png")},
            content_type="multipart/form-data",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="image-gallery"' in body
        assert "Imagem enviada com sucesso" in body


class TestLoginFieldLimits:
    def test_password_over_max_length_is_rejected(self, client):
        import re

        resp = client.get("/login")
        html = resp.get_data(as_text=True)
        match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
        data = {"email": "admin@cardapio.saas", "password": "a" * 200}
        if match:
            data["csrf_token"] = match.group(1)

        resp = client.post("/login", data=data, follow_redirects=True)
        assert resp.status_code == 200
        # 200 bytes já estoura o limite de 72 bytes do bcrypt antes mesmo
        # de chegar no Length(max=128) — ver password_max_bytes em
        # app/utils/validators.py (StopValidation corta a cadeia aqui).
        assert "muito longa" in resp.get_data(as_text=True)

    def test_email_field_has_maxlength_attribute_in_html(self, client):
        resp = client.get("/login")
        html = resp.get_data(as_text=True)
        assert 'maxlength="180"' in html
        assert 'maxlength="128"' in html
