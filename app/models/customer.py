from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin


class Customer(db.Model, TenantScopedMixin, TimestampMixin):
    """
    Cliente final de uma loja específica. Criado/atualizado automaticamente
    a partir dos dados informados no checkout (nome + telefone), para permitir
    reconhecer clientes recorrentes e alimentar relatórios do lojista.
    """

    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    phone = Column(String(20), nullable=True, index=True)

    tenant = relationship("Tenant", back_populates="customers")
    orders = relationship("Order", back_populates="customer")

    __table_args__ = (
        # Garante no banco (não só na lógica de app) que não existam dois
        # cadastros de cliente com o mesmo telefone na mesma loja. Sem
        # isso, dois checkouts concorrentes com o mesmo telefone (duplo
        # clique, duas abas, retry de rede) podiam passar pela checagem
        # "cliente já existe?" da aplicação ao mesmo tempo e cada um criar
        # o seu próprio registro — duplicando o cadastro e espalhando o
        # histórico/estatísticas desse cliente entre duas linhas. NULLs
        # continuam permitidos múltiplas vezes (comportamento padrão de
        # UNIQUE em Postgres/SQLite), então clientes sem telefone não são
        # afetados.
        UniqueConstraint("tenant_id", "phone", name="uq_customer_tenant_phone"),
    )

    def __repr__(self):
        return f"<Customer {self.name}>"
