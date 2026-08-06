"""adicionar personalização de QR Code por plano

Revision ID: 9a1b2c3d4e5f
Revises: a2e6d89415c9
Create Date: 2026-08-06 10:00:00.000000

Adiciona `plans.allow_qr_customization` (bônus de personalizar o QR
Code do cardápio pra impressão: nome do negócio, cor de fundo, ícone —
ver TenantSettingsService.update_qr_card_settings) e já liga o campo
pros planos Start e Premium, conforme pedido do Super Admin — dali em
diante o controle é 100% pelo painel de planos, não por este valor
inicial.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9a1b2c3d4e5f'
down_revision = 'a2e6d89415c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('allow_qr_customization', sa.Boolean(), server_default='0', nullable=False))

    plans = sa.table(
        "plans",
        sa.column("slug", sa.String),
        sa.column("allow_qr_customization", sa.Boolean),
    )
    conn = op.get_bind()
    conn.execute(
        plans.update()
        .where(plans.c.slug.in_(["start", "premium"]))
        .values(allow_qr_customization=True)
    )


def downgrade():
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_column('allow_qr_customization')
