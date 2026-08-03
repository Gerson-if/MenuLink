"""
Script único (não é uma migration) para criar/ajustar os três planos de
assinatura pedidos pelo Super Admin: Básico, Start e Premium.

Roda via `python scripts/seed_plans.py`. Usa o AdminPlanService (mesma
camada usada pelo painel /admin/planos) para respeitar geração de slug e
regras de negócio, em vez de inserir direto via ORM.

Diferenciação HONESTA entre os planos: as três vantagens mostradas na
landing page (link do cardápio + QR Code, pedidos via WhatsApp sem
comissão) já são hardcoded no template pra TODOS os planos, porque são
recursos realmente disponíveis para qualquer loja, independente do
plano contratado (ver app/templates/marketing/landing.html:300-301).
A única diferença REAL entre os planos, de fato aplicada pelo sistema,
são os limites checados em app/services/category_service.py e
app/services/product_service.py: número máximo de produtos, categorias
e fotos por produto. É isso que este script usa para diferenciar os
três planos — nada de propaganda enganosa.
"""

from app import create_app
from app.models import Plan
from app.services.admin_plan_service import AdminPlanService

PLANS = [
    dict(
        name="Básico",
        description="Até 20 produtos, 4 categorias e 1 foto por produto.",
        price_reais=15.90,
        billing_cycle="monthly",
        max_categories=4,
        max_products=20,
        max_images_per_product=1,
        is_featured=False,
        display_order=0,
    ),
    dict(
        name="Start",
        description="Até 60 produtos, 10 categorias e 3 fotos por produto.",
        price_reais=23.90,
        billing_cycle="monthly",
        max_categories=10,
        max_products=60,
        max_images_per_product=3,
        is_featured=True,
        display_order=1,
    ),
    dict(
        name="Premium",
        description="Produtos e categorias ilimitados, com até 6 fotos por produto.",
        price_reais=35.90,
        billing_cycle="monthly",
        max_categories=None,
        max_products=None,
        max_images_per_product=6,
        is_featured=False,
        display_order=2,
    ),
]


def run():
    app = create_app("development")
    with app.app_context():
        service = AdminPlanService()
        for data in PLANS:
            existing = Plan.query.filter_by(name=data["name"]).first()
            if existing:
                service.update(existing, **{k: v for k, v in data.items() if k != "name"}, name=data["name"])
                print(f"Atualizado: {existing.name} (id={existing.id}) -> R${data['price_reais']:.2f}")
            else:
                plan = service.create(**data)
                print(f"Criado: {plan.name} (id={plan.id}, slug={plan.slug}) -> R${data['price_reais']:.2f}")


if __name__ == "__main__":
    run()
