"""
Regras de negócio das configurações globais da plataforma (Super
Admin) — hoje só a integração de cobrança com o Asaas.
"""

import secrets

from app.extensions import db
from app.models.platform_settings import PlatformSettings
from app.services.payment_gateway import get_gateway
from app.services.payment_gateway.base import PaymentGatewayError


class PlatformSettingsService:
    def __init__(self):
        self.settings = PlatformSettings.get_or_create()

    def update_asaas(
        self, *, environment: str,
        api_key: str | None = None, clear_api_key: bool = False,
        webhook_token: str | None = None, clear_webhook_token: bool = False,
    ) -> PlatformSettings:
        self.settings.asaas_environment = environment or "sandbox"

        if clear_api_key:
            self.settings.asaas_api_key = None
        elif api_key:
            self.settings.asaas_api_key = api_key.strip()

        if clear_webhook_token:
            self.settings.asaas_webhook_token = None
        elif webhook_token:
            self.settings.asaas_webhook_token = webhook_token.strip()

        db.session.commit()
        return self.settings

    def generate_webhook_token(self) -> str:
        """Gera um token aleatório e seguro (evita o erro comum de
        configurar um token curto/previsível "na mão"). Salva
        imediatamente — o valor só é exibido uma vez, no flash da
        própria requisição que chamou este método."""
        token = secrets.token_urlsafe(32)
        self.settings.asaas_webhook_token = token
        db.session.commit()
        return token

    def test_connection(self) -> None:
        """Chama o Asaas com a chave salva atualmente, só para leitura,
        e levanta PaymentGatewayError com uma mensagem exibível se a
        chave for inválida ou a conexão falhar. Usado pelo botão
        "Testar conexão" — detecta uma chave errada antes dela ser
        usada de verdade para gerar uma cobrança."""
        gateway = get_gateway()
        if gateway is None:
            raise PaymentGatewayError("Nenhuma chave de API configurada ainda.")
        gateway.ping()

    def update_admin_appearance(self, *, accent_color=None, reset_to_default=False):
        from app.utils.colors import normalize_hex

        if reset_to_default:
            self.settings.admin_theme_accent = None
        elif accent_color:
            normalized = normalize_hex(accent_color)
            if normalized:
                self.settings.admin_theme_accent = normalized
        db.session.commit()
        return self.settings

    def update_landing_content(
        self, *, hero_title: str, hero_subtitle: str, features: list[dict],
        hero_image_file=None, feature_image_files: list = None,
        hero_video_file=None, theme: str = "chili",
        remove_hero_image: bool = False, remove_hero_video: bool = False,
        remove_feature_images: list = None,
    ) -> PlatformSettings:
        """
        `hero_image_file`, `hero_video_file` e cada item de
        `feature_image_files` (mesma ordem de `features`) são FileStorage
        opcionais — só substituem o arquivo atual quando um novo for de
        fato enviado (mesmo padrão de TenantSettingsService.update_store_info
        pra logo da loja); o arquivo antigo é apagado depois do novo ser
        salvo com sucesso, pra não acumular lixo em static/uploads/platform/.

        `remove_hero_image`/`remove_hero_video`/`remove_feature_images`
        (lista de bool, mesma ordem de `features`) apagam o arquivo atual
        e voltam o campo pra vazio — só têm efeito quando NENHUM arquivo
        novo foi enviado no mesmo campo (um upload novo sempre vence).
        """
        from app.utils.uploads import (
            InvalidImageError,
            InvalidVideoError,
            delete_product_image_file,
            save_platform_image,
            save_platform_video,
        )

        current = self.settings.landing_content_or_default
        feature_image_files = feature_image_files or [None, None, None]
        remove_feature_images = remove_feature_images or [False, False, False]

        hero_image = current.get("hero_image")
        if hero_image_file and hero_image_file.filename:
            try:
                new_hero_image = save_platform_image("landing", hero_image_file)
            except InvalidImageError:
                raise
            else:
                if hero_image:
                    delete_product_image_file(hero_image)
                hero_image = new_hero_image
        elif remove_hero_image and hero_image:
            delete_product_image_file(hero_image)
            hero_image = None

        hero_video = current.get("hero_video")
        if hero_video_file and hero_video_file.filename:
            try:
                new_hero_video = save_platform_video("landing", hero_video_file)
            except InvalidVideoError:
                raise
            else:
                if hero_video:
                    delete_product_image_file(hero_video)
                hero_video = new_hero_video
        elif remove_hero_video and hero_video:
            delete_product_image_file(hero_video)
            hero_video = None

        new_features = []
        for i, f in enumerate(features):
            current_feature = current["features"][i] if i < len(current.get("features", [])) else {}
            image = current_feature.get("image")
            image_file = feature_image_files[i] if i < len(feature_image_files) else None
            remove_this_image = remove_feature_images[i] if i < len(remove_feature_images) else False
            if image_file and image_file.filename:
                try:
                    new_image = save_platform_image("landing", image_file)
                except InvalidImageError:
                    raise
                else:
                    if image:
                        delete_product_image_file(image)
                    image = new_image
            elif remove_this_image and image:
                delete_product_image_file(image)
                image = None
            new_features.append({
                "icon": (f.get("icon") or "").strip(),
                "image": image,
                "title": (f.get("title") or "").strip(),
                "description": (f.get("description") or "").strip(),
            })

        self.settings.landing_content = {
            "hero_title": hero_title.strip(),
            "hero_subtitle": hero_subtitle.strip(),
            "hero_image": hero_image,
            "hero_video": hero_video,
            "theme": theme if theme in ("chili", "blue", "light") else "chili",
            "features": new_features,
        }
        db.session.commit()
        return self.settings
