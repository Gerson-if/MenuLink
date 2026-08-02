"""
Testes para os ajustes de: favicon, categoria sintética "Sem categoria"
(fix do bug de duplicidade com uma categoria real chamada "Outros"),
tema/cor de destaque customizável do cardápio público (lojista) e do
painel administrativo (Super Admin).
"""

from app.extensions import db
from app.models import Category, Product, Tenant, TenantStatus
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
        # Sem logo customizada do sistema, o favicon do painel admin é um
        # SVG gerado na hora (colorido com a cor de destaque em vigor —
        # ver TestFaviconReflectsTheme), não mais os arquivos estáticos
        # fixos em img/favicon.*.
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/lojistas")
        html = resp.get_data(as_text=True)
        assert "data:image/svg+xml;base64," in html
        assert "img/favicon.svg" not in html

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


class TestTenantPanelTheme:
    def test_defaults_to_none_and_follows_platform_theme(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.panel_theme is None

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/dashboard")
        assert "theme-chili" in resp.get_data(as_text=True)

    def test_settings_route_saves_panel_theme(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "ocean"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.panel_theme == "ocean"

        resp = client.get("/painel/dashboard")
        assert "theme-ocean" in resp.get_data(as_text=True)

    def test_panel_theme_independent_from_other_tenants(self, app, client, login_as):
        # Um lojista escolher o próprio tema não deve afetar o painel de
        # outra loja nem o painel do Super Admin — cada um segue sua
        # própria configuração (Tenant.panel_theme x PlatformSettings.system_theme).
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "midnight"},
        )
        client.get("/logout")

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/lojistas")
        html = resp.get_data(as_text=True)
        assert "theme-midnight" not in html
        assert "theme-chili" in html

    def test_reset_to_platform_default(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.theme_settings = {**(tenant.theme_settings or {}), "panel_theme": "forest"}
            db.session.commit()

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": ""},
        )

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.panel_theme is None

    def test_invalid_panel_theme_is_ignored(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "nao-existe"},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.panel_theme is None


