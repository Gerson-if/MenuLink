"""adicionar notificação de WhatsApp por status do pedido

Revision ID: b3c4d5e6f7a8
Revises: 9a1b2c3d4e5f
Create Date: 2026-08-07 10:00:00.000000

Adiciona a integração com a WhatsApp Cloud API (Meta) em
`platform_settings` — token de acesso (criptografado, mesmo padrão de
asaas_api_key), o Phone Number ID e o mapa {status: nome_do_template}
— e `plans.allow_whatsapp_notifications`, o bônus por plano que libera
o recurso pro lojista (mesmo padrão de allow_qr_customization).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = '9a1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('whatsapp_access_token', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('whatsapp_phone_number_id', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('whatsapp_status_templates', sa.JSON(), nullable=True))

    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('allow_whatsapp_notifications', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_column('allow_whatsapp_notifications')

    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.drop_column('whatsapp_status_templates')
        batch_op.drop_column('whatsapp_phone_number_id')
        batch_op.drop_column('whatsapp_access_token')
