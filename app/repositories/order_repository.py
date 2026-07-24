from app.extensions import db
from app.models import Order
from app.repositories.base_repository import TenantScopedRepository


class OrderRepository(TenantScopedRepository[Order]):
    model = Order

    def next_order_number(self) -> int:
        current_max = (
            db.session.query(db.func.max(Order.order_number))
            .filter(Order.tenant_id == self.tenant_id)
            .scalar()
        )
        return (current_max or 0) + 1

    def list_ordered(self):
        return self._base_query().order_by(Order.created_at.desc()).all()

    def paginated(self, page: int, per_page: int = 12, status: str | None = None, statuses: list | None = None):
        """
        `status`: filtro exato (um único status, ex.: os chips da tela de
        Pedidos/Histórico). `statuses`: filtro por um conjunto de status
        (ex.: "todos os ativos" ou "todos os do histórico") — usado quando
        não há um `status` específico selecionado. Os dois são mutuamente
        complementares: `status` sempre tem prioridade se informado.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        stmt = (
            select(Order)
            .where(Order.tenant_id == self.tenant_id)
            .options(selectinload(Order.items))
        )
        if status:
            stmt = stmt.where(Order.status == status)
        elif statuses:
            stmt = stmt.where(Order.status.in_(statuses))
        stmt = stmt.order_by(Order.created_at.desc())
        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)
