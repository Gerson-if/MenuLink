from app.models import ChatMessage
from app.repositories.base_repository import TenantScopedRepository


class ChatRepository(TenantScopedRepository[ChatMessage]):
    model = ChatMessage

    def list_ordered(self):
        return self._base_query().order_by(ChatMessage.created_at.asc()).all()
