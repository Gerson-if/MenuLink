"""
Chat simples entre o lojista (dono/equipe da loja) e o Super Admin —
um único fio de conversa por tenant (não por usuário: se a loja tem
mais de um funcionário com acesso, todos compartilham a mesma
conversa com a plataforma, como um chat de suporte).

Atualização "ao vivo" via polling HTTP (HTMX, `hx-trigger="every Ns"`)
— sem WebSocket/fila nova, consistente com o resto do app.
"""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
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

    # `nullable=False` com `server_default=''` (não `nullable=True`) DE
    # PROPÓSITO: uma mensagem agora pode ser só-anexo, sem texto nenhum
    # (ver ChatService.send_message) — mas manter a coluna NOT NULL evita
    # espalhar checagem de `None` por todo template/serviço que já lê
    # `message.body` esperando uma string (ex: _chat_messages.html,
    # list_conversations_for_admin) — uma string vazia já se comporta do
    # jeito certo em todos esses lugares sem precisar mudar mais nada.
    body = Column(Text, nullable=False, server_default="")

    # Preenchido quando o OUTRO lado (quem não mandou a mensagem) a viu —
    # base pro contador de "não lidas" de cada lado (ver ChatService).
    read_at = Column(DateTime(timezone=True), nullable=True)

    # Anexo opcional (comprovante de pagamento etc — ver
    # app/utils/uploads.py:save_chat_attachment). Todos nullable: a
    # imensa maioria das mensagens continua sendo só texto, sem anexo
    # nenhum. `attachment_path` é relativo a static/ (mesmo padrão de
    # ProductImage.file_path), ex: "uploads/tenant_3/chat/8f1c...a2.webp".
    attachment_path = Column(String(255), nullable=True)
    attachment_original_name = Column(String(255), nullable=True)
    attachment_mimetype = Column(String(100), nullable=True)
    attachment_size_bytes = Column(Integer, nullable=True)

    sender = relationship("User")

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachment_path)

    @property
    def attachment_is_image(self) -> bool:
        return bool(self.attachment_mimetype and self.attachment_mimetype.startswith("image/"))

    def __repr__(self):
        return f"<ChatMessage tenant_id={self.tenant_id} sender_role={self.sender_role}>"
