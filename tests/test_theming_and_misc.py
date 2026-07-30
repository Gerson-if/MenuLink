"""
Testes para os ajustes de: favicon, categoria sintética "Sem categoria"
(fix do bug de duplicidade com uma categoria real chamada "Outros"),
tema/cor de destaque customizável do cardápio público (lojista) e do
painel administrativo (Super Admin).
"""

from app.extensions import db
from app.models import Category, Product, Tenant
from app.models.platform_settings import PlatformSettings
from app.utils.colors import darken_hex, hex_to_rgba_css, normalize_hex


class TestColorUtils:
    def test_normalize_hex_accepts_with_and_without_hash(self):
        assert normalize_hex("#e8a33d") == "#E8A33D"
        assert normalize_hex("e8a33d") == "#E8A33D"

    def test_normalize_hex_rejects_invalid(self):
        assert normalize_hex("not-a-color") is None
        assert normalize_hex("#fff") is None
        assert normalize_hex("") is None
        assert normalize_hex(None) is None

    def test_darken_hex_produces_darker_color(self):
        darker = darken_hex("#E8A33D", 0.16)
        assert darker != "#E8A33D"
        # cada componente RGB deve ser <= o original
        orig = (0xE8, 0xA3, 0x3D)
        new = tuple(int(darker[i : i + 2], 16) for i in (1, 3, 5))
        assert all(n <= o for n, o in zip(new, orig))

    def test_hex_to_rgba_css_format(self):
        assert hex_to_rgba_css("#E8A33D", 0.14) == "rgba(232,163,61,0.14)"


