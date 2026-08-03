"""Criptografa segredos de integracao (asaas/smtp) em repouso

Revision ID: 71e8fb9dcdf7
Revises: c3d4e5f6a7b8
Create Date: 2026-08-03 20:09:40.807444

Aumenta as colunas de segredo de terceiro (chave da API do Asaas, token
de webhook do Asaas, senha do SMTP) para caberem o texto cifrado (maior
que o texto puro original — ver app/utils/crypto.py) e, em seguida,
CRIPTOGRAFA em uma única passada qualquer valor que já esteja gravado em
texto puro (dados de antes desta migração existir). Sem esse segundo
passo, dados antigos ficariam em texto puro até alguém reabrir e salvar
de novo a tela de Integrações — o app/models/base.py:EncryptedString já
lida com esse caso (fallback ao ler texto puro), mas criptografar de uma
vez aqui é mais seguro do que depender de alguém resalvar a tela depois.

Usamos `sa.String(length=512)` (não `EncryptedString` diretamente) na
DDL porque o que muda no banco é só o TAMANHO da coluna — a
criptografia em si acontece na camada Python (TypeDecorator), não no
schema; evita depender de importar `app.models.base` dentro da
migration.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '71e8fb9dcdf7'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None

_SECRET_COLUMNS = ("asaas_api_key", "asaas_webhook_token", "smtp_password")


def upgrade():
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        for col in _SECRET_COLUMNS:
            batch_op.alter_column(
                col,
                existing_type=sa.VARCHAR(length=255),
                type_=sa.String(length=512),
                existing_nullable=True,
            )

    from app.utils.crypto import decrypt_secret, encrypt_secret

    conn = op.get_bind()
    platform_settings = sa.table(
        "platform_settings",
        sa.column("id", sa.Integer),
        *(sa.column(col, sa.String) for col in _SECRET_COLUMNS),
    )
    rows = conn.execute(sa.select(platform_settings)).fetchall()
    for row in rows:
        row = row._mapping
        updates = {}
        for col in _SECRET_COLUMNS:
            value = row[col]
            if not value:
                continue
            # Se descriptografar dá em algo DIFERENTE do valor gravado, é
            # porque já é um token Fernet válido — já está criptografado
            # (torna esta migration segura de rodar mais de uma vez).
            if decrypt_secret(value) != value:
                continue
            updates[col] = encrypt_secret(value)
        if updates:
            conn.execute(
                platform_settings.update()
                .where(platform_settings.c.id == row["id"])
                .values(**updates)
            )


def downgrade():
    # Descriptografa de volta antes de encolher a coluna — sem isso, o
    # texto cifrado (maior que 255 chars) ficaria truncado ao encolher a
    # coluna, corrompendo o valor de forma irrecuperável.
    from app.utils.crypto import decrypt_secret

    conn = op.get_bind()
    platform_settings = sa.table(
        "platform_settings",
        sa.column("id", sa.Integer),
        *(sa.column(col, sa.String) for col in _SECRET_COLUMNS),
    )
    rows = conn.execute(sa.select(platform_settings)).fetchall()
    for row in rows:
        row = row._mapping
        updates = {}
        for col in _SECRET_COLUMNS:
            value = row[col]
            if not value:
                continue
            plain = decrypt_secret(value)
            if plain != value:
                updates[col] = plain
        if updates:
            conn.execute(
                platform_settings.update()
                .where(platform_settings.c.id == row["id"])
                .values(**updates)
            )

    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        for col in _SECRET_COLUMNS:
            batch_op.alter_column(
                col,
                existing_type=sa.String(length=512),
                type_=sa.VARCHAR(length=255),
                existing_nullable=True,
            )
