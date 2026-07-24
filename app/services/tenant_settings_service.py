"""
Regras de negócio para o próprio lojista editar os dados da sua loja
(diferente de AdminTenantService, que é exclusivo do Super Admin e
gerencia QUALQUER tenant — este serviço só é chamado com o tenant da
sessão atual, nunca com um tenant_id vindo de fora)."""

from app.extensions import db
from app.models.tenant import WEEKDAY_KEYS
from app.repositories.tenant_repository import TenantRepository
from app.utils.slugs import generate_unique_slug
from app.utils.uploads import save_tenant_logo, delete_product_image_file, InvalidImageError


class TenantSettingsError(Exception):
    pass


class TenantSettingsService:
    def __init__(self, tenant):
        self.tenant = tenant
        self.repo = TenantRepository()

    def update_store_info(
        self, *, name: str, whatsapp_number: str, phone: str, logo_file_storage=None,
        address_street: str | None = None, address_number: str | None = None,
        address_neighborhood: str | None = None, address_city: str | None = None,
    ):
        self.tenant.name = name.strip()
        self.tenant.whatsapp_number = (whatsapp_number or "").strip() or None
        self.tenant.phone = (phone or "").strip() or None
        self.tenant.address_street = (address_street or "").strip() or None
        self.tenant.address_number = (address_number or "").strip() or None
        self.tenant.address_neighborhood = (address_neighborhood or "").strip() or None
        self.tenant.address_city = (address_city or "").strip() or None

        if logo_file_storage and logo_file_storage.filename:
            try:
                old_logo = self.tenant.logo_path
                self.tenant.logo_path = save_tenant_logo(self.tenant.id, logo_file_storage)
            except InvalidImageError:
                raise
            else:
                if old_logo:
                    delete_product_image_file(old_logo)

        db.session.commit()
        return self.tenant

    def remove_logo(self):
        """Ação isolada, disparada pelo ícone de remover no dropzone —
        efeito imediato, sem depender do envio do resto do formulário
        de dados da loja."""
        if self.tenant.logo_path:
            delete_product_image_file(self.tenant.logo_path)
            self.tenant.logo_path = None
            db.session.commit()
        return self.tenant

    def update_menu_settings(
        self, *, slug: str, pickup_enabled: bool, delivery_enabled: bool, show_price_from_label: bool = True,
        notes_placeholder: str | None = None,
    ):
        if slug != self.tenant.slug:
            existing = self.repo.get_by_slug(slug)
            if existing and existing.id != self.tenant.id:
                raise TenantSettingsError("Esse endereço já está em uso por outra loja.")
            self.tenant.slug = generate_unique_slug(slug, self.repo.get_by_slug, current_id=self.tenant.id)

        if not pickup_enabled and not delivery_enabled:
            raise TenantSettingsError("Habilite ao menos uma forma de recebimento (retirada ou entrega).")

        self.tenant.pickup_enabled = pickup_enabled
        self.tenant.delivery_enabled = delivery_enabled
        self.tenant.show_price_from_label = show_price_from_label

        # Mesmo padrão de merge de update_appearance() — theme_settings é
        # um JSON livre compartilhado, não sobrescrevemos as outras chaves
        # (ex: "accent", "mode") que já possam estar lá.
        settings = dict(self.tenant.theme_settings or {})
        if (notes_placeholder or "").strip():
            settings["notes_placeholder"] = notes_placeholder.strip()
        else:
            settings.pop("notes_placeholder", None)
        self.tenant.theme_settings = settings or None

        db.session.commit()
        return self.tenant

    def update_checkout_settings(
        self, *, delivery_fee_reais, free_delivery_above_reais, min_order_reais,
        accept_pix=True, accept_card=True, accept_cash=True, accept_other=True,
    ):
        self.tenant.delivery_fee_cents = round(delivery_fee_reais * 100) if delivery_fee_reais is not None else None
        self.tenant.free_delivery_above_cents = round(free_delivery_above_reais * 100) if free_delivery_above_reais is not None else None
        self.tenant.min_order_cents = round(min_order_reais * 100) if min_order_reais is not None else None
        self.tenant.accept_pix = accept_pix
        self.tenant.accept_card = accept_card
        self.tenant.accept_cash = accept_cash
        self.tenant.accept_other = accept_other
        db.session.commit()
        return self.tenant

    def update_appearance(self, *, accent_color=None, reset_to_default=False, theme_mode=None):
        """
        `theme_settings` é um JSON livre (pensado pra "cores, fontes
        etc." desde que o campo foi criado) — guarda a cor de destaque e
        o modo (claro/escuro) escolhidos pelo lojista para o cardápio
        público. Usa merge em vez de sobrescrever o dict inteiro, para
        não perder outras chaves que venham a existir aí no futuro.
        """
        from app.utils.colors import normalize_hex

        settings = dict(self.tenant.theme_settings or {})
        if reset_to_default:
            settings.pop("accent", None)
        elif accent_color:
            normalized = normalize_hex(accent_color)
            if normalized:
                settings["accent"] = normalized
        if theme_mode == "light":
            settings["mode"] = "light"
        else:
            settings.pop("mode", None)
        self.tenant.theme_settings = settings or None
        db.session.commit()
        return self.tenant

    def update_opening_hours(self, days: dict) -> None:
        """
        `days`: dict {chave_do_dia: {"closed": bool, "open": str, "close": str}}
        vindo do OpeningHoursForm (um FormField por dia). Um dia só é
        salvo como "aberto" se tiver abertura E fechamento válidos; caso
        contrário é salvo como fechado — evita um horário incompleto
        quebrar silenciosamente o cálculo de "aberto agora" no cardápio.
        """
        hours = {}
        for key in WEEKDAY_KEYS:
            day = days.get(key) or {}
            open_time = (day.get("open") or "").strip()
            close_time = (day.get("close") or "").strip()
            if not day.get("closed") and open_time and close_time:
                hours[key] = {"open": open_time, "close": close_time}
            else:
                hours[key] = {"closed": True}

        self.tenant.opening_hours = hours
        db.session.commit()
        return self.tenant
