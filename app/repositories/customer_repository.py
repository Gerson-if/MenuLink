from app.models import Customer, Order
from app.repositories.base_repository import TenantScopedRepository


class CustomerRepository(TenantScopedRepository[Customer]):
    model = Customer

    def get_by_phone(self, phone: str):
        return self._base_query().filter(Customer.phone == phone).first()

    def paginated(self, page: int, per_page: int = 20, sort: str = "recent"):
        """
        `sort`:
        - "recent" (padrão): clientes mais novos primeiro.
        - "spent": clientes que mais gastaram primeiro — permite ao lojista
          alocar/identificar os melhores clientes (os que mais compram).
        - "orders": clientes com mais pedidos primeiro.

        Pra ordenar por gasto/pedidos é preciso agregar `orders` já na
        query paginada (não dá pra ordenar em Python depois de paginar),
        daí o outerjoin com uma subquery de estatísticas por cliente.
        """
        from sqlalchemy import func, select

        from app.extensions import db
        from app.models import Order

        stats_subq = (
            select(
                Order.customer_id.label("customer_id"),
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.total_cents), 0).label("total_spent_cents"),
            )
            .where(Order.tenant_id == self.tenant_id)
            .group_by(Order.customer_id)
            .subquery()
        )

        stmt = (
            select(Customer)
            .outerjoin(stats_subq, stats_subq.c.customer_id == Customer.id)
            .where(Customer.tenant_id == self.tenant_id)
        )
        if sort == "spent":
            stmt = stmt.order_by(func.coalesce(stats_subq.c.total_spent_cents, 0).desc(), Customer.created_at.desc())
        elif sort == "orders":
            stmt = stmt.order_by(func.coalesce(stats_subq.c.order_count, 0).desc(), Customer.created_at.desc())
        else:
            stmt = stmt.order_by(Customer.created_at.desc())

        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    def top_customer_ids(self, limit: int = 3) -> list[int]:
        """
        Ids dos clientes que mais gastaram nesta loja (para destacar os
        "melhores clientes" com um selo, independente da ordenação/página
        atual da listagem)."""
        from sqlalchemy import func, select

        from app.extensions import db

        stmt = (
            select(Order.customer_id, func.coalesce(func.sum(Order.total_cents), 0).label("total"))
            .where(Order.tenant_id == self.tenant_id, Order.customer_id.isnot(None))
            .group_by(Order.customer_id)
            .order_by(func.coalesce(func.sum(Order.total_cents), 0).desc())
            .limit(limit)
        )
        return [row[0] for row in db.session.execute(stmt).all()]

    def order_stats_for(self, customer_ids: list[int]) -> dict:
        """
        {customer_id: (order_count, total_spent_cents, last_order_at)} para
        os ids informados — uma query agregada (mesmo estilo de
        app/services/report_service.py) em vez de uma por cliente, pra
        evitar N+1 ao montar a tabela de clientes paginada.
        """
        if not customer_ids:
            return {}

        from sqlalchemy import func

        from app.extensions import db

        rows = (
            db.session.query(
                Order.customer_id,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_cents), 0),
                func.max(Order.created_at),
            )
            .filter(Order.tenant_id == self.tenant_id, Order.customer_id.in_(customer_ids))
            .group_by(Order.customer_id)
            .all()
        )
        return {customer_id: (int(count), int(total), last) for customer_id, count, total, last in rows}
