"""
User = qualquer pessoa que faz login no sistema.

- role = SUPER_ADMIN  -> tenant_id é sempre NULL. Gerencia toda a plataforma.
- role = OWNER         -> dono da loja (lojista principal).
- role = STAFF         -> funcionário do lojista, acesso mais restrito
                          (reservado para uma futura tela de permissões).

Login para ambos os perfis é feito por e-mail + senha (ver
app/services/auth_service.py).
"""

import enum

from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from app.extensions import db, bcrypt
from app.models.base import TimestampMixin, str_enum


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    OWNER = "owner"
    STAFF = "staff"


class User(db.Model, UserMixin, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # tenant_id é NULL para super admins; obrigatório para owner/staff.
    # Não usamos TenantScopedMixin aqui de propósito, pois essa tabela
    # também guarda o super admin (que não pertence a tenant nenhum).
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)

    name = Column(String(150), nullable=False)
    email = Column(String(180), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)

    role = Column(str_enum(UserRole, "user_role"), nullable=False, default=UserRole.OWNER)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", back_populates="users")
    # cascade="all, delete-orphan" é necessário aqui: sem ele, ao apagar um
    # User (direto ou em cascata pela remoção do Tenant), o SQLAlchemy só
    # desassocia as UserSession filhas (tenta um UPDATE ... SET user_id =
    # NULL) em vez de apagá-las — e como user_id é NOT NULL, isso quebra a
    # remoção com IntegrityError. passive_deletes=True deixa o ON DELETE
    # CASCADE do próprio Postgres (ver app/models/user_session.py) cuidar
    # das linhas, sem o SQLAlchemy tentar carregá-las/atualizá-las à toa.
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(role = 'super_admin' AND tenant_id IS NULL) OR "
            "(role != 'super_admin' AND tenant_id IS NOT NULL)",
            name="ck_user_tenant_consistency",
        ),
    )

    # --- Senha ---
    # bcrypt (via flask-bcrypt) só aceita até 72 BYTES de senha — acima
    # disso `generate_password_hash`/`check_password_hash` levantam
    # ValueError e derrubam a requisição com 500 em vez de um erro
    # tratado. Os formulários (app/forms/*.py, ver password_max_bytes em
    # app/utils/validators.py) já bloqueiam isso antes de chegar aqui, mas
    # esta é uma segunda camada de proteção — pra scripts de linha de
    # comando, seeds ou qualquer outro caminho que crie/altere senha sem
    # passar pelos forms — para nunca mais estourar um 500 em produção
    # por causa disso (foi exatamente o que aconteceu: alguém digitou uma
    # senha longa no login e o worker do gunicorn quebrou).
    _MAX_PASSWORD_BYTES = 72

    def set_password(self, raw_password: str) -> None:
        if len(raw_password.encode("utf-8")) > self._MAX_PASSWORD_BYTES:
            raise ValueError(f"A senha não pode passar de {self._MAX_PASSWORD_BYTES} bytes.")
        self.password_hash = bcrypt.generate_password_hash(raw_password).decode("utf-8")

    def check_password(self, raw_password: str) -> bool:
        if len(raw_password.encode("utf-8")) > self._MAX_PASSWORD_BYTES:
            # Senha enviada não bate com nenhum hash válido de qualquer
            # jeito — trata como "senha errada" em vez de deixar o bcrypt
            # levantar ValueError e virar um 500.
            return False
        return bcrypt.check_password_hash(self.password_hash, raw_password)

    # --- Flask-Login ---
    def get_id(self):
        # Sobrescrito para incluir o papel no id de sessão é desnecessário;
        # o padrão (str(self.id)) já é suficiente e mais simples de manter.
        return str(self.id)

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"
