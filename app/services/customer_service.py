"""
Clientes finais (Customer) do ponto de vista do lojista: consulta (com
estatísticas de pedidos e destaque dos melhores clientes) e gerenciamento
básico (remover cadastro). Customer em si continua sendo criado/atualizado
automaticamente a partir do checkout (ver app/models/customer.py) — não há
formulário de criação manual.
"""

from collections import namedtuple

from flask import abort

from app.extensions import db
from app.repositories.customer_repository import CustomerRepository

CustomerRow = namedtuple(
    "CustomerRow", ["customer", "order_count", "total_spent_cents", "last_order_at", "is_top"]
)


class CustomerService:
    def __init__(self, tenant):
        self.tenant = tenant
        self.repo = CustomerRepository(tenant.id)

    def paginated(self, page: int, per_page: int = 20, sort: str = "recent"):
        pagination = self.repo.paginated(page=page, per_page=per_page, sort=sort)
        stats = self.repo.order_stats_for([customer.id for customer in pagination.items])
        # Só faz sentido destacar "melhor cliente" quando o cliente teve ao
        # menos um pedido — clientes sem gasto (total 0) nunca entram aqui,
        # mesmo que apareçam nos 3 primeiros de uma loja com poucos clientes.
        top_ids = {cid for cid in self.repo.top_customer_ids(limit=3)}
        pagination.items = [
            CustomerRow(customer, *stats.get(customer.id, (0, 0, None)), customer.id in top_ids)
            for customer in pagination.items
        ]
        return pagination

    def get_or_404(self, customer_id: int):
        customer = self.repo.get_by_id(customer_id)
        if customer is None:
            abort(404)
        return customer

    def delete(self, customer) -> None:
        # Order.customer_id é nullable com ondelete=SET NULL: os pedidos do
        # cliente permanecem no histórico do lojista (faturamento, relatórios),
        # só perdem o vínculo com o cadastro removido.
        db.session.delete(customer)
        db.session.commit()
