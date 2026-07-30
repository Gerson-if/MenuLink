"""adicionar video de fundo das telas de autenticacao

Revision ID: b2d3e4f5a6b7
Revises: a1c2d3e4f5a6
Create Date: 2026-07-30 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2d3e4f5a6b7'
down_revision = 'a1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auth_background_video', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.drop_column('auth_background_video')
