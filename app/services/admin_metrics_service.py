"""
Métricas agregadas do sistema inteiro pro dashboard do Super Admin
(app/controllers/admin/dashboard.py) — visão de negócio (crescimento,
receita, saúde da base de lojistas), diferente dos relatórios de venda
por loja (ver ReportService, que é por tenant).

Mesmo princípio de portabilidade SQLite/PostgreSQL do ReportService:
agregações simples (SUM/COUNT/GROUP BY) em SQL, séries mensais
bucketizadas em Python (evita função de data específica de dialeto —
a janela é sempre pequena, 12 meses, então o custo é desprezível).
"""

from calendar import monthrange
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func

from app.extensions import db
from app.models import (
    BillingCycle, Invoice, InvoiceStatus, Plan, Subscription, SubscriptionStatus, Tenant, TenantStatus,
)


def _aware_utc(dt: datetime) -> datetime:
    """Mesma normalização do ReportService — SQLite devolve datetime
    "naive" mesmo pra colunas DateTime(timezone=True); PostgreSQL
    preserva o timezone. Torna comparações seguras nos dois ambientes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_label(d: date) -> str:
    return f"{d.month:02d}/{d.year % 100:02d}"


def _last_n_month_starts(n: int) -> list[date]:
    """Primeiro dia de cada um dos últimos `n` meses, do mais antigo pro
    mais recente (inclui o mês corrente)."""
    today = datetime.now(timezone.utc).date()
    months = []
    year, month = today.year, today.month
    for _ in range(n):
        months.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


class AdminMetricsService:
    def tenant_kpis(self) -> dict:
        """Contagens usadas nos cards do topo — cada uma precisa bater
        exatamente com o filtro da tela pra onde o card leva (ver
        admin/tenants/list.html), senão o número promete um total que a
        lista filtrada não confirma."""
        today = datetime.now(timezone.utc).date()
        month_start = today.replace(day=1)

        total_tenants = Tenant.query.count()
        active_tenants = Tenant.query.filter_by(status=TenantStatus.ACTIVE).count()
        blocked_tenants = Tenant.query.filter_by(status=TenantStatus.BLOCKED_PAYMENT).count()
        new_this_month = Tenant.query.filter(
            func.date(Tenant.created_at) >= month_start
        ).count()

        return {
            "total_tenants": total_tenants,
            "active_tenants": active_tenants,
            "blocked_tenants": blocked_tenants,
            "new_this_month": new_this_month,
        }

    def mrr_cents(self) -> int:
        """Receita mensal recorrente: soma do preço do plano de cada
        assinatura ATIVA de um tenant ATIVO (paga de verdade, não em
        teste/bloqueado/suspenso/cancelado), normalizado pra mensal —
        assinatura anual entra dividida por 12. Reflete o plano ATUAL da
        assinatura (não o valor histórico de faturas já pagas), então
        muda na hora se o lojista faz upgrade/downgrade."""
        rows = (
            db.session.query(Plan.price_cents, Subscription.billing_cycle)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .join(Tenant, Tenant.id == Subscription.tenant_id)
            .filter(Subscription.status == SubscriptionStatus.ACTIVE, Tenant.status == TenantStatus.ACTIVE)
            .all()
        )
        total = 0
        for price_cents, billing_cycle in rows:
            total += price_cents // 12 if billing_cycle == BillingCycle.YEARLY else price_cents
        return total

    def open_invoices_cents(self) -> int:
        """Valor total ainda a receber (faturas pendentes ou vencidas de
        TODOS os lojistas) — o que o Super Admin efetivamente tem "preso"
        aguardando pagamento agora."""
        total = (
            db.session.query(func.coalesce(func.sum(Invoice.amount_cents), 0))
            .filter(Invoice.status.in_((InvoiceStatus.PENDING, InvoiceStatus.OVERDUE)))
            .scalar()
        )
        return int(total or 0)

    def tenant_growth_series(self, months: int = 12) -> list[dict]:
        """Novos lojistas cadastrados por mês, últimos `months` meses
        (inclui o corrente) — carrega só (id, created_at) e bucketiza em
        Python, mesmo padrão do ReportService.daily_revenue_series."""
        month_starts = _last_n_month_starts(months)
        window_start = month_starts[0]

        rows = (
            db.session.query(Tenant.created_at)
            .filter(func.date(Tenant.created_at) >= window_start)
            .all()
        )

        counts = OrderedDict((_month_key(m), 0) for m in month_starts)
        for (created_at,) in rows:
            key = _month_key(_aware_utc(created_at).date())
            if key in counts:
                counts[key] += 1

        return [
            {"month": _month_label(m), "count": counts[_month_key(m)]}
            for m in month_starts
        ]

    def revenue_series(self, months: int = 12) -> list[dict]:
        """Receita EFETIVAMENTE recebida por mês (faturas pagas, por
        `paid_at`) — diferente de MRR (que é a recorrência esperada daqui
        pra frente), isto é o que já entrou de verdade, incluindo
        cobranças avulsas/prorateio, não só assinatura recorrente."""
        month_starts = _last_n_month_starts(months)
        window_start = month_starts[0]

        rows = (
            db.session.query(Invoice.paid_at, Invoice.amount_cents)
            .filter(Invoice.status == InvoiceStatus.PAID, Invoice.paid_at.isnot(None))
            .filter(func.date(Invoice.paid_at) >= window_start)
            .all()
        )

        totals = OrderedDict((_month_key(m), 0) for m in month_starts)
        for paid_at, amount_cents in rows:
            key = _month_key(_aware_utc(paid_at).date())
            if key in totals:
                totals[key] += amount_cents

        return [
            {"month": _month_label(m), "revenue_cents": totals[_month_key(m)]}
            for m in month_starts
        ]

    def tenants_by_status(self) -> list[dict]:
        """Distribuição de TODOS os tenants pelos status possíveis —
        sempre inclui os 5 status (mesmo com contagem 0), pra ordem e
        cor do gráfico ficarem estáveis independente dos dados."""
        rows = dict(
            db.session.query(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status).all()
        )
        order = [
            (TenantStatus.TRIAL, "Em teste"),
            (TenantStatus.ACTIVE, "Ativa"),
            (TenantStatus.BLOCKED_PAYMENT, "Bloqueada"),
            (TenantStatus.SUSPENDED, "Suspensa"),
            (TenantStatus.CANCELED, "Cancelada"),
        ]
        return [{"status": label, "count": rows.get(status, 0)} for status, label in order]

    def tenants_by_plan(self) -> list[dict]:
        """Quantos tenants ATIVOS (pagantes) em cada plano — planos sem
        nenhum tenant ativo não aparecem (evita poluir o gráfico com
        planos antigos desativados e nunca contratados)."""
        rows = (
            db.session.query(Plan.name, func.count(Tenant.id))
            .join(Tenant, Tenant.plan_id == Plan.id)
            .filter(Tenant.status == TenantStatus.ACTIVE)
            .group_by(Plan.id, Plan.name)
            .order_by(func.count(Tenant.id).desc())
            .all()
        )
        return [{"plan": name, "count": count} for name, count in rows]
