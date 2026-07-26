"""
Assinatura (Subscription) e Cobrança (Invoice) de cada tenant.

Fluxo básico:
1. Tenant contrata um Plan -> cria-se uma Subscription com um período
   corrente (current_period_start/end).
2. A cada ciclo, gera-se uma Invoice (fatura) com due_date.
3. Se a Invoice vencer sem pagamento -> Super Admin bloqueia o tenant
   (Tenant.status = BLOCKED_PAYMENT).
4. Quando o pagamento é validado -> Invoice marcada como PAID e o
   tenant é reativado (Tenant.status = ACTIVE).

Esse histórico completo (Subscription + Invoice) é o que alimenta a tela
do Super Admin de "gerenciar planos, cobranças e pagamentos".
"""

import enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, str_enum
from app.models.tenant import BillingCycle


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class InvoiceStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELED = "canceled"


class Subscription(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    status = Column(str_enum(SubscriptionStatus, "subscription_status"), nullable=False, default=SubscriptionStatus.ACTIVE)
    current_period_start = Column(Date, nullable=False)
    current_period_end = Column(Date, nullable=False)

    # Ciclo de cobrança desta assinatura — copiado do Plan no momento em
    # que a Subscription é criada, mas o Super Admin pode depois
    # sobrescrever por tenant (ver AdminBillingService.update_subscription_cycle),
    # sem afetar o Plan (que continua valendo como padrão para novas
    # assinaturas). É este campo, não `plan.billing_cycle`, que
    # `generate_next_invoice` usa para calcular a próxima fatura.
    billing_cycle = Column(str_enum(BillingCycle, "billing_cycle"), nullable=False, default=BillingCycle.MONTHLY)

    # Downgrade agendado (nunca imediato — ver regra de negócio em
    # TenantSettingsService.change_plan): quando o lojista pede pra
    # trocar para um plano mais barato, a troca de verdade só é
    # aplicada no fim do ciclo já pago (evitando estorno em dinheiro).
    # Até lá, `plan_id` acima continua sendo o plano premium atual, e
    # estes dois campos guardam a intenção. `AdminBillingService.
    # generate_next_invoice` aplica a troca (copia pending_plan_id pra
    # plan_id e limpa os dois) antes de calcular o valor da próxima
    # fatura.
    pending_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    pending_plan_change_at = Column(Date, nullable=True)

    tenant = relationship("Tenant", back_populates="subscriptions")
    plan = relationship("Plan", foreign_keys=[plan_id])
    pending_plan = relationship("Plan", foreign_keys=[pending_plan_id])
    invoices = relationship("Invoice", back_populates="subscription", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("current_period_end > current_period_start", name="ck_subscription_period_valid"),
    )


class Invoice(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)

    amount_cents = Column(Integer, nullable=False)
    status = Column(str_enum(InvoiceStatus, "invoice_status"), nullable=False, default=InvoiceStatus.PENDING, index=True)
    due_date = Column(Date, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # Explicação em texto simples de POR QUE esta fatura existe (ex:
    # "renovação mensal", "prorateio de upgrade", "lançada manualmente
    # pelo Super Admin") — mostrada na tela de detalhes da fatura do
    # lojista (ver lojista/settings/invoice_detail.html), pra ele
    # entender a origem do valor sem precisar perguntar ao suporte.
    # Nullable: faturas antigas (de antes deste campo existir) caem no
    # texto padrão do template.
    description = Column(Text, nullable=True)

    # Integração de cobrança (Asaas) — preenchido quando o Super Admin
    # gera uma cobrança para esta fatura. `asaas_payment_id` é a chave
    # usada para casar o webhook de confirmação de pagamento com esta
    # Invoice (ver app/controllers/webhooks_controller.py).
    asaas_payment_id = Column(String(60), nullable=True, index=True)
    payment_link_url = Column(String(500), nullable=True)

    tenant = relationship("Tenant", back_populates="invoices")
    subscription = relationship("Subscription", back_populates="invoices")

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_invoice_amount_non_negative"),
    )
