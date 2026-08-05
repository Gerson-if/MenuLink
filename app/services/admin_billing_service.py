"""
Regras de negócio de cobrança pelo Super Administrador.

Fluxo:
1. Cada tenant tem (ou ganha, na primeira fatura) uma Subscription ativa
   vinculada ao seu plano atual.
2. Faturas (Invoice) são lançadas manualmente pelo Super Admin.
3. Opcionalmente, se a integração com o Asaas estiver configurada
   (`ASAAS_API_KEY`), o Super Admin pode gerar uma cobrança real
   (boleto/Pix/cartão) para a fatura — ver `generate_asaas_charge`.
   Sem isso configurado, o fluxo manual (marcar como paga na mão)
   continua funcionando normalmente.
4. Marcar uma fatura como paga (manualmente, ou automaticamente via
   webhook do Asaas quando o pagamento é confirmado) é o gatilho que
   **libera automaticamente** um tenant que estava bloqueado por
   inadimplência.
"""

from calendar import monthrange
from datetime import date, datetime, timezone

from app.extensions import db
from app.models import BillingCycle, Invoice, InvoiceStatus, Subscription, SubscriptionStatus, TenantStatus
from app.repositories.billing_repository import InvoiceRepository, SubscriptionRepository
from app.services.payment_gateway import get_gateway
from app.services.payment_gateway.base import PaymentGatewayError


class BillingError(Exception):
    pass


def _add_cycle(base: date, cycle: "BillingCycle") -> date:
    """Soma um ciclo de cobrança a uma data respeitando o calendário de
    verdade — diferente de somar 30/365 dias fixos, "mensal" a partir de
    31/jan cai em 28 ou 29/fev (não em 3/mar), e assinaturas lançadas no
    fim do mês não vão "andando" pra frente a cada renovação."""
    if cycle == BillingCycle.YEARLY:
        try:
            return base.replace(year=base.year + 1)
        except ValueError:
            # 29/fev sem ano bissexto no ano seguinte -> cai pro dia 28.
            return base.replace(year=base.year + 1, day=28)

    month = base.month + 1
    year = base.year + (1 if month > 12 else 0)
    month = 1 if month > 12 else month
    last_day_of_month = monthrange(year, month)[1]
    return base.replace(year=year, month=month, day=min(base.day, last_day_of_month))


