"""
Validação de limites de plano (categorias/produtos) — compartilhada
entre TenantSettingsService (ao AGENDAR um downgrade) e
AdminBillingService (ao efetivamente APLICAR o downgrade agendado na
renovação, ver `_apply_pending_plan_change`). Ficar num módulo à parte
evita duplicar a mesma regra nos dois lugares (e o risco de um deles
ficar desatualizado se a regra mudar).
"""

from app.repositories.category_repository import CategoryRepository
from app.repositories.product_repository import ProductRepository


class PlanLimitExceeded(Exception):
    """Levantada quando o uso atual do tenant não cabe nos limites do
    plano candidato — a mensagem já vem pronta para exibir ao usuário."""


def check_plan_limits(tenant, plan) -> None:
    """Confere se o uso atual (categorias/produtos) do `tenant` cabe
    nos limites do `plan` candidato. `None` em max_categories/max_products
    significa "ilimitado" (nada a checar). Levanta `PlanLimitExceeded`
    com uma mensagem pronta para o usuário se não couber."""
    if plan.max_categories is not None:
        category_count = CategoryRepository(tenant.id).count()
        if category_count > plan.max_categories:
            raise PlanLimitExceeded(
                f"O plano '{plan.name}' permite no máximo {plan.max_categories} categoria(s), mas "
                f"sua loja tem {category_count} cadastrada(s). Exclua o excedente antes de trocar de plano."
            )

    if plan.max_products is not None:
        product_count = ProductRepository(tenant.id).count_completed()
        if product_count > plan.max_products:
            raise PlanLimitExceeded(
                f"O plano '{plan.name}' permite no máximo {plan.max_products} produto(s), mas "
                f"sua loja tem {product_count} cadastrado(s). Exclua o excedente antes de trocar de plano."
            )
