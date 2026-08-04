"""Adicionar anexo (comprovante/imagem/PDF) ao chat e tornar body opcional

Revision ID: f3a1c9b7d2e4
Revises: trial_7dias_0803
Create Date: 2026-08-04 10:00:00.000000

Duas mudanças em `chat_messages`, pedidas junto porque uma depende da
outra:

1. Quatro colunas novas (todas nullable — a imensa maioria das
   mensagens continua sendo só texto, sem anexo): `attachment_path`
   (caminho relativo dentro de static/, mesmo padrão de
   ProductImage.file_path), `attachment_original_name` (nome exibido
   pro usuário, sanitizado — NUNCA o mesmo do arquivo no disco, que é
   um uuid4 aleatório — ver app/utils/uploads.py:save_chat_attachment),
   `attachment_mimetype` e `attachment_size_bytes`.

2. `body` deixa de ter só `NOT NULL` e passa a ter também
   `server_default=''`: com anexo, uma mensagem agora pode ser
   só-imagem/só-PDF, sem nenhum texto (ver
   ChatService.send_message) — mas a coluna continua NOT NULL (não
   vira nullable) pra não precisar espalhar checagem de `None` por
   todo lugar que já lê `message.body` esperando uma string (ver
   app/models/chat_message.py). Linhas já existentes não são tocadas
   (todas já têm um body de verdade, preenchido).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a1c9b7d2e4'
down_revision = 'trial_7dias_0803'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.alter_column(
            'body',
            existing_type=sa.Text(),
            nullable=False,
            server_default='',
        )
        batch_op.add_column(sa.Column('attachment_path', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('attachment_original_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('attachment_mimetype', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('attachment_size_bytes', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_column('attachment_size_bytes')
        batch_op.drop_column('attachment_mimetype')
        batch_op.drop_column('attachment_original_name')
        batch_op.drop_column('attachment_path')
        batch_op.alter_column(
            'body',
            existing_type=sa.Text(),
            nullable=False,
            server_default=None,
        )
