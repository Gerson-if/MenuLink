"""
Chat simples entre o lojista (dono/equipe da loja) e o Super Admin —
um único fio de conversa por tenant (não por usuário: se a loja tem
mais de um funcionário com acesso, todos compartilham a mesma
conversa com a plataforma, como um chat de suporte).

Atualização "ao vivo" via polling HTTP (HTMX, `hx-trigger="every Ns"`)
— sem WebSocket/fila nova, consistente com o resto do app.
"""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, str_enum


class ChatSenderRole(str, enum.Enum):
    LOJISTA = "lojista"
    SUPER_ADMIN = "super_admin"


class ChatMessage(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    sender_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    sender_role = Column(str_enum(ChatSenderRole, "chat_sender_role"), nullable=False)

    body = Column(Text, nullable=False)

    # Preenchido quando o OUTRO lado (quem não mandou a mensagem) a viu —
    # base pro contador de "não lidas" de cada lado (ver ChatService).
    read_at = Column(DateTime(timezone=True), nullable=True)

    sender = relationship("User")

    def __repr__(self):
        return f"<ChatMessage tenant_id={self.tenant_id} sender_role={self.sender_role}>"
