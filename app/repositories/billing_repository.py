from app.models import Invoice, InvoiceStatus, Subscription
from app.repositories.base_repository import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    def get_active_for_tenant(self, tenant_id: int):
        return (
            self.model.query.filter_by(tenant_id=tenant_id)
            .order_by(Subscription.current_period_end.desc())
            .first()
        )


class InvoiceRepository(BaseRepository[Invoice]):
    model = Invoice

    def list_for_tenant(self, tenant_id: int):
        return (
            self.model.query.filter_by(tenant_id=tenant_id)
            .order_by(Invoice.due_date.desc())
            .all()
        )

    def count_open_for_tenant(self, tenant_id: int) -> int:
        """Quantas faturas do PRÓPRIO lojista ainda estão em aberto
        (pendente ou vencida) — usado pro selo de "Assinatura" na
        sidebar (ver lojista_pending_invoices_count em app/__init__.py),
        mesmo padrão de OrderRepository.counts_by_statuses pros pedidos."""
        return self.model.query.filter(
            Invoice.tenant_id == tenant_id,
            Invoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.OVERDUE]),
        ).count()
