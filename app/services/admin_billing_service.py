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
from datetime import date, datetime, timedelta, timezone

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

    def get_or_create_subscription(self, tenant) -> Subscription:
        subscription = self.subscription_repo.get_active_for_tenant(tenant.id)
        if subscription is not None:
            return subscription

        if tenant.plan is None:
            raise BillingError("Este lojista não tem um plano associado. Defina um plano antes de lançar faturas.")

        today = date.today()
        billing_cycle = tenant.plan.billing_cycle

        subscription = Subscription(
            tenant_id=tenant.id,
            plan_id=tenant.plan_id,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=billing_cycle,
            current_period_start=today,
            current_period_end=_add_cycle(today, billing_cycle),
        )
        db.session.add(subscription)
        db.session.flush()
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
        """
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
        )
        db.session.add(invoice)

        subscription.current_period_start = base_date
        subscription.current_period_end = next_due_date

        db.session.commit()
        return invoice

    def create_invoice(self, tenant, *, amount_reais: float, due_date: date) -> Invoice:
        subscription = self.get_or_create_subscription(tenant)

        invoice = Invoice(
            tenant_id=tenant.id,
            subscription_id=subscription.id,
            amount_cents=round(amount_reais * 100),
            status=InvoiceStatus.PENDING,
            due_date=due_date,
        )
        db.session.add(invoice)
        db.session.commit()
        return invoice

    def mark_paid(self, invoice: Invoice):
        """Marca a fatura como paga e libera o tenant se ele estava
        bloqueado por inadimplência (validação de pagamento)."""
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
        return invoice

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
