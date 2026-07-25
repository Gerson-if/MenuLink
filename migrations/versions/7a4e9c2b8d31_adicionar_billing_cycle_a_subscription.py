"""adicionar billing_cycle a subscription

Revision ID: 7a4e9c2b8d31
Revises: 3203a071ca33
Create Date: 2026-07-24 23:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a4e9c2b8d31'
down_revision = '3203a071ca33'
branch_labels = None
depends_on = None


def upgrade():
    # nullable=True por enquanto — a migration seguinte faz o backfill a
    # partir de plans.billing_cycle antes de travar como NOT NULL.
    # create_type=False: o tipo enum "billing_cycle" já existe no Postgres
    # (criado pela migration de plans.billing_cycle) — reaproveitado aqui,
    # não recriado.
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'billing_cycle',
            sa.Enum('monthly', 'yearly', name='billing_cycle', create_type=False),
            nullable=True,
        ))

    op.execute(
        """
        UPDATE subscriptions
        SET billing_cycle = (
            SELECT plans.billing_cycle FROM plans WHERE plans.id = subscriptions.plan_id
        )
        WHERE EXISTS (SELECT 1 FROM plans WHERE plans.id = subscriptions.plan_id)
        """
    )
    # Fallback pros poucos casos sem plano associável (não deveria
    # acontecer em uso normal, mas evita deixar NULL antes do NOT NULL).
    op.execute("UPDATE subscriptions SET billing_cycle = 'monthly' WHERE billing_cycle IS NULL")

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.alter_column('billing_cycle', existing_type=sa.Enum('monthly', 'yearly', name='billing_cycle'), nullable=False)


def downgrade():
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_column('billing_cycle')
