"""
Regras de negócio das configurações globais da plataforma (Super
Admin) — hoje só a integração de cobrança com o Asaas.
"""

import secrets
from datetime import date

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

    def update_email(
        self, *, host: str | None, port: int, username: str | None,
        password: str | None = None, clear_password: bool = False,
        encryption: str, from_email: str | None, from_name: str | None,
    ) -> PlatformSettings:
        self.settings.smtp_host = (host or "").strip() or None
        self.settings.smtp_port = port or 587
        self.settings.smtp_username = (username or "").strip() or None
        self.settings.smtp_encryption = encryption if encryption in ("tls", "ssl", "none") else "tls"
        self.settings.smtp_from_email = (from_email or "").strip() or None
        self.settings.smtp_from_name = (from_name or "").strip() or None

        if clear_password:
            self.settings.smtp_password = None
        elif password:
            self.settings.smtp_password = password

        db.session.commit()
        return self.settings

    def send_test_email(self, *, to: str) -> None:
        """Usado pelo botão "Enviar e-mail de teste" — levanta EmailError
        (mensagem já pronta pra exibir) se o envio falhar por qualquer
        motivo, incluindo SMTP ainda não configurado."""
        from app.services.email_service import EmailService

        EmailService(self.settings).send(
            to=to,
            subject="Teste de configuração de e-mail — Comanda",
            body_text=(
                "Se você recebeu este e-mail, a integração de e-mail do Comanda está "
                "configurada corretamente e pronta para enviar recuperação de senha, "
                "avisos de cobrança e outras notificações."
            ),
            body_html=(
                "<p>Se você recebeu este e-mail, a integração de e-mail do Comanda está "
                "configurada corretamente e pronta para enviar recuperação de senha, "
                "avisos de cobrança e outras notificações.</p>"
            ),
        )

    def update_legal_content(self, *, terms_content: str | None, privacy_content: str | None) -> PlatformSettings:
        """Salva o texto customizado de Termos de Uso e/ou Política de
        Privacidade — deixar um campo em branco restaura o texto padrão
        do template pra aquele documento (ver `terms_content`/
        `privacy_content` em app/models/platform_settings.py)."""
        today = date.today()

        terms_content = (terms_content or "").strip()
        self.settings.terms_content = terms_content or None
        self.settings.terms_updated_at = today if terms_content else None

        privacy_content = (privacy_content or "").strip()
        self.settings.privacy_content = privacy_content or None
        self.settings.privacy_updated_at = today if privacy_content else None

        db.session.commit()
        return self.settings

    def update_admin_appearance(
        self, *, accent_color=None, reset_to_default=False, platform_logo_file=None, support_whatsapp=None,
    ):
        from app.utils.colors import normalize_hex
        from app.utils.uploads import PLATFORM_LOGO_DIMENSION, InvalidImageError, delete_product_image_file, save_platform_image

        if reset_to_default:
            self.settings.admin_theme_accent = None
        elif accent_color:
            normalized = normalize_hex(accent_color)
            if normalized:
                self.settings.admin_theme_accent = normalized

        self.settings.support_whatsapp = (support_whatsapp or "").strip() or None

        if platform_logo_file and platform_logo_file.filename:
            try:
                new_logo = save_platform_image("logo", platform_logo_file, max_dimension=PLATFORM_LOGO_DIMENSION)
            except InvalidImageError:
                raise
            else:
                if self.settings.platform_logo:
                    delete_product_image_file(self.settings.platform_logo)
                self.settings.platform_logo = new_logo

        db.session.commit()
        return self.settings

    def remove_platform_logo(self) -> PlatformSettings:
        """Ação isolada do ícone de remover no dropzone (mesmo padrão de
        `remove_landing_media`) — efeito imediato, não depende do envio
        do resto do formulário de Aparência."""
        from app.utils.uploads import delete_product_image_file

        if self.settings.platform_logo:
            delete_product_image_file(self.settings.platform_logo)
            self.settings.platform_logo = None
            db.session.commit()
        return self.settings

    def update_landing_content(
        self, *, hero_title: str, hero_subtitle: str, features: list[dict],
        hero_image_file=None, feature_image_files: list = None,
        hero_image_opacity: int = 100, hero_image_blur: int = 0,
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
        video_warning = None
        if hero_video_file and hero_video_file.filename:
            try:
                new_hero_video, compressed = save_platform_video("landing", hero_video_file)
            except InvalidVideoError:
                raise
            else:
                if hero_video:
                    delete_product_image_file(hero_video)
                hero_video = new_hero_video
                if not compressed:
                    video_warning = (
                        "O vídeo foi salvo sem otimização (o servidor não tem o ffmpeg "
                        "instalado, ou a compressão falhou/expirou) — sem isso o vídeo pode "
                        "demorar para começar a tocar ou travar em conexões mais lentas. "
                        "Instale o ffmpeg no servidor (ou rode o instalador de novo) para "
                        "otimizar automaticamente os próximos vídeos enviados."
                    )

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
            "hero_image_opacity": min(max(int(hero_image_opacity or 100), 10), 100),
            "hero_image_blur": min(max(int(hero_image_blur or 0), 0), 20),
            "hero_video": hero_video,
            "theme": theme if theme in ("chili", "blue", "light") else "chili",
            "features": new_features,
        }
        db.session.commit()
        # Atributo transiente (não é uma coluna do model, não é persistido)
        # só para o controller decidir se mostra o aviso de vídeo não
        # otimizado — ver settings_landing em controllers/admin/settings.py.
        self.settings.video_compression_warning = video_warning
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
