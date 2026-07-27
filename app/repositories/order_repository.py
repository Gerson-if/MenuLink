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

    def paginated(
        self, page: int, per_page: int = 12, status: str | None = None, statuses: list | None = None,
        date_from=None, date_to=None,
    ):
        """
        `status`: filtro exato (um único status, ex.: os chips da tela de
        Pedidos/Histórico). `statuses`: filtro por um conjunto de status
        (ex.: "todos os ativos" ou "todos os do histórico") — usado quando
        não há um `status` específico selecionado. Os dois são mutuamente
        complementares: `status` sempre tem prioridade se informado.

        `date_from`/`date_to`: recorte por período de criação do pedido
        (ex.: filtro de "Hoje"/"Este mês"/"Este ano" do Histórico).
        `date_from` é inclusivo, `date_to` é EXCLUSIVO (ou seja, já deve
        vir como "o dia seguinte ao último dia desejado") — assim um
        filtro de "hoje" simplesmente usa `date_from=hoje, date_to=amanhã`
        sem precisar lidar com hora do dia.
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
        if date_from is not None:
            stmt = stmt.where(Order.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Order.created_at < date_to)
        stmt = stmt.order_by(Order.created_at.desc())
        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)