class TestFavicon:
    def test_admin_pages_use_platform_favicon(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/lojistas")
        assert "favicon.svg" in resp.get_data(as_text=True)

    def test_public_menu_generates_favicon_from_store_initial_without_logo(self, app, client):
        # Tenant de teste (braseiro-cia) não tem logo cadastrado — o
        # cardápio público deve gerar um favicon SVG com a inicial da loja
        # em vez do favicon genérico da plataforma.
        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        assert "favicon.svg" not in html
        assert "data:image/svg+xml;base64," in html

    def test_public_menu_uses_logo_as_favicon_when_configured(self, app, client):
        with app.app_context():
            from app.extensions import db
            from app.models import Tenant

            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.logo_path = "uploads/tenant_1/logo.png"
            db.session.commit()

        try:
            resp = client.get("/loja/braseiro-cia")
            html = resp.get_data(as_text=True)
            assert "uploads/tenant_1/logo.png" in html
            assert "data:image/svg+xml;base64," not in html
        finally:
            with app.app_context():
                from app.extensions import db
                from app.models import Tenant

                tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
                tenant.logo_path = None
                db.session.commit()


class TestUncategorizedBucketNaming:
    def test_uncategorized_products_use_distinct_name_from_real_outros_category(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            real_outros = Category(tenant_id=tenant.id, name="Outros", slug="outros", icon="other", is_active=True)
            db.session.add(real_outros)
            db.session.flush()
            db.session.add(Product(
                tenant_id=tenant.id, category_id=real_outros.id, name="Item da categoria Outros",
                slug="item-categoria-outros", price_cents=1000, is_active=True,
            ))
            db.session.add(Product(
                tenant_id=tenant.id, category_id=None, name="Item sem categoria",
                slug="item-sem-categoria", price_cents=1500, is_active=True,
            ))
            db.session.commit()

        resp = client.get("/loja/braseiro-cia")
        html = resp.get_data(as_text=True)
        # a categoria real "Outros" continua aparecendo com esse nome...
        assert '"name": "Outros"' in html or '"name":"Outros"' in html
        # ...mas o bucket sintético agora tem um nome diferente, então
        # não aparecem duas entradas idênticas "Outros" na barra lateral
        assert '"name": "Sem categoria"' in html or '"name":"Sem categoria"' in html


class TestTenantPublicTheme:
    def test_no_customization_returns_none(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme_css_vars is None

    def test_customized_accent_returns_derived_vars(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.theme_settings = {"accent": "#2A9D8F"}
            db.session.commit()

            theme = tenant.public_theme_css_vars
            assert theme["accent"] == "#2A9D8F"
            assert theme["accent_dark"] != "#2A9D8F"
            assert theme["accent_soft"].startswith("rgba(")

    def test_settings_route_saves_accent_color(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#2A9D8F"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.theme_settings["accent"] == "#2A9D8F"

        resp = client.get("/loja/braseiro-cia")
        assert "#2A9D8F" in resp.get_data(as_text=True)

    def test_reset_to_default_clears_customization(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.theme_settings = {"accent": "#2A9D8F"}
            db.session.commit()

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post("/painel/configuracoes/aparencia", data={"reset_to_default": "y"})

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert not (tenant.theme_settings or {}).get("accent")

    def test_invalid_color_is_rejected(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post("/painel/configuracoes/aparencia", data={"accent_color": "not-a-color"})
        assert resp.status_code == 200  # form re-renderizado com erro

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert not (tenant.theme_settings or {}).get("accent")


class TestAdminPanelTheme:
    def test_admin_panel_defaults_to_chili_not_blue(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/lojistas")
        html = resp.get_data(as_text=True)
        assert "theme-blue" not in html
        assert "theme-chili" in html

    def test_admin_theme_css_vars_none_by_default(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.admin_theme_css_vars is None

    def test_settings_appearance_route_saves_and_reflects_in_panel(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/aparencia",
            data={"accent_color": "#123456"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.admin_theme_accent == "#123456"

        resp = client.get("/admin/lojistas")
        assert "#123456" in resp.get_data(as_text=True)

        # o mesmo tema também deve valer para o painel do lojista
        client.get("/logout")
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/dashboard")
        assert "#123456" in resp.get_data(as_text=True)

    def test_settings_appearance_requires_super_admin(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/configuracoes/aparencia")
        assert resp.status_code in (302, 403)


class TestUnifiedSystemTheme:
    def test_defaults_to_chili(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.system_theme_or_default == "chili"

    def test_landing_page_uses_default_theme_class(self, app, client):
        resp = client.get("/")
        assert "theme-chili" in resp.get_data(as_text=True)

    def test_settings_appearance_saves_system_theme_and_reflects_everywhere(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/aparencia",
            data={"system_theme": "ocean", "accent_color": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.system_theme == "ocean"

        # painel do admin
        resp = client.get("/admin/lojistas")
        assert "theme-ocean" in resp.get_data(as_text=True)

        # painel do lojista
        client.get("/logout")
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/dashboard")
        assert "theme-ocean" in resp.get_data(as_text=True)

        # landing page pública
        client.get("/logout")
        resp = client.get("/")
        assert "theme-ocean" in resp.get_data(as_text=True)

        # tela de login
        resp = client.get("/login")
        assert "theme-ocean" in resp.get_data(as_text=True)

    def test_invalid_system_theme_is_ignored(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        client.post(
            "/admin/configuracoes/aparencia",
            data={"system_theme": "nao-existe", "accent_color": ""},
        )
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.system_theme_or_default == "chili"


class TestAuthBackgroundVideo:
    def test_login_page_has_no_video_by_default(self, app, client):
        resp = client.get("/login")
        html = resp.get_data(as_text=True)
        assert "auth-video-bg" not in html
        assert "auth-shell-with-video" not in html

    def test_login_page_shows_configured_video(self, app, client):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.auth_background_video = "uploads/platform/auth/bg.mp4"
            db.session.commit()

        try:
            for path in ("/login", "/cadastro", "/recuperar-senha"):
                resp = client.get(path)
                html = resp.get_data(as_text=True)
                assert "auth-video-bg" in html, f"vídeo não apareceu em {path}"
                assert "uploads/platform/auth/bg.mp4" in html
        finally:
            with app.app_context():
                settings = PlatformSettings.get_or_create()
                settings.auth_background_video = None
                db.session.commit()
