"""adicionar tema unificado do sistema em platform_settings

Revision ID: a1c2d3e4f5a6
Revises: fbd430ffe466
Create Date: 2026-07-29 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c2d3e4f5a6'
down_revision = 'fbd430ffe466'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('system_theme', sa.String(length=20), nullable=False, server_default='chili'))


def downgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.drop_column('system_theme')
