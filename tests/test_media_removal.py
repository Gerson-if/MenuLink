"""
Ícone de remover instantâneo nos dropzones (logo da loja, ícone de
categoria, mídia da landing page) — ação isolada, com efeito imediato,
sem depender do envio do resto do formulário. Ver
TenantSettingsService.remove_logo, CategoryService.remove_icon_image,
PlatformSettingsService.remove_landing_media e as rotas HTMX-only
correspondentes.
"""

import pytest

from app.extensions import db
from app.models import Category, Tenant
from app.services.category_service import CategoryService
from app.services.platform_settings_service import PlatformSettingsService
from app.services.tenant_settings_service import TenantSettingsService


class TestTenantSettingsServiceRemoveLogo:
    def test_removes_logo_path(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.logo_path = "uploads/tenant_1/logo.png"
            db.session.commit()

            TenantSettingsService(tenant).remove_logo()

            assert tenant.logo_path is None

    def test_is_a_no_op_when_no_logo(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.logo_path = None
            db.session.commit()

            TenantSettingsService(tenant).remove_logo()

            assert tenant.logo_path is None


class TestCategoryServiceRemoveIconImage:
    def test_removes_icon_image_path(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            category = Category(tenant_id=tenant.id, name="Lanches", slug="lanches", icon_image_path="uploads/tenant_1/cat_1/icon.png")
            db.session.add(category)
            db.session.commit()

            CategoryService(tenant).remove_icon_image(category)

            assert category.icon_image_path is None


class TestPlatformSettingsServiceRemoveLandingMedia:
    def test_removes_hero_image(self, app):
        with app.app_context():
            service = PlatformSettingsService()
            service.update_landing_content(
                hero_title="T", hero_subtitle="S",
                features=[{"icon": "bi-star", "title": "F1", "description": "D1"}] * 3,
            )
            service.settings.landing_content = {**service.settings.landing_content_or_default, "hero_image": "uploads/platform/landing/x.png"}
            db.session.commit()

            service.remove_landing_media("hero_image")

            assert service.settings.landing_content_or_default["hero_image"] is None

    def test_removes_one_feature_image_without_touching_others(self, app):
        with app.app_context():
            service = PlatformSettingsService()
            service.update_landing_content(
                hero_title="T", hero_subtitle="S",
                features=[{"icon": "bi-star", "title": f"F{i}", "description": f"D{i}"} for i in range(3)],
            )
            content = dict(service.settings.landing_content_or_default)
            content["features"] = [
                {**content["features"][0], "image": "uploads/platform/landing/f1.png"},
                {**content["features"][1], "image": "uploads/platform/landing/f2.png"},
                {**content["features"][2], "image": None},
            ]
            service.settings.landing_content = content
            db.session.commit()

            service.remove_landing_media("feature1_image")

            features = service.settings.landing_content_or_default["features"]
            assert features[0]["image"] is None
            assert features[1]["image"] == "uploads/platform/landing/f2.png"

    def test_rejects_unknown_field(self, app):
        with app.app_context():
            service = PlatformSettingsService()
            with pytest.raises(ValueError):
                service.remove_landing_media("not_a_real_field")


class TestPlatformSettingsServiceRemoveAuthVideo:
    def test_removes_auth_background_video(self, app):
        with app.app_context():
            service = PlatformSettingsService()
            service.settings.auth_background_video = "uploads/platform/auth/x.mp4"
            db.session.commit()

            service.remove_auth_background_video()

            assert service.settings.auth_background_video is None

    def test_is_a_no_op_when_no_video(self, app):
        with app.app_context():
            service = PlatformSettingsService()
            service.settings.auth_background_video = None
            db.session.commit()

            service.remove_auth_background_video()  # não deve levantar erro

            assert service.settings.auth_background_video is None


class TestRemoveAuthVideoRoute:
    def test_requires_htmx_header(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/aparencia/video-login/remover")
        assert resp.status_code == 400

    def test_removes_video_and_returns_fragment(self, app, client, login_as):
        with app.app_context():
            from app.models.platform_settings import PlatformSettings

            settings = PlatformSettings.get_or_create()
            settings.auth_background_video = "uploads/platform/auth/x.mp4"
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/aparencia/video-login/remover", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert b"dropzone-remove-existing" not in resp.data

        with app.app_context():
            from app.models.platform_settings import PlatformSettings

            settings = PlatformSettings.get_or_create()
            assert settings.auth_background_video is None

    def test_requires_super_admin(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post("/admin/configuracoes/aparencia/video-login/remover", headers={"HX-Request": "true"})
        assert resp.status_code in (302, 403)


class TestRemoveLogoRoute:
    def test_requires_htmx_header(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post("/painel/configuracoes/loja/logo/remover")
        assert resp.status_code == 400

    def test_removes_logo_and_returns_fragment(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.logo_path = "uploads/tenant_1/logo.png"
            db.session.commit()

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post("/painel/configuracoes/loja/logo/remover", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert b"dropzone-remove-existing" not in resp.data
        assert b"sidebar-brand-mark" in resp.data

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.logo_path is None

    def test_requires_login(self, client):
        resp = client.post("/painel/configuracoes/loja/logo/remover", headers={"HX-Request": "true"})
        assert resp.status_code in (302, 401, 403)


class TestRemoveCategoryIconRoute:
    def test_requires_htmx_header(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            category = Category(tenant_id=tenant.id, name="Bebidas", slug="bebidas", icon_image_path="uploads/tenant_1/cat_1/icon.png")
            db.session.add(category)
            db.session.commit()
            category_id = category.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(f"/painel/categorias/{category_id}/icone/remover")
        assert resp.status_code == 400

    def test_removes_icon_and_returns_fragment(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            category = Category(tenant_id=tenant.id, name="Bebidas", slug="bebidas", icon_image_path="uploads/tenant_1/cat_1/icon.png")
            db.session.add(category)
            db.session.commit()
            category_id = category.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(f"/painel/categorias/{category_id}/icone/remover", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert b"dropzone-remove-existing" not in resp.data

        with app.app_context():
            category = db.session.get(Category, category_id)
            assert category.icon_image_path is None


class TestRemoveLandingMediaRoute:
    def test_requires_htmx_header(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post("/admin/configuracoes/landing/midia/hero_image/remover")
        assert resp.status_code == 400

    def test_rejects_unknown_field(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/landing/midia/not_a_field/remover", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    def test_removes_hero_video_and_returns_fragment(self, app, client, login_as):
        with app.app_context():
            service = PlatformSettingsService()
            content = dict(service.settings.landing_content_or_default)
            content["hero_video"] = "uploads/platform/landing/video.mp4"
            service.settings.landing_content = content
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.post(
            "/admin/configuracoes/landing/midia/hero_video/remover", headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert b"dropzone-remove-existing" not in resp.data

        with app.app_context():
            service = PlatformSettingsService()
            assert service.settings.landing_content_or_default["hero_video"] is None

    def test_requires_super_admin(self, app, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/admin/configuracoes/landing/midia/hero_image/remover", headers={"HX-Request": "true"},
        )
        assert resp.status_code in (302, 401, 403)
