"""
Mixins reutilizados por todos os models de domínio.

- TimestampMixin: created_at / updated_at automáticos.
- TenantScopedMixin: adiciona tenant_id (FK para tenants.id) + índice.
  Toda tabela que guarda dado *de um lojista específico* (produtos,
  categorias, pedidos, clientes, imagens, configurações...) deve herdar
  desse mixin. Tabelas globais da plataforma (tenants, plans, users
  super-admin) NÃO herdam.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import declared_attr
from sqlalchemy.types import String, TypeDecorator

from app.extensions import db


def str_enum(enum_cls, name: str):
    """
    Helper para colunas Enum baseadas em `str, enum.Enum`.

    Por padrão, o SQLAlchemy persiste o *nome* do membro do enum (ex:
    "SUPER_ADMIN"), não o seu *valor* (ex: "super_admin"). Como nossos
    CHECK constraints, comparações e migrations usam o valor em minúsculas,
    forçamos `values_callable` para persistir sempre `.value`.
    """
    return SAEnum(enum_cls, name=name, values_callable=lambda obj: [e.value for e in obj])


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantScopedMixin:
    """
    Garante o isolamento multi-tenant a nível de linha (row-level).

    Toda query de repositório para tabelas com esse mixin DEVE filtrar por
    tenant_id (isso é reforçado na camada de repositório, ver
    app/repositories/base_repository.py). O índice em tenant_id é
    obrigatório: é a coluna mais usada em WHERE de toda a aplicação.
    """

    @declared_attr
    def tenant_id(cls):
        return Column(
            Integer,
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class EncryptedString(TypeDecorator):
    """Tipo de coluna para segredos de terceiro que precisam ser
    recuperáveis em texto puro para USO (chave de API, senha de SMTP —
    diferente da senha do usuário, que é só hash/nunca lida de volta) mas
    não devem ficar em texto puro NO BANCO. Ver app/utils/crypto.py para
    o porquê e como a chave de criptografia é derivada.

    Uso: `Column(EncryptedString(255))` no lugar de `Column(String(255))`
    — para quem consome o model, o valor Python continua uma string
    normal (des)criptografada automaticamente na escrita/leitura; nenhum
    código que já lê/escreve esses campos precisa mudar.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from app.utils.crypto import encrypt_secret

        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        from app.utils.crypto import decrypt_secret

        return decrypt_secret(value)
