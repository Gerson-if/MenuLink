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


class PlanChangeResult:
    """Retorno de `TenantSettingsService.change_plan` — carrega o
    suficiente pra quem chamou (ver
    app/controllers/lojista/settings.py) montar uma mensagem clara e
    específica sobre o que de fato aconteceu (cada tipo de troca tem
    implicações bem diferentes: uma cobrança na hora, uma troca
    agendada sem estorno, ou só uma intenção registrada durante o
    trial), em vez de um aviso genérico que deixaria os detalhes
    financeiros por conta do lojista adivinhar.

    `kind` é um dos: "trial_confirmed", "no_subscription", "reaffirmed",
    "upgrade", "downgrade_scheduled"."""

    def __init__(
        self, *, tenant, plan, kind: str, current_plan=None,
        proration_cents: int = 0, effective_date=None,
    ):
        self.tenant = tenant
        self.plan = plan  # plano solicitado nesta chamada
        self.kind = kind
        self.current_plan = current_plan  # plano que continua valendo até `effective_date` (downgrade)
        self.proration_cents = proration_cents
        self.effective_date = effective_date


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
            return PlanChangeResult(tenant=self.tenant, plan=plan, kind="trial_confirmed")

        from app.repositories.billing_repository import SubscriptionRepository

        subscription = SubscriptionRepository().get_active_for_tenant(self.tenant.id)
        if subscription is None:
            self.tenant.plan_id = plan.id
            db.session.commit()
            return PlanChangeResult(tenant=self.tenant, plan=plan, kind="no_subscription")

        current_plan = subscription.plan
        if current_plan is not None and plan.id == current_plan.id:
            # O lojista está reafirmando o plano que já está em vigor —
            # se havia um downgrade agendado pendente (ex: ele tinha
            # pedido pra voltar pro plano menor e mudou de ideia),
            # cancela: continuar no plano atual "pra sempre" (até uma
            # nova troca de verdade) é o que ele está pedindo agora.
            had_pending = subscription.pending_plan_id is not None
            if had_pending:
                subscription.pending_plan_id = None
                subscription.pending_plan_change_at = None
                db.session.commit()
            return PlanChangeResult(
                tenant=self.tenant, plan=plan, kind="reaffirmed_canceled_downgrade" if had_pending else "reaffirmed",
            )

        if current_plan is None or plan.price_cents > current_plan.price_cents:
            proration_cents = self._apply_upgrade(subscription, plan)
            result = PlanChangeResult(
                tenant=self.tenant, plan=plan, kind="upgrade", proration_cents=proration_cents,
            )
        else:
            self._schedule_downgrade(subscription, plan)
            result = PlanChangeResult(
                tenant=self.tenant, plan=plan, kind="downgrade_scheduled",
                current_plan=current_plan, effective_date=subscription.pending_plan_change_at,
            )

        db.session.commit()
        return result

    def _apply_upgrade(self, subscription, plan) -> int:
        """Upgrade: liberado na hora, com cobrança prorateada pela
        diferença de valor nos dias restantes do ciclo atual (ver
        AdminBillingService.calculate_upgrade_proration_cents). Se
        havia um downgrade agendado pendente (o lojista mudou de ideia
        antes dele entrar em vigor), cancela — o upgrade agora é o que
        vale. Retorna o valor (em centavos) da cobrança prorateada
        gerada (0 se não houve, ex: diferença de preço nula)."""
        from app.services.admin_billing_service import AdminBillingService

        billing = AdminBillingService()
        proration_cents = billing.calculate_upgrade_proration_cents(subscription, plan)
        old_plan_name = subscription.plan.name if subscription.plan else "seu plano anterior"

        subscription.plan_id = plan.id
        subscription.pending_plan_id = None
        subscription.pending_plan_change_at = None
        self.tenant.plan_id = plan.id

        if proration_cents > 0:
            from app.models import Invoice, InvoiceStatus

            description = (
                f"Cobrança prorateada pelo upgrade de '{old_plan_name}' para '{plan.name}' — referente "
                "aos dias restantes do ciclo atual (você já tinha pago o resto do ciclo no plano anterior)."
            )
            invoice = Invoice(
                tenant_id=self.tenant.id,
                subscription_id=subscription.id,
                amount_cents=proration_cents,
                status=InvoiceStatus.PENDING,
                due_date=subscription.current_period_end,
                description=description,
            )
            db.session.add(invoice)

        return proration_cents

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
        from app.services.plan_limits import PlanLimitExceeded, check_plan_limits

        try:
            check_plan_limits(self.tenant, plan)
        except PlanLimitExceeded as exc:
            raise TenantSettingsError(str(exc)) from exc

    def update_store_info(
        self, *, name: str, whatsapp_number: str, phone: str, logo_file_storage=None,
        address_street: str | None = None, address_number: str | None = None,
        address_neighborhood: str | None = None, address_city: str | None = None,
        menu_tagline: str | None = None, legal_name: str | None = None, document: str | None = None,
    ):
        from app.utils.validators import only_digits

        self.tenant.name = name.strip()
        self.tenant.menu_tagline = (menu_tagline or "").strip() or None
        self.tenant.whatsapp_number = (whatsapp_number or "").strip() or None
        self.tenant.phone = (phone or "").strip() or None
        self.tenant.address_street = (address_street or "").strip() or None
        self.tenant.address_number = (address_number or "").strip() or None
        self.tenant.address_neighborhood = (address_neighborhood or "").strip() or None
        self.tenant.address_city = (address_city or "").strip() or None
        self.tenant.legal_name = (legal_name or "").strip() or None
        # Guarda só os dígitos — é o formato que o Asaas espera em
        # `cpfCnpj` (ver AsaasGateway.ensure_customer) e evita
        # inconsistência de formatação (com/sem pontuação) entre
        # cadastros diferentes.
        self.tenant.document = only_digits(document) or None

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

    def update_appearance(self, *, accent_color=None, reset_to_default=False, theme_mode=None, panel_theme=None):
        """
        `theme_settings` é um JSON livre (pensado pra "cores, fontes
        etc." desde que o campo foi criado) — guarda a cor de destaque e
        o modo (claro/escuro) escolhidos pelo lojista para o CARDÁPIO
        PÚBLICO, e também (chave `panel_theme`) o tema escolhido pelo
        lojista para a PRÓPRIA área de acesso (painel de gestão) — ver
        Tenant.panel_theme. Usa merge em vez de sobrescrever o dict
        inteiro, para não perder outras chaves que venham a existir aí
        no futuro.
        """
        from app.models.platform_settings import SYSTEM_THEME_CHOICES
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

        valid_panel_themes = {key for key, _label in SYSTEM_THEME_CHOICES}
        if panel_theme in valid_panel_themes:
            settings["panel_theme"] = panel_theme
        else:
            settings.pop("panel_theme", None)

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
