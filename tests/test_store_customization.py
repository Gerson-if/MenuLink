"""
Testes para as customizações do cardápio público adicionadas junto com o
novo template visual: ícone de categoria, selo (tag) de produto, endereço
da loja e horário de funcionamento (status aberto/fechado).
"""

from datetime import datetime

from app.extensions import db
from app.models import Category, Product, Tenant


class TestCategoryIcon:
    def test_create_category_with_icon_persists_and_appears_on_public_menu(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.post(
            "/painel/categorias/nova",
            data={"name": "Pizzas da Casa", "icon": "pizza", "is_active": "y"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            category = Category.query.filter_by(name="Pizzas da Casa").first()
            assert category is not None
            assert category.icon == "pizza"

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            product = Product(
                tenant_id=tenant.id, category_id=category.id, name="Marguerita",
                slug="marguerita", price_cents=3990, is_active=True,
            )
            db.session.add(product)
            db.session.commit()

        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        # o ícone da categoria é embutido no JSON do menu (usado pelo Alpine
        # para escolher o SVG certo no menu lateral)
        assert '"icon": "pizza"' in html or '"icon":"pizza"' in html

    def test_default_icon_is_other_when_not_specified(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/categorias/nova", data={"name": "Diversos", "is_active": "y"})

        with app.app_context():
            category = Category.query.filter_by(name="Diversos").first()
            assert category.icon == "other"


class TestProductTag:
    def test_product_tag_persists_and_appears_on_public_menu(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        client.post(
            "/painel/produtos/novo",
            data={"name": "Combo Família", "category_id": "0", "price": "99.90", "tag": "Mais pedido", "is_active": "y"},
        )

        with app.app_context():
            product = Product.query.filter_by(name="Combo Família").first()
            assert product is not None
            assert product.tag == "Mais pedido"

        resp = client.get("/loja/braseiro-cia")
        assert "Mais pedido" in resp.get_data(as_text=True)

    def test_tag_is_optional(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/produtos/novo",
            data={"name": "Água Mineral", "category_id": "0", "price": "5.00", "is_active": "y"},
        )
        with app.app_context():
            product = Product.query.filter_by(name="Água Mineral").first()
            assert product.tag is None


class TestStoreAddress:
    def test_updating_address_reflects_on_public_menu_footer(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.post(
            "/painel/configuracoes/loja",
            data={
                "name": "Braseiro & Cia",
                "address_street": "Rua das Brasas",
                "address_number": "142",
                "address_neighborhood": "Centro",
                "address_city": "Campo Grande",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.address_street == "Rua das Brasas"
            assert tenant.address_city == "Campo Grande"

        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        assert "Rua das Brasas, 142" in html
        assert "Centro" in html


class TestMenuTagline:
    def test_customizing_tagline_reflects_on_public_menu(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.post(
            "/painel/configuracoes/loja",
            data={"name": "Braseiro & Cia", "menu_tagline": "Churrasco Artesanal"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.menu_tagline == "Churrasco Artesanal"

        html = client.get("/loja/braseiro-cia").get_data(as_text=True)
        assert "Churrasco Artesanal" in html
        assert "CARDÁPIO DIGITAL" not in html

    def test_blank_tagline_falls_back_to_default(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.menu_tagline_or_default == "CARDÁPIO DIGITAL"


class TestOpeningHours:
    def test_saving_hours_via_settings_form(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/configuracoes/horario")
        assert resp.status_code == 200

        data = {"mon-open": "18:00", "mon-close": "23:00"}
        # Todos os outros dias ficam marcados como fechados (checkbox
        # "closed" ausente do payload == não marcado -> tratado como
        # fechado pelo service, já que não veio open/close válidos).
        for day in ["tue", "wed", "thu", "fri", "sat", "sun"]:
            data[f"{day}-closed"] = "y"

        resp = client.post("/painel/configuracoes/horario", data=data, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.opening_hours["mon"] == {"open": "18:00", "close": "23:00"}
            assert tenant.opening_hours["tue"] == {"closed": True}

    def test_opening_status_none_when_not_configured(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.opening_hours is None
            assert tenant.opening_status() is None

    def test_opening_status_open_within_hours(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.opening_hours = {
                "mon": {"open": "18:00", "close": "23:00"},
                "tue": {"closed": True}, "wed": {"closed": True}, "thu": {"closed": True},
                "fri": {"closed": True}, "sat": {"closed": True}, "sun": {"closed": True},
            }
            # Uma segunda-feira, 20:00 -> dentro do horário de funcionamento.
            now = datetime(2024, 1, 1, 20, 0)  # 2024-01-01 é uma segunda-feira
            status = tenant.opening_status(now=now)
            assert status["open"] is True
            assert "23:00" in status["label"]

    def test_opening_status_closed_outside_hours(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.opening_hours = {
                "mon": {"open": "18:00", "close": "23:00"},
                "tue": {"closed": True}, "wed": {"closed": True}, "thu": {"closed": True},
                "fri": {"closed": True}, "sat": {"closed": True}, "sun": {"closed": True},
            }
            now = datetime(2024, 1, 1, 10, 0)  # segunda-feira de manhã, antes de abrir
            status = tenant.opening_status(now=now)
            assert status["open"] is False
            assert "18:00" in status["label"]


class TestAcceptedPaymentMethods:
    def test_all_methods_enabled_by_default(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            values = [m["value"] for m in tenant.accepted_payment_methods]
            assert values == ["pix", "card", "cash", "other"]

    def test_disabling_methods_via_settings_form(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.post(
            "/painel/configuracoes/checkout",
            data={"accept_pix": "y"},  # só Pix marcado — resto ausente = desmarcado
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.accept_pix is True
            assert tenant.accept_card is False
            assert tenant.accept_cash is False
            assert tenant.accept_other is False
            assert tenant.accepted_payment_methods == [{"value": "pix", "label": "Pix"}]

    def test_cannot_disable_every_payment_method(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        # Um payload realmente vazio faz o WTForms tratar os campos como
        # "não enviados" (mantém o default) em vez de "desmarcados" — por
        # isso incluímos um campo presente (mesmo que vazio) para simular
        # um formulário de verdade submetido sem nenhum checkbox marcado.
        resp = client.post("/painel/configuracoes/checkout", data={"min_order": ""})
        assert resp.status_code == 200  # form re-renderizado com erro, não redireciona

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            # nada foi salvo — continua com os padrões (todos habilitados)
            assert tenant.accept_pix is True
            assert tenant.accept_card is True

    def test_public_menu_only_shows_enabled_methods(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/configuracoes/checkout", data={"accept_pix": "y", "accept_cash": "y"})

        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        assert '"value": "pix"' in html or '"value":"pix"' in html
        assert '"value": "cash"' in html or '"value":"cash"' in html
        assert '"value": "card"' not in html and '"value":"card"' not in html
