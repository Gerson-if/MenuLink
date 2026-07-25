"""
Pedido feito pelo cliente final no cardápio público.

O pedido é sempre GRAVADO no banco (histórico, relatórios, financeiro do
lojista) e, adicionalmente, uma mensagem é enviada por WhatsApp — as duas
coisas acontecem juntas (ver app/services/order_service.py).

Campos de entrega: quando o lojista habilita a entrega (Tenant.delivery_enabled),
o endereço é dividido em campos separados e claros (rua, número, complemento,
bairro, referência) em vez de um único campo de texto livre — isso reduz
erro de entrega. Quando é retirada no local, esses campos ficam nulos.
"""

import enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, str_enum


class DeliveryType(str, enum.Enum):
    PICKUP = "pickup"      # retirada no local
    DELIVERY = "delivery"  # entrega


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    PIX = "pix"
    OTHER = "other"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"              # recebido, aguardando confirmação do lojista
    CONFIRMED = "confirmed"          # lojista confirmou
    PREPARING = "preparing"          # em preparo
    OUT_FOR_DELIVERY = "out_for_delivery"  # saiu para entrega
    READY_FOR_PICKUP = "ready_for_pickup"  # pronto para retirada
    COMPLETED = "completed"          # entregue / retirado
    CANCELED = "canceled"


class Order(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)

    # Numeração amigável, sequencial POR loja (não é o id global da tabela).
    order_number = Column(Integer, nullable=False)

    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_name = Column(String(150), nullable=False)   # snapshot, mesmo se customer mudar o nome depois
    customer_phone = Column(String(20), nullable=True)

    delivery_type = Column(str_enum(DeliveryType, "delivery_type"), nullable=False)

    # --- Campos de entrega, separados e explícitos (evita erro de endereço) ---
    address_street = Column(String(180), nullable=True)
    address_number = Column(String(20), nullable=True)
    address_complement = Column(String(120), nullable=True)  # apto, bloco, etc.
    address_neighborhood = Column(String(100), nullable=True)
    address_city = Column(String(100), nullable=True)
    address_reference = Column(String(180), nullable=True)   # ponto de referência

    payment_method = Column(str_enum(PaymentMethod, "payment_method"), nullable=False)
    status = Column(str_enum(OrderStatus, "order_status"), nullable=False, default=OrderStatus.PENDING, index=True)

    subtotal_cents = Column(Integer, nullable=False, default=0)
    delivery_fee_cents = Column(Integer, nullable=False, default=0)
    total_cents = Column(Integer, nullable=False, default=0)

    notes = Column(Text, nullable=True)

    whatsapp_sent = Column(db.Boolean, nullable=False, default=False)
    whatsapp_sent_at = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", back_populates="orders")
    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "order_number", name="uq_order_tenant_number"),
        CheckConstraint(
            "(delivery_type = 'pickup') OR "
            "(delivery_type = 'delivery' AND address_street IS NOT NULL "
            "AND address_number IS NOT NULL AND address_neighborhood IS NOT NULL)",
            name="ck_order_delivery_requires_address",
        ),
        CheckConstraint("subtotal_cents >= 0 AND total_cents >= 0", name="ck_order_totals_non_negative"),
    )

    def __repr__(self):
        return f"<Order #{self.order_number} (tenant={self.tenant_id})>"


class OrderItem(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)

    # Snapshot: se o produto mudar de nome/preço depois, o histórico do
    # pedido não pode mudar junto.
    product_name = Column(String(150), nullable=False)
    unit_price_cents = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    subtotal_cents = Column(Integer, nullable=False)

    order = relationship("Order", back_populates="items")
    choices = relationship("OrderItemChoice", back_populates="item", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
    )


class OrderItemChoice(db.Model, TenantScopedMixin, TimestampMixin):
    """Snapshot de uma variação/complemento escolhido em um item de pedido."""

    __tablename__ = "order_item_choices"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True)

    group_name = Column(String(100), nullable=False)   # snapshot, ex: "Tamanho"
    option_name = Column(String(100), nullable=False)   # snapshot, ex: "Grande"
    extra_price_cents = Column(Integer, nullable=False, default=0)  # preço unitário — multiplicar por `quantity` pro total
    quantity = Column(Integer, nullable=False, default=1)  # ex: 2x Coca-Cola — ver ComplementGroup.allow_quantity

    item = relationship("OrderItem", back_populates="choices")
