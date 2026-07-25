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
    ) -> PlatformSettings:
        """
        `hero_image_file`, `hero_video_file` e cada item de
        `feature_image_files` (mesma ordem de `features`) são FileStorage
        opcionais — só substituem o arquivo atual quando um novo for de
        fato enviado (mesmo padrão de TenantSettingsService.update_store_info
        pra logo da loja); o arquivo antigo é apagado depois do novo ser
        salvo com sucesso, pra não acumular lixo em static/uploads/platform/.

        Remover um arquivo (sem substituir) é uma ação separada — ver
        `remove_landing_media()`, chamada pelo ícone de remover no
        dropzone, com efeito imediato (não depende deste método).
        """
        from app.utils.uploads import (
            LANDING_FEATURE_IMAGE_DIMENSION,
            LANDING_HERO_IMAGE_DIMENSION,
            InvalidImageError,
            InvalidVideoError,
            delete_product_image_file,
            save_platform_image,
            save_platform_video,
        )

        current = self.settings.landing_content_or_default
        feature_image_files = feature_image_files or [None, None, None]

        hero_image = current.get("hero_image")
        if hero_image_file and hero_image_file.filename:
            try:
                new_hero_image = save_platform_image("landing", hero_image_file, max_dimension=LANDING_HERO_IMAGE_DIMENSION)
            except InvalidImageError:
                raise
            else:
                if hero_image:
                    delete_product_image_file(hero_image)
                hero_image = new_hero_image

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

        new_features = []
        for i, f in enumerate(features):
            current_feature = current["features"][i] if i < len(current.get("features", [])) else {}
            image = current_feature.get("image")
            image_file = feature_image_files[i] if i < len(feature_image_files) else None
            if image_file and image_file.filename:
                try:
                    new_image = save_platform_image("landing", image_file, max_dimension=LANDING_FEATURE_IMAGE_DIMENSION)
                except InvalidImageError:
                    raise
                else:
                    if image:
                        delete_product_image_file(image)
                    image = new_image
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

    LANDING_MEDIA_FIELDS = ("hero_image", "hero_video", "feature1_image", "feature2_image", "feature3_image")

    def remove_landing_media(self, field: str) -> PlatformSettings:
        """Ação isolada do ícone de remover no dropzone — apaga o
        arquivo e limpa o campo na hora, sem depender do envio do resto
        do formulário da landing page (ver update_landing_content).
        `field` é um dos LANDING_MEDIA_FIELDS."""
        from app.utils.uploads import delete_product_image_file

        if field not in self.LANDING_MEDIA_FIELDS:
            raise ValueError(f"Campo de mídia inválido: {field!r}")

        current = self.settings.landing_content_or_default
        content = dict(current)
        content["features"] = [dict(f) for f in current.get("features", [])]

        if field.startswith("feature"):
            index = int(field[len("feature")]) - 1
            if index < len(content["features"]):
                path = content["features"][index].get("image")
                if path:
                    delete_product_image_file(path)
                    content["features"][index]["image"] = None
        else:
            path = content.get(field)
            if path:
                delete_product_image_file(path)
                content[field] = None

        self.settings.landing_content = content
        db.session.commit()
        return self.settings
