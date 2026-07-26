"""
Regras de negócio para o próprio lojista editar os dados da sua loja
(diferente de AdminTenantService, que é exclusivo do Super Admin e
gerencia QUALQUER tenant — este serviço só é chamado com o tenant da
sessão atual, nunca com um tenant_id vindo de fora)."""

from datetime import datetime, timezone

from app.extensions import db
from app.models import TenantStatus
from app.models.tenant import WEEKDAY_KEYS
from app.repositories.plan_repository import PlanRepository
from app.repositories.tenant_repository import TenantRepository
from app.utils.slugs import generate_unique_slug
from app.utils.uploads import save_tenant_logo, delete_product_image_file, InvalidImageError


class TenantSettingsError(Exception):
    pass


class TenantSettingsService:
    def __init__(self, tenant):
        self.tenant = tenant
        self.repo = TenantRepository()
        self.plan_repo = PlanRepository()

    def change_plan(self, new_plan_id: int):
        """O próprio lojista escolhe/troca de plano (ver
        lojista/settings/subscription.html) — SÓ é aplicado quando ele
        confirma explicitamente pelo modal de confirmação do formulário
        (data-confirm-message), nunca automaticamente. Isso evita tanto
        loja com plano nulo (toda loja nova já cai no mais barato, ver
        AdminTenantService.create) quanto uma troca acidental de plano
        sem o lojista perceber.

        Só permite planos ATIVOS (disponíveis para contratação) — um
        plano desativado pelo Super Admin não deve poder ser escolhido
        por quem ainda não o tinha.

        O que acontece depois depende do estado da loja:

        - Ainda em TRIAL: nenhuma assinatura/cobrança é criada agora —
          só registra a intenção (`plan_confirmed_at`). O primeiro
          ciclo de cobrança só começa no dia seguinte ao fim do teste
          (ver TrialService/AdminBillingService.activate_subscription_after_trial),
          então o lojista não perde os dias grátis restantes.
        - Já ACTIVE, mas sem assinatura lançada ainda: só troca
          `tenant.plan_id` (mesmo comportamento de antes) — a
          assinatura nasce lazy na primeira fatura.
        - Já ACTIVE com assinatura em andamento: upgrade (plano mais
          caro) é liberado IMEDIATAMENTE e gera uma fatura prorateada
          pela diferença dos dias restantes do ciclo. Downgrade (plano
          mais barato) exige que o uso atual já caiba no plano menor
          (senão pede pra excluir produtos/categorias antes) e, se
          couber, só entra em vigor no fim do ciclo já pago — sem
          estorno em dinheiro (ver `AdminBillingService.generate_next_invoice`,
          que aplica a troca agendada nesse momento)."""
        plan = self.plan_repo.get_by_id(new_plan_id)
        if plan is None or not plan.is_active:
            raise TenantSettingsError("Plano indisponível para contratação no momento.")

        if self.tenant.status == TenantStatus.TRIAL:
            self.tenant.plan_id = plan.id
            self.tenant.plan_confirmed_at = datetime.now(timezone.utc)
            db.session.commit()
            return self.tenant

        from app.repositories.billing_repository import SubscriptionRepository

        subscription = SubscriptionRepository().get_active_for_tenant(self.tenant.id)
        if subscription is None:
            self.tenant.plan_id = plan.id
            db.session.commit()
            return self.tenant

        current_plan = subscription.plan
        if current_plan is not None and plan.id == current_plan.id:
            return self.tenant

        if current_plan is None or plan.price_cents > current_plan.price_cents:
            self._apply_upgrade(subscription, plan)
        else:
            self._schedule_downgrade(subscription, plan)

        db.session.commit()
        return self.tenant

    def _apply_upgrade(self, subscription, plan) -> None:
        """Upgrade: liberado na hora, com cobrança prorateada pela
        diferença de valor nos dias restantes do ciclo atual (ver
        AdminBillingService.calculate_upgrade_proration_cents). Se
        havia um downgrade agendado pendente (o lojista mudou de ideia
        antes dele entrar em vigor), cancela — o upgrade agora é o que
        vale."""
        from app.services.admin_billing_service import AdminBillingService

        billing = AdminBillingService()
        proration_cents = billing.calculate_upgrade_proration_cents(subscription, plan)

        subscription.plan_id = plan.id
        subscription.pending_plan_id = None
        subscription.pending_plan_change_at = None
        self.tenant.plan_id = plan.id

        if proration_cents > 0:
            from app.models import Invoice, InvoiceStatus

            invoice = Invoice(
                tenant_id=self.tenant.id,
                subscription_id=subscription.id,
                amount_cents=proration_cents,
                status=InvoiceStatus.PENDING,
                due_date=subscription.current_period_end,
            )
            db.session.add(invoice)

    def _schedule_downgrade(self, subscription, plan) -> None:
        """Downgrade: exige que o uso atual já caiba nos limites do
        plano menor (senão o lojista precisa excluir produtos/categorias
        primeiro). Se couber, não muda nada agora — só agenda a troca
        para o fim do ciclo já pago (sem estorno em dinheiro): o
        lojista continua no plano premium até lá, e
        AdminBillingService.generate_next_invoice aplica a troca na
        próxima renovação."""
        self._validate_downgrade_limits(plan)
        subscription.pending_plan_id = plan.id
        subscription.pending_plan_change_at = subscription.current_period_end

    def _validate_downgrade_limits(self, plan) -> None:
        from app.repositories.category_repository import CategoryRepository
        from app.repositories.product_repository import ProductRepository

        if plan.max_categories is not None:
            category_count = CategoryRepository(self.tenant.id).count()
            if category_count > plan.max_categories:
                raise TenantSettingsError(
                    f"O plano '{plan.name}' permite no máximo {plan.max_categories} categoria(s), mas "
                    f"sua loja tem {category_count} cadastrada(s). Exclua o excedente antes de trocar de plano."
                )

        if plan.max_products is not None:
            product_count = ProductRepository(self.tenant.id).count_completed()
            if product_count > plan.max_products:
                raise TenantSettingsError(
                    f"O plano '{plan.name}' permite no máximo {plan.max_products} produto(s), mas "
                    f"sua loja tem {product_count} cadastrado(s). Exclua o excedente antes de trocar de plano."
                )

    def update_store_info(
        self, *, name: str, whatsapp_number: str, phone: str, logo_file_storage=None,
        address_street: str | None = None, address_number: str | None = None,
        address_neighborhood: str | None = None, address_city: str | None = None,
        menu_tagline: str | None = None,
    ):
        self.tenant.name = name.strip()
        self.tenant.menu_tagline = (menu_tagline or "").strip() or None
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
