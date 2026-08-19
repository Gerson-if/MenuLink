"""adicionar comanda de impressão por plano

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-18 10:00:00.000000

Adiciona `plans.allow_order_print_ticket` (bônus da comanda de
impressão do pedido, ver lojista/orders/print_ticket.html) e já liga
o campo pros planos Start e Premium, mesmo padrão de
allow_qr_customization — dali em diante o controle é 100% pelo painel
de planos.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('allow_order_print_ticket', sa.Boolean(), server_default='0', nullable=False))

    plans = sa.table(
        "plans",
        sa.column("slug", sa.String),
        sa.column("allow_order_print_ticket", sa.Boolean),
    )
    conn = op.get_bind()
    conn.execute(
        plans.update()
        .where(plans.c.slug.in_(["start", "premium"]))
        .values(allow_order_print_ticket=True)
    )


def downgrade():
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_column('allow_order_print_ticket')
