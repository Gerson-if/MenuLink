"""Ajustar periodo de teste gratis padrao para 7 dias

Revision ID: trial_7dias_0803
Revises: seed_planos_0803
Create Date: 2026-08-03 22:10:00.000000

Muda o padrão do período de teste grátis de 20 para 7 dias — tanto o
`server_default` da coluna (novas instalações, ou qualquer INSERT que
não informe o campo) quanto o valor já gravado em
`platform_settings.trial_days` nesta instalação.

O UPDATE de dado só mexe na linha se ela ainda estiver no valor antigo
(20) — ou seja, só troca o "padrão de fábrica" nunca customizado. Se
algum Super Admin já tiver ajustado esse número manualmente pela tela
de Configurações (/admin/configuracoes) para outro valor qualquer
(inclusive 20 de propósito), esta migration NÃO sobrescreve a escolha
dele. Só afeta lojas cadastradas DAQUI PRA FRENTE (ver
AdminTenantService.create) — quem já está em teste com um prazo
calculado a partir do valor de 20 dias mantém o prazo que já tinha.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'trial_7dias_0803'
down_revision = 'seed_planos_0803'
branch_labels = None
depends_on = None

_OLD_DEFAULT = 20
_NEW_DEFAULT = 7


def upgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.alter_column(
            'trial_days',
            existing_type=sa.Integer(),
            server_default=str(_NEW_DEFAULT),
            existing_nullable=False,
        )

    conn = op.get_bind()
    platform_settings = sa.table(
        "platform_settings",
        sa.column("id", sa.Integer),
        sa.column("trial_days", sa.Integer),
    )
    conn.execute(
        platform_settings.update()
        .where(platform_settings.c.trial_days == _OLD_DEFAULT)
        .values(trial_days=_NEW_DEFAULT)
    )


def downgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.alter_column(
            'trial_days',
            existing_type=sa.Integer(),
            server_default=str(_OLD_DEFAULT),
            existing_nullable=False,
        )

    conn = op.get_bind()
    platform_settings = sa.table(
        "platform_settings",
        sa.column("id", sa.Integer),
        sa.column("trial_days", sa.Integer),
    )
    conn.execute(
        platform_settings.update()
        .where(platform_settings.c.trial_days == _NEW_DEFAULT)
        .values(trial_days=_OLD_DEFAULT)
    )
