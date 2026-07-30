"""
Agrega todos os models para que:
1. `db.metadata` os conheça (necessário para Flask-Migrate/Alembic gerar
   as migrations corretamente).
2. Outros módulos possam importar de `app.models` diretamente, ex:
   `from app.models import Tenant, Product`.
"""

from app.models.tenant import Tenant, Plan, TenantStatus, BillingCycle
from app.models.user import User, UserRole
from app.models.user_session import UserSession
from app.models.category import Category
from app.models.product import Product, ProductImage, ComplementGroup, ComplementOption
from app.models.customer import Customer
from app.models.order import (
    Order,
    OrderItem,
    OrderItemChoice,
    DeliveryType,
    PaymentMethod,
    OrderStatus,
)
from app.models.billing import Subscription, Invoice, SubscriptionStatus, InvoiceStatus
from app.models.banner import Banner
from app.models.chat_message import ChatMessage, ChatSenderRole
from app.models.platform_settings import PlatformSettings, ASAAS_ENVIRONMENT_CHOICES
from app.models.feedback import Feedback, FeedbackKind, FeedbackStatus

__all__ = [
    "Tenant",
    "Plan",
    "TenantStatus",
    "BillingCycle",
    "User",
    "UserRole",
    "UserSession",
    "Category",
    "Product",
    "ProductImage",
    "ComplementGroup",
    "ComplementOption",
    "Customer",
    "Order",
    "OrderItem",
    "OrderItemChoice",
    "DeliveryType",
    "PaymentMethod",
    "OrderStatus",
    "Subscription",
    "Invoice",
    "SubscriptionStatus",
    "InvoiceStatus",
    "Banner",
    "ChatMessage",
    "ChatSenderRole",
    "PlatformSettings",
    "ASAAS_ENVIRONMENT_CHOICES",
    "Feedback",
    "FeedbackKind",
    "FeedbackStatus",
]
