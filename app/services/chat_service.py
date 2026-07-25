"""
Chat de suporte entre o lojista e o Super Admin — ver
app/models/chat_message.py para o desenho (um fio por tenant).
"""

from datetime import datetime, timezone

from app.extensions import db
from app.models import ChatMessage, ChatSenderRole
from app.repositories.chat_repository import ChatRepository

MAX_MESSAGE_LENGTH = 2000


class ChatError(Exception):
    pass


class ChatService:
    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id
        self.repo = ChatRepository(tenant_id)

    def list_messages(self) -> list[ChatMessage]:
        return self.repo.list_ordered()

    def send_message(self, *, sender_user, sender_role: ChatSenderRole, body: str) -> ChatMessage:
        body = (body or "").strip()
        if not body:
            raise ChatError("Escreva uma mensagem antes de enviar.")
        if len(body) > MAX_MESSAGE_LENGTH:
            raise ChatError(f"Mensagem muito longa (máximo {MAX_MESSAGE_LENGTH} caracteres).")

        message = ChatMessage(
            tenant_id=self.tenant_id, sender_user_id=sender_user.id, sender_role=sender_role, body=body,
        )
        db.session.add(message)
        db.session.commit()
        return message

    def mark_read(self, *, reader_role: ChatSenderRole) -> None:
        """`reader_role` é quem está LENDO agora — marca como lidas as
        mensagens que vieram do OUTRO lado (nunca marca a própria
        mensagem enviada como "lida", isso não faz sentido)."""
        other_role = ChatSenderRole.SUPER_ADMIN if reader_role == ChatSenderRole.LOJISTA else ChatSenderRole.LOJISTA
        now = datetime.now(timezone.utc)
        unread = (
            self.repo._base_query()
            .filter(ChatMessage.sender_role == other_role, ChatMessage.read_at.is_(None))
            .all()
        )
        if not unread:
            return
        for message in unread:
            message.read_at = now
        db.session.commit()

    def unread_count(self, *, reader_role: ChatSenderRole) -> int:
        other_role = ChatSenderRole.SUPER_ADMIN if reader_role == ChatSenderRole.LOJISTA else ChatSenderRole.LOJISTA
        return (
            self.repo._base_query()
            .filter(ChatMessage.sender_role == other_role, ChatMessage.read_at.is_(None))
            .count()
        )

    @staticmethod
    def tenant_ids_with_unread_messages_for_admin() -> set[int]:
        """Usado na listagem de lojistas do Super Admin, pra sinalizar
        quais têm mensagem nova sem abrir um por um."""
        rows = (
            db.session.query(ChatMessage.tenant_id)
            .filter(ChatMessage.sender_role == ChatSenderRole.LOJISTA, ChatMessage.read_at.is_(None))
            .distinct()
            .all()
        )
        return {row[0] for row in rows}

    @staticmethod
    def list_conversations_for_admin() -> list[dict]:
        """Uma linha por loja que já trocou pelo menos uma mensagem,
        com a última mensagem e quantas estão sem ler pelo admin — usado
        pela Central de Suporte (não mistura com a gestão da loja em si,
        ver app/controllers/admin/chat.py:support_inbox)."""
        from app.models import Tenant

        tenant_ids = [row[0] for row in db.session.query(ChatMessage.tenant_id).distinct().all()]
        if not tenant_ids:
            return []

        unread_ids = ChatService.tenant_ids_with_unread_messages_for_admin()

        conversations = []
        for tenant_id in tenant_ids:
            tenant = db.session.get(Tenant, tenant_id)
            if tenant is None:
                continue  # loja pode ter sido removida, mensagens ficam órfãs por CASCADE ainda não rodado
            last_message = (
                ChatMessage.query.filter_by(tenant_id=tenant_id).order_by(ChatMessage.created_at.desc()).first()
            )
            conversations.append({
                "tenant": tenant,
                "last_message": last_message,
                "has_unread": tenant_id in unread_ids,
            })

        conversations.sort(
            key=lambda c: (
                not c["has_unread"],  # não lidas primeiro
                -(c["last_message"].created_at.timestamp() if c["last_message"] else 0),  # depois, mais recente
            )
        )
        return conversations
