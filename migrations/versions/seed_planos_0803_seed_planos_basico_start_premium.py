"""Cria/ajusta os planos Basico, Start e Premium

Revision ID: seed_planos_0803
Revises: 71e8fb9dcdf7
Create Date: 2026-08-03 21:30:00.000000

Migration de DADOS (não de schema) — cria os três planos de assinatura
pedidos pelo Super Admin ou, se um plano com o mesmo nome já existir
(ex: o "Básico" de exemplo que já vinha na instalação), só ajusta o
preço e os limites em vez de duplicar.

Sem isto, `scripts/seed_plans.py` só afeta o banco em que alguém rodar
o script manualmente — o processo de atualização do servidor
(deploy/update.sh) roda apenas `flask db upgrade`, então uma migration
é o único jeito de garantir que os três planos apareçam de fato na
landing page em produção depois de um `git pull` + atualização normal.

Diferenciação HONESTA entre os planos, baseada só em limites que o
sistema realmente aplica (ver app/services/category_service.py e
app/services/product_service.py) — nada de vantagem inventada:
  - Básico R$15,90/mês: até 20 produtos, 4 categorias, 1 foto/produto
  - Start  R$23,90/mês: até 60 produtos, 10 categorias, 3 fotos/produto (destaque na landing)
  - Premium R$35,90/mês: produtos e categorias ilimitados, 6 fotos/produto

O downgrade só reverte os campos alterados quando o próprio upgrade
tiver sido quem criou o registro (guardamos isso implicitamente: um
downgrade não tenta "adivinhar" o estado anterior de um plano que já
existia antes, então ele apenas remove os planos que esta migration
criou do zero — Start e Premium — e deixa o Básico como está, pois
mexer no preço dele de novo poderia surpreender quem já vendeu
assinaturas com o valor novo).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'seed_planos_0803'
down_revision = '71e8fb9dcdf7'
branch_labels = None
depends_on = None


_PLANS_TABLE_COLUMNS = (
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("slug", sa.String),
    sa.column("description", sa.Text),
    sa.column("price_cents", sa.Integer),
    sa.column("billing_cycle", sa.String),
    sa.column("max_products", sa.Integer),
    sa.column("max_categories", sa.Integer),
    sa.column("max_images_per_product", sa.Integer),
    sa.column("is_active", sa.Boolean),
    sa.column("is_featured", sa.Boolean),
    sa.column("display_order", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)

_PLANS_DATA = [
    dict(
        name="Básico",
        slug="basico",
        description="Até 20 produtos, 4 categorias e 1 foto por produto.",
        price_cents=1590,
        max_categories=4,
        max_products=20,
        max_images_per_product=1,
        is_featured=False,
        display_order=0,
    ),
    dict(
        name="Start",
        slug="start",
        description="Até 60 produtos, 10 categorias e 3 fotos por produto.",
        price_cents=2390,
        max_categories=10,
        max_products=60,
        max_images_per_product=3,
        is_featured=True,
        display_order=1,
    ),
    dict(
        name="Premium",
        slug="premium",
        description="Produtos e categorias ilimitados, com até 6 fotos por produto.",
        price_cents=3590,
        max_categories=None,
        max_products=None,
        max_images_per_product=6,
        is_featured=False,
        display_order=2,
    ),
]


def upgrade():
    conn = op.get_bind()
    now = sa.func.now()
    plans = sa.table("plans", *_PLANS_TABLE_COLUMNS)

    for data in _PLANS_DATA:
        existing = conn.execute(
            sa.select(plans.c.id).where(plans.c.name == data["name"])
        ).first()
        if existing:
            conn.execute(
                plans.update()
                .where(plans.c.id == existing.id)
                .values(
                    description=data["description"],
                    price_cents=data["price_cents"],
                    max_categories=data["max_categories"],
                    max_products=data["max_products"],
                    max_images_per_product=data["max_images_per_product"],
                    is_featured=data["is_featured"],
                    display_order=data["display_order"],
                    updated_at=now,
                )
            )
        else:
            conn.execute(
                plans.insert().values(
                    name=data["name"],
                    slug=data["slug"],
                    description=data["description"],
                    price_cents=data["price_cents"],
                    billing_cycle="monthly",
                    max_categories=data["max_categories"],
                    max_products=data["max_products"],
                    max_images_per_product=data["max_images_per_product"],
                    is_active=True,
                    is_featured=data["is_featured"],
                    display_order=data["display_order"],
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade():
    # Só remove os planos Start e Premium, que esta migration cria do
    # zero — nunca mexe no Básico no downgrade (ver docstring acima).
    conn = op.get_bind()
    plans = sa.table("plans", *_PLANS_TABLE_COLUMNS)
    conn.execute(plans.delete().where(plans.c.name.in_(["Start", "Premium"])))