class TestTenantPublicThemePreset:
    """Tema PRONTO do CARDÁPIO PÚBLICO (Tenant.public_theme) — distinto
    da cor livre customizada testada em TestTenantPublicTheme acima e de
    panel_theme (área de gestão, ver TestTenantPanelTheme abaixo). Ver
    Tenant.public_theme_css_vars e
    app/controllers/lojista/settings.py:settings_appearance."""

    def test_defaults_to_none_and_uses_custom_accent(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme is None
            assert tenant.public_theme_css_vars is None  # nunca customizou nada ainda

    def test_settings_route_saves_public_theme_and_overrides_accent(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "", "public_theme": "ocean"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme == "ocean"
            # A cor de destaque do cardápio vem do TEMA, não do accent_color
            # livre submetido junto (que fica guardado, mas não tem prioridade).
            assert tenant.public_theme_css_vars["accent"] == "#2E86AB"

    def test_custom_accent_kept_when_switching_back_to_personalized(self, app, client, login_as):
        # Escolher um tema pronto não apaga a cor livre já salva — só
        # sai de prioridade. Voltando para "Personalizado" (public_theme=""),
        # a cor livre volta a valer, sem o lojista precisar escolher de novo.
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#123456", "theme_mode": "dark", "panel_theme": "", "public_theme": ""},
        )
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#123456", "theme_mode": "dark", "panel_theme": "", "public_theme": "forest"},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme_css_vars["accent"] == "#3E8E5B"

        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#123456", "theme_mode": "dark", "panel_theme": "", "public_theme": ""},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme is None
            assert tenant.public_theme_css_vars["accent"] == "#123456"

    def test_invalid_public_theme_is_ignored(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "", "public_theme": "nao-existe"},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.public_theme is None

    def test_public_theme_reflected_on_store_menu_page(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "", "public_theme": "midnight"},
        )

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            slug = tenant.slug
            tenant.status = TenantStatus.ACTIVE
            db.session.commit()

        resp = client.get(f"/loja/{slug}")
        assert "#8B5CF6" in resp.get_data(as_text=True)  # accent do tema "midnight"

    def test_public_theme_preset_overrides_full_palette_not_just_accent(self, app, client, login_as):
        # Reprodução do bug relatado: antes desta correção, escolher um
        # tema pronto só trocava --accent — fundo/superfície/texto
        # continuavam sempre na paleta "Picante" fixa, dando a
        # impressão de que o tema não tinha mudado de verdade. Ver
        # Tenant.public_theme_css_vars / PUBLIC_STORE_THEME_PALETTES.
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "dark", "panel_theme": "", "public_theme": "ocean"},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            theme = tenant.public_theme_css_vars
            assert theme["bg"] == "#0B1420"  # paleta "Oceano" escura, não a "Picante" (#1B140F)
            assert theme["surface"] == "#162535"
            assert theme["text"] == "#EAF4F7"

            slug = tenant.slug
            tenant.status = TenantStatus.ACTIVE
            db.session.commit()

        resp = client.get(f"/loja/{slug}")
        html = resp.get_data(as_text=True)
        # A paleta "Oceano" precisa aparecer DEPOIS da paleta "Picante"
        # fixa (que continua ali como valor-padrão do template) — CSS
        # aplica a última declaração de mesma especificidade dentro de
        # :root, então é essa ordem que garante que "Oceano" vença.
        assert html.index("--bg: #0B1420") > html.index("--bg: #1B140F")

    def test_public_theme_preset_uses_light_palette_when_mode_is_light(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#E8A33D", "theme_mode": "light", "panel_theme": "", "public_theme": "forest"},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            theme = tenant.public_theme_css_vars
            assert theme["bg"] == "#F5F8EF"  # paleta "Floresta" clara

    def test_custom_accent_only_does_not_add_full_palette(self, app, client, login_as):
        # Sem tema pronto escolhido (cor livre), o comportamento de
        # sempre continua: só o accent muda, o resto fica com o padrão
        # fixo do template (nenhuma chave de paleta é adicionada).
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        client.post(
            "/painel/configuracoes/aparencia",
            data={"accent_color": "#123456", "theme_mode": "dark", "panel_theme": "", "public_theme": ""},
        )
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            theme = tenant.public_theme_css_vars
            assert "bg" not in theme


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
            "/admin/configuracoes/aparencia/cor",
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

    def test_saving_system_theme_does_not_reintroduce_stale_accent_override(self, app, client, login_as):
        """Bug relatado: trocar de tema só mudava 'algumas cores e mantinha
        as cores antigas'. Causa raiz: as 5 gavetas viviam num único
        <form>, então salvar o tema reenviava também o valor (desatualizado)
        do campo de cor avançada, recriando um `admin_theme_accent`
        obsoleto. Agora cada gaveta salva isolada — trocar só o tema não
        deve tocar em `admin_theme_accent`."""
        login_as(client, "admin@cardapio.saas", "admin123")

        # define uma cor avançada customizada sobre o tema chili (padrão)
        client.post("/admin/configuracoes/aparencia/cor", data={"accent_color": "#123456"})
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.admin_theme_accent == "#123456"

        # troca só o tema — a gaveta de cor não é reenviada
        resp = client.post(
            "/admin/configuracoes/aparencia/tema",
            data={"system_theme": "ocean"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.system_theme == "ocean"
            # a cor customizada antiga (do tema chili) continua intacta —
            # não foi apagada nem sobrescrita silenciosamente
            assert settings.admin_theme_accent == "#123456"

        resp = client.get("/admin/lojistas")
        html = resp.get_data(as_text=True)
        assert "theme-ocean" in html
        assert "#123456" in html

    def test_saving_accent_matching_current_theme_default_does_not_create_override(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        from app.models.platform_settings import SYSTEM_THEME_ACCENTS

        chili_default = SYSTEM_THEME_ACCENTS["chili"]
        client.post("/admin/configuracoes/aparencia/cor", data={"accent_color": chili_default})
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.admin_theme_accent is None

    def test_saving_support_whatsapp_does_not_clear_accent_override(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        client.post("/admin/configuracoes/aparencia/cor", data={"accent_color": "#123456"})
        client.post("/admin/configuracoes/aparencia/suporte", data={"support_whatsapp": "5567999998888"})
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.admin_theme_accent == "#123456"
            assert settings.support_whatsapp == "5567999998888"

    def test_saving_accent_does_not_clear_support_whatsapp(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        client.post("/admin/configuracoes/aparencia/suporte", data={"support_whatsapp": "5567999998888"})
        client.post("/admin/configuracoes/aparencia/tema", data={"system_theme": "ocean"})
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.support_whatsapp == "5567999998888"


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
            "/admin/configuracoes/aparencia/tema",
            data={"system_theme": "ocean"},
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
            "/admin/configuracoes/aparencia/tema",
            data={"system_theme": "nao-existe"},
        )
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            assert settings.system_theme_or_default == "chili"


class TestFaviconReflectsTheme:
    """O favicon padrão (sem logo customizada do sistema) é gerado como
    um SVG embutido colorido com `PlatformSettings.effective_accent` —
    antes disso, ele usava os arquivos estáticos em img/favicon.* com
    uma cor fixa (vermelho-tijolo), que não acompanhava a troca de tema
    do Super Admin (bug relatado: "a alteração de tema não reflete no
    favicon")."""

    def test_default_favicon_uses_chili_accent(self, app, client):
        resp = client.get("/")
        html = resp.get_data(as_text=True)
        assert "data:image/svg+xml;base64," in html
        assert "img/favicon.svg" not in html

    def test_favicon_updates_after_system_theme_change(self, app, client, login_as):
        from app.models.platform_settings import SYSTEM_THEME_ACCENTS

        login_as(client, "admin@cardapio.saas", "admin123")
        client.post("/admin/configuracoes/aparencia/tema", data={"system_theme": "ocean"})
        client.get("/logout")

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        # o SVG do favicon é embutido em base64 — decodifica pra conferir a cor
        import base64
        import re

        b64 = re.search(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", html).group(1)
        svg = base64.b64decode(b64).decode("utf-8")
        assert SYSTEM_THEME_ACCENTS["ocean"] in svg
        assert SYSTEM_THEME_ACCENTS["chili"] not in svg

    def test_favicon_uses_custom_accent_override_over_theme_default(self, app, client, login_as):
        import base64
        import re

        login_as(client, "admin@cardapio.saas", "admin123")
        client.post("/admin/configuracoes/aparencia/cor", data={"accent_color": "#123456"})
        client.get("/logout")

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        b64 = re.search(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", html).group(1)
        svg = base64.b64decode(b64).decode("utf-8")
        assert "#123456" in svg

    def test_favicon_uses_platform_logo_when_set(self, app, client):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.platform_logo = "uploads/platform/logo.png"
            db.session.commit()

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        assert "uploads/platform/logo.png" in html


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
