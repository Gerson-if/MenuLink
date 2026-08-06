import re

from app.extensions import db
from app.models import Plan, Tenant


def _get_csrf(html: str) -> str | None:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


def _allow_qr_customization(app):
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
        tenant.plan.allow_qr_customization = True
        db.session.commit()


class TestQrCodePreviewLayout:
    def test_qr_preview_image_has_responsive_css_class(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio")
        html = resp.get_data(as_text=True)
        assert 'class="rounded mb-3 qr-preview-img"' in html


class TestQrCardGatingByPlan:
    def test_menu_page_shows_upsell_when_plan_does_not_allow(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio")
        html = resp.get_data(as_text=True)
        assert "plano Start" in html
        assert 'name="show_business_name"' not in html

    def test_customize_route_forbidden_when_plan_does_not_allow(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post("/painel/configuracoes/cardapio/qrcode/personalizar", data={})
        assert resp.status_code == 403

    def test_print_route_forbidden_when_plan_does_not_allow(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio/qrcode/cartaz")
        assert resp.status_code == 403


class TestQrCardCustomization:
    def test_menu_page_shows_form_when_plan_allows(self, app, client, login_as):
        _allow_qr_customization(app)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/cardapio")
        html = resp.get_data(as_text=True)
        assert 'name="show_business_name"' in html
        assert 'name="bg_color"' in html
        assert 'name="icon"' in html

    def test_saving_customization_persists_and_reflects_on_print_card(self, app, client, login_as):
        _allow_qr_customization(app)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/configuracoes/cardapio")
        csrf = _get_csrf(resp.get_data(as_text=True))

        resp = client.post(
            "/painel/configuracoes/cardapio/qrcode/personalizar",
            data={
                "show_business_name": "y", "bg_color": "#112233", "icon": "bi-cup-hot-fill",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Personalização do QR Code salva" in resp.get_data(as_text=True)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.theme_settings["qr_card"] == {
                "show_business_name": True, "bg_color": "#112233", "icon": "bi-cup-hot-fill",
            }

        card_html = client.get("/painel/configuracoes/cardapio/qrcode/cartaz").get_data(as_text=True)
        assert "Braseiro &amp; Cia" in card_html or "Braseiro & Cia" in card_html
        assert "#112233" in card_html
        assert "bi-cup-hot-fill" in card_html

    def test_invalid_icon_is_rejected_by_form_validation(self, app, client, login_as):
        _allow_qr_customization(app)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/configuracoes/cardapio")
        csrf = _get_csrf(resp.get_data(as_text=True))

        client.post(
            "/painel/configuracoes/cardapio/qrcode/personalizar",
            data={
                "show_business_name": "y", "bg_color": "#112233",
                "icon": "<script>alert(1)</script>", "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert not (tenant.theme_settings or {}).get("qr_card")

    def test_hiding_business_name_omits_it_from_card(self, app, client, login_as):
        _allow_qr_customization(app)
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        resp = client.get("/painel/configuracoes/cardapio")
        csrf = _get_csrf(resp.get_data(as_text=True))

        client.post(
            "/painel/configuracoes/cardapio/qrcode/personalizar",
            data={"bg_color": "#FFFFFF", "icon": "none", "csrf_token": csrf},
            follow_redirects=True,
        )

        card_html = client.get("/painel/configuracoes/cardapio/qrcode/cartaz").get_data(as_text=True)
        assert '<div class="qrcode-print-name">' not in card_html