class AdminBillingService:
    def __init__(self):
        self.subscription_repo = SubscriptionRepository()
        self.invoice_repo = InvoiceRepository()

    def list_invoices_for_tenant(self, tenant_id: int):
        return self.invoice_repo.list_for_tenant(tenant_id)

    def get_invoice_or_404(self, invoice_id: int) -> Invoice:
        from flask import abort

        invoice = self.invoice_repo.get_by_id(invoice_id)
        if invoice is None:
            abort(404)
        return invoice

    def get_subscription_for_tenant(self, tenant_id: int) -> Subscription | None:
        """Só leitura — ao contrário de `get_or_create_subscription`, não
        cria nada. Usado para exibir a tela de detalhe do lojista (GET),
        onde criar uma Subscription como efeito colateral de uma simples
        visualização seria surpreendente."""
        return self.subscription_repo.get_active_for_tenant(tenant_id)

    def get_or_create_subscription(self, tenant, *, start_date: date | None = None) -> Subscription:
        """`start_date` permite criar a assinatura com um início diferente
        de hoje — usado por `activate_subscription_after_trial` para o
        primeiro ciclo começar exatamente em `trial_ends_at`, não no dia
        em que o job rodou (o lojista não perde os dias grátis restantes,
        ver TrialService)."""
        subscription = self.subscription_repo.get_active_for_tenant(tenant.id)
        if subscription is not None:
            return subscription

        if tenant.plan is None:
            raise BillingError("Este lojista não tem um plano associado. Defina um plano antes de lançar faturas.")

        period_start = start_date or date.today()
        billing_cycle = tenant.plan.billing_cycle

        subscription = Subscription(
            tenant_id=tenant.id,
            plan_id=tenant.plan_id,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=billing_cycle,
            current_period_start=period_start,
            current_period_end=_add_cycle(period_start, billing_cycle),
        )
        db.session.add(subscription)
        db.session.flush()
        return subscription

    def activate_subscription_after_trial(self, tenant, *, trial_ends_at: date) -> Subscription:
        """Chamado por TrialService quando o trial acaba e o lojista já
        tinha confirmado um plano pago (Tenant.plan_confirmed_at
        preenchido, ver TenantSettingsService.change_plan) — ativa o
        tenant e cria a assinatura com o ciclo começando exatamente no
        fim do trial, não no dia em que este job rodou."""
        tenant.status = TenantStatus.ACTIVE
        tenant.blocked_reason = None
        tenant.blocked_at = None
        subscription = self.get_or_create_subscription(tenant, start_date=trial_ends_at)
        db.session.commit()
        return subscription

    def update_subscription_cycle(self, subscription: Subscription, billing_cycle: BillingCycle) -> Subscription:
        """Sobrescreve o ciclo de cobrança só desta assinatura (por
        tenant), sem mexer no Plan (que continua valendo como padrão
        para assinaturas novas). Não recalcula `current_period_end` nem
        faturas já lançadas — o novo ciclo passa a valer a partir da
        próxima vez que `generate_next_invoice` for chamado."""
        subscription.billing_cycle = billing_cycle
        db.session.commit()
        return subscription

    def generate_next_invoice(self, subscription: Subscription) -> Invoice:
        """
        Gera a próxima fatura da assinatura automaticamente: valor =
        preço atual do plano, vencimento = data da última fatura (ou o
        fim do período corrente, se ainda não há nenhuma) + um ciclo de
        `subscription.billing_cycle` — poupando o Super Admin de
        calcular isso na mão a cada mês. Também avança o período
        corrente da assinatura para acompanhar.

        Validações (evitam os erros mais comuns de lançar fatura na
        mão): recusa se já existe uma fatura em aberto (pendente ou
        vencida) para esta assinatura — geraria cobrança duplicada — e
        recusa se o plano não tem preço definido.

        Antes de tudo, aplica um downgrade agendado (ver
        TenantSettingsService.change_plan / `pending_plan_id`), se
        houver um pendente para esta assinatura — é exatamente neste
        momento (início do próximo ciclo) que a troca de plano
        combinada deve entrar em vigor, então o valor calculado abaixo
        já reflete o plano novo (mais barato).
        """
        self._apply_pending_plan_change(subscription)

        plan = subscription.plan
        if plan is None:
            raise BillingError("Esta assinatura não tem um plano associado.")
        if plan.price_cents <= 0:
            raise BillingError(
                f"O plano '{plan.name}' não tem um preço definido (R$ 0,00) — configure um "
                "valor em Planos antes de gerar a próxima fatura automaticamente."
            )

        open_invoice = next(
            (inv for inv in subscription.invoices if inv.status in (InvoiceStatus.PENDING, InvoiceStatus.OVERDUE)),
            None,
        )
        if open_invoice is not None:
            raise BillingError(
                f"Já existe uma fatura em aberto (vencimento {open_invoice.due_date.strftime('%d/%m/%Y')}) "
                "para esta assinatura. Marque-a como paga, cancele-a, ou aguarde ela vencer antes de gerar a próxima."
            )

        last_invoice = max(subscription.invoices, key=lambda inv: inv.due_date, default=None)
        base_date = last_invoice.due_date if last_invoice else subscription.current_period_end
        next_due_date = _add_cycle(base_date, subscription.billing_cycle)

        invoice = Invoice(
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            amount_cents=plan.price_cents,
            status=InvoiceStatus.PENDING,
            due_date=next_due_date,
            description=f"Renovação do ciclo de cobrança — plano '{plan.name}'.",
        )
        db.session.add(invoice)

        subscription.current_period_start = base_date
        subscription.current_period_end = next_due_date

        db.session.commit()
        return invoice

    def _apply_pending_plan_change(self, subscription: Subscription) -> None:
        """Efetiva um downgrade agendado (ver `pending_plan_id`) se a
        data marcada já chegou — chamado por `generate_next_invoice`
        antes de calcular a próxima fatura. Mantém `tenant.plan_id`
        sincronizado com `subscription.plan_id`, mesmo motivo de
        TenantSettingsService.change_plan (AdminBillingService lê
        `subscription.plan`, mas o resto do painel/telas do lojista lê
        `tenant.plan`).

        Revalida os limites do plano de destino ANTES de aplicar —
        entre o pedido de downgrade e a renovação, o lojista continua
        oficialmente no plano premium (ver TenantSettingsService.
        change_plan) e pode ter cadastrado mais produtos/categorias
        nesse meio tempo, usando a cota maior. Se não couber mais,
        adia a troca por mais um ciclo (em vez de aplicar silenciosamente
        e deixar a loja acima do limite do plano novo) e avisa o
        lojista pelo chat de suporte, pra ele saber que precisa ajustar
        antes da próxima tentativa."""
        if subscription.pending_plan_id is None:
            return
        if subscription.pending_plan_change_at and subscription.pending_plan_change_at > date.today():
            return

        pending_plan = subscription.pending_plan
        tenant = subscription.tenant
        if pending_plan is not None and tenant is not None:
            from app.services.plan_limits import PlanLimitExceeded, check_plan_limits

            try:
                check_plan_limits(tenant, pending_plan)
            except PlanLimitExceeded:
                subscription.pending_plan_change_at = _add_cycle(
                    subscription.pending_plan_change_at or date.today(), subscription.billing_cycle,
                )
                from app.services.chat_service import ChatService

                ChatService.send_system_message(
                    tenant.id,
                    f"Sua troca agendada para o plano '{pending_plan.name}' não pôde ser aplicada nesta "
                    "renovação porque sua loja está usando mais produtos/categorias do que esse plano "
                    "permite. A troca foi adiada para o próximo ciclo — ajuste o quanto antes na sua "
                    "página de assinatura, ou exclua o excedente para a troca ser aplicada automaticamente.",
                )
                return

        subscription.plan_id = subscription.pending_plan_id
        if tenant is not None:
            tenant.plan_id = subscription.pending_plan_id
        subscription.pending_plan_id = None
        subscription.pending_plan_change_at = None

    def calculate_upgrade_proration_cents(self, subscription: Subscription, new_plan) -> int:
        """Valor proporcional (prorateio) cobrado imediatamente num
        upgrade no meio do ciclo: a diferença de preço entre o plano
        novo e o atual, multiplicada pela fração de dias que ainda
        faltam no ciclo corrente. Ex: upgrade de R$50 para R$100 na
        metade do ciclo -> cobra só R$25 agora (a diferença de R$50,
        pela metade dos dias restantes) — o cliente já pagou o resto do
        plano antigo, só completa a diferença até a próxima renovação
        (que já sai no preço cheio do novo plano).

        Nunca retorna um valor negativo (se por algum motivo o cálculo
        desse negativo, não cobra nada agora — evita gerar uma fatura
        de valor negativo por engano)."""
        today = date.today()
        total_days = (subscription.current_period_end - subscription.current_period_start).days
        if total_days <= 0:
            return max(0, new_plan.price_cents - subscription.plan.price_cents)

        remaining_days = max(0, (subscription.current_period_end - today).days)
        price_diff_cents = new_plan.price_cents - subscription.plan.price_cents
        prorated = round(price_diff_cents * remaining_days / total_days)
        return max(0, prorated)

    def create_invoice(self, tenant, *, amount_reais: float, due_date: date, description: str | None = None) -> Invoice:
        subscription = self.get_or_create_subscription(tenant)

        invoice = Invoice(
            tenant_id=tenant.id,
            subscription_id=subscription.id,
            amount_cents=round(amount_reais * 100),
            status=InvoiceStatus.PENDING,
            due_date=due_date,
            description=description or "Fatura lançada manualmente pelo Super Admin.",
        )
        db.session.add(invoice)
        db.session.commit()
        return invoice

    def mark_paid(self, invoice: Invoice):
        """Marca a fatura como paga e libera o tenant se ele estava
        bloqueado por inadimplência (validação de pagamento). Chamado
        tanto pelo webhook do Asaas (Pix confirmado automaticamente,
        ver app/controllers/webhooks_controller.py) quanto por uma
        marcação manual do Super Admin — nos dois casos, o lojista é
        avisado pelo chat de suporte (ver `_send_payment_confirmed_message`
        abaixo), que já é o canal "em tempo real" (polling HTMX a cada
        5s) usado no resto do app para avisos automáticos, ex.: lembretes
        de fim de período de teste em TrialService."""
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = datetime.now(timezone.utc)

        tenant = invoice.tenant
        if tenant.status == TenantStatus.BLOCKED_PAYMENT:
            tenant.status = TenantStatus.ACTIVE
            tenant.blocked_reason = None
            tenant.blocked_at = None

        if invoice.subscription.status == SubscriptionStatus.PAST_DUE:
            invoice.subscription.status = SubscriptionStatus.ACTIVE

        db.session.commit()
        self._send_payment_confirmed_message(invoice)
        return invoice

    def _send_payment_confirmed_message(self, invoice: Invoice) -> None:
        """Mensagem automática de confirmação de pagamento — a parte
        "em tempo real" pedida para o Pix: assim que o webhook do Asaas
        confirma o pagamento (ou o Super Admin marca manualmente), o
        lojista vê a mensagem aparecer no chat de suporte no próximo
        poll de 5s, com os dados da fatura paga (valor, vencimento,
        plano) sem precisar atualizar a página."""
        from app.services.chat_service import ChatService

        tenant = invoice.tenant
        if tenant is None:
            return

        plan = invoice.subscription.plan if invoice.subscription else tenant.plan
        plan_line = f" do plano '{plan.name}'" if plan else ""
        paid_at_label = invoice.paid_at.strftime("%d/%m/%Y às %H:%M") if invoice.paid_at else ""

        ChatService.send_system_message(
            tenant.id,
            f"✅ Pagamento confirmado! Recebemos R$ {invoice.amount_cents / 100:.2f} referente à "
            f"fatura de vencimento {invoice.due_date.strftime('%d/%m/%Y')}{plan_line}, "
            f"confirmado em {paid_at_label}. Sua assinatura está em dia — obrigado!",
        )

    def mark_overdue(self, invoice: Invoice):
        invoice.status = InvoiceStatus.OVERDUE
        if invoice.subscription:
            invoice.subscription.status = SubscriptionStatus.PAST_DUE
        db.session.commit()
        return invoice

    def cancel_invoice(self, invoice: Invoice):
        invoice.status = InvoiceStatus.CANCELED
        db.session.commit()
        return invoice

    def process_dunning(self, now: date | None = None) -> dict:
        """Ciclo de vida simples de inadimplência (dunning) para
        faturas de tenants já pagos (diferente de TrialService, que
        cuida só do período de teste): marca como vencida (OVERDUE)
        toda fatura PENDING cujo vencimento já passou, e bloqueia
        (BLOCKED_PAYMENT) o tenant cuja fatura estiver vencida há mais
        de `PlatformSettings.dunning_grace_days` dias sem pagamento —
        mesmo prazo de tolerância dado ao período de teste, só que
        aplicado a cada fatura de renovação. Chamado por `flask
        process-dunning` (ver app/cli.py), sem agendador embutido na
        aplicação — precisa de um cron/systemd timer no servidor.
        """
        from app.models.platform_settings import PlatformSettings
        from app.services.admin_tenant_service import AdminTenantService
        from app.services.chat_service import ChatService

        today = now or date.today()
        grace_days = PlatformSettings.get_or_create().dunning_grace_days

        summary = {"marked_overdue": 0, "blocked": 0}

        pending_invoices = Invoice.query.filter(
            Invoice.status == InvoiceStatus.PENDING, Invoice.due_date < today,
        ).all()
        for invoice in pending_invoices:
            self.mark_overdue(invoice)
            summary["marked_overdue"] += 1

        overdue_invoices = Invoice.query.filter(Invoice.status == InvoiceStatus.OVERDUE).all()
        tenant_service = AdminTenantService()
        for invoice in overdue_invoices:
            tenant = invoice.tenant
            if tenant is None or tenant.status != TenantStatus.ACTIVE:
                continue
            if (today - invoice.due_date).days < grace_days:
                continue

            tenant_service.block_for_payment(
                tenant,
                reason=f"Fatura vencida em {invoice.due_date.strftime('%d/%m/%Y')} sem pagamento "
                       f"(tolerância de {grace_days} dia(s) esgotada).",
            )
            ChatService.send_system_message(
                tenant.id,
                "O acesso ao painel foi pausado por uma fatura em aberto sem pagamento. Regularize "
                "sua situação na página de assinatura para voltar a usar o sistema normalmente.",
            )
            summary["blocked"] += 1

        return summary

    # --- Integração Asaas ---
    def is_asaas_configured(self) -> bool:
        return get_gateway() is not None

    def generate_asaas_charge(self, invoice: Invoice) -> Invoice:
        """
        Gera (ou reaproveita) uma cobrança real no Asaas para esta
        fatura, e salva o link de pagamento. Não marca a fatura como
        paga — isso só acontece quando o pagamento é de fato confirmado
        (webhook do Asaas, ver app/controllers/webhooks_controller.py,
        ou manualmente pelo Super Admin como já era possível antes).
        """
        gateway = get_gateway()
        if gateway is None:
            raise BillingError(
                "Integração com o Asaas não está configurada. Defina a variável "
                "de ambiente ASAAS_API_KEY para habilitar cobrança automática."
            )

        if invoice.asaas_payment_id:
            raise BillingError("Esta fatura já tem uma cobrança Asaas gerada.")

        if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.OVERDUE):
            raise BillingError("Só é possível gerar cobrança Asaas para uma fatura pendente ou vencida.")

        if invoice.due_date < date.today():
            raise BillingError(
                "O vencimento desta fatura já passou — o Asaas não aceita gerar uma cobrança com "
                "data retroativa. Ajuste o vencimento (cancele e lance de novo) antes de gerar a cobrança."
            )

        if not (invoice.tenant.email or "").strip():
            raise BillingError(
                "Esta loja não tem e-mail cadastrado — o Asaas exige um e-mail para criar o "
                "cliente de cobrança. Preencha o e-mail em Editar dados antes de gerar a cobrança."
            )

        # O Asaas também exige CPF/CNPJ pra criar o cliente de cobrança
        # (ver AsaasGateway.ensure_customer) — sem isso cadastrado, a
        # chamada real à API falharia lá na frente com um erro genérico
        # do gateway. Barrar aqui, cedo, dá uma mensagem clara de qual
        # dado falta e onde completar (o próprio lojista edita isso em
        # Configurações da loja, ver TenantSettingsService.update_store_info).
        if not (invoice.tenant.document or "").strip():
            raise BillingError(
                "Esta loja não tem CPF/CNPJ cadastrado — o Asaas exige um documento válido para "
                "criar o cliente de cobrança. Peça para o lojista preencher em Configurações > "
                "Dados da loja antes de gerar a cobrança."
            )

        try:
            charge = gateway.create_charge(invoice.tenant, invoice)
        except PaymentGatewayError as exc:
            raise BillingError(str(exc)) from exc

        invoice.asaas_payment_id = charge["id"]
        invoice.payment_link_url = charge.get("payment_url")
        db.session.commit()
        return invoice

    def mark_paid_by_asaas_payment_id(self, asaas_payment_id: str) -> Invoice | None:
        """Usado pelo webhook: localiza a fatura pelo ID da cobrança no
        Asaas e marca como paga. Retorna None se não encontrar (ex:
        webhook de uma cobrança que não foi gerada por este sistema —
        ignorado silenciosamente, não é um erro)."""
        invoice = Invoice.query.filter_by(asaas_payment_id=asaas_payment_id).first()
        if invoice is None:
            return None
        if invoice.status == InvoiceStatus.PAID:
            return invoice  # já processado (webhook pode reenviar o mesmo evento)
        return self.mark_paid(invoice)
