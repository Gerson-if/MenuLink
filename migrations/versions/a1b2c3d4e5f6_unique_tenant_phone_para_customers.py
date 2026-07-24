"""adicionar unicidade (tenant_id, phone) em customers

Revision ID: a1b2c3d4e5f6
Revises: 95fb40205b20
Create Date: 2026-07-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '95fb40205b20'
branch_labels = None
depends_on = None


def upgrade():
    """
    Antes de criar a constraint, mescla cadastros de Customer que hoje
    coexistem duplicados para o mesmo (tenant_id, phone) — algo que a
    aplicação sempre tentou evitar checando "cliente já existe?" antes de
    criar, mas sem garantia no banco (ver comentário em app/models/customer.py)
    duas requisições concorrentes podiam criar duas linhas pro mesmo
    telefone. Mantém o cadastro mais antigo (menor id), reatribui os
    pedidos das duplicatas pra ele, e remove as duplicatas — só então a
    constraint UNIQUE é criada.
    """
    connection = op.get_bind()

    duplicates = connection.execute(
        sa.text(
            """
            SELECT tenant_id, phone
            FROM customers
            WHERE phone IS NOT NULL
            GROUP BY tenant_id, phone
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for tenant_id, phone in duplicates:
        rows = connection.execute(
            sa.text(
                "SELECT id FROM customers WHERE tenant_id = :tenant_id AND phone = :phone ORDER BY id ASC"
            ),
            {"tenant_id": tenant_id, "phone": phone},
        ).fetchall()
        keep_id = rows[0][0]
        duplicate_ids = [row[0] for row in rows[1:]]

        for dup_id in duplicate_ids:
            connection.execute(
                sa.text("UPDATE orders SET customer_id = :keep_id WHERE customer_id = :dup_id"),
                {"keep_id": keep_id, "dup_id": dup_id},
            )
            connection.execute(sa.text("DELETE FROM customers WHERE id = :dup_id"), {"dup_id": dup_id})

    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_customer_tenant_phone", ["tenant_id", "phone"])


def downgrade():
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_constraint("uq_customer_tenant_phone", type_="unique")
