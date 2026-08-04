"""
Chat de suporte entre o lojista e o Super Admin — ver
app/models/chat_message.py para o desenho (um fio por tenant).
"""

from datetime import datetime, timezone

from app.extensions import db
from app.models import ChatMessage, ChatSenderRole
from app.repositories.chat_repository import ChatRepository
from app.utils.uploads import InvalidAttachmentError, save_chat_attachment

MAX_MESSAGE_LENGTH = 2000


class ChatError(Exception):
    pass


class ChatService:
    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id
        self.repo = ChatRepository(tenant_id)

    def list_messages(self) -> list[ChatMessage]:
        return self.repo.list_ordered()

    def send_message(
        self, *, sender_user, sender_role: ChatSenderRole, body: str, attachment=None,
    ) -> ChatMessage:
        """`attachment`: um `werkzeug.datastructures.FileStorage` (vindo
        de `request.files.get('attachment')`) ou None. Uma mensagem
        agora pode ser SÓ texto, SÓ anexo, ou os dois — mas nunca
        nenhum dos dois (ver checagem abaixo). A validação de
        formato/tamanho do arquivo fica em `save_chat_attachment` (ver
        app/utils/uploads.py); qualquer `InvalidAttachmentError` de lá
        vira um `ChatError` em português, igual às outras validações
        deste método — quem chama só precisa tratar um tipo de erro."""
        body = (body or "").strip()
        if len(body) > MAX_MESSAGE_LENGTH:
            raise ChatError(f"Mensagem muito longa (máximo {MAX_MESSAGE_LENGTH} caracteres).")

        attachment_path = attachment_original_name = attachment_mimetype = None
        attachment_size_bytes = None
        # `getattr(attachment, "filename", "")`: um FileField sem arquivo
        # selecionado ainda chega aqui como um FileStorage "vazio" (nome
        # em branco), não como None — testar só `attachment is not None`
        # trataria TODO envio de texto puro como se tivesse anexo.
        if attachment is not None and getattr(attachment, "filename", ""):
            try:
                (
                    attachment_path, attachment_original_name,
                    attachment_mimetype, attachment_size_bytes,
                ) = save_chat_attachment(self.tenant_id, attachment)
            except InvalidAttachmentError as exc:
                raise ChatError(str(exc))

        if not body and not attachment_path:
            raise ChatError("Escreva uma mensagem ou anexe um arquivo antes de enviar.")

        message = ChatMessage(
            tenant_id=self.tenant_id, sender_user_id=sender_user.id, sender_role=sender_role, body=body,
            attachment_path=attachment_path, attachment_original_name=attachment_original_name,
            attachment_mimetype=attachment_mimetype, attachment_size_bytes=attachment_size_bytes,
        )
        db.session.add(message)
        db.session.commit()
        return message

    @staticmethod
    def send_system_message(tenant_id: int, body: str) -> ChatMessage:
        """Mensagem automática da PLATAFORMA (não de um usuário humano) —
        usada pelos lembretes/avisos de período de teste (ver
        app/services/trial_service.py). Aparece no chat com o mesmo
        estilo visual de uma mensagem do Super Admin (mesmo
        `sender_role`, já que não há um terceiro papel "sistema" no
        modelo — ver ChatSenderRole), mas com `sender_user_id` nulo. Ao
        contrário de `send_message`, não valida tamanho/vazio: quem
        chama isso já controla o texto (mensagens fixas ou geradas a
        partir de um template configurado pelo Super Admin)."""
        message = ChatMessage(
            tenant_id=tenant_id, sender_user_id=None, sender_role=ChatSenderRole.SUPER_ADMIN, body=body,
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
        """UMA LINHA POR LOJA (não só as que já mandaram mensagem) — a
        Central de Suporte funciona como uma lista de "contatos": o Super
        Admin precisa conseguir puxar assunto com qualquer lojista, não só
        responder quem já escreveu primeiro. Lojas sem nenhuma mensagem
        aparecem no fim da lista, ordenadas por nome, com "Nenhuma
        mensagem ainda" no lugar da prévia — ver
        app/controllers/admin/chat.py:support_inbox."""
        from app.models import Tenant

        tenants = Tenant.query.order_by(Tenant.name).all()
        if not tenants:
            return []

        unread_ids = ChatService.tenant_ids_with_unread_messages_for_admin()

        last_messages_by_tenant = {
            row.tenant_id: row
            for row in ChatMessage.query.order_by(ChatMessage.created_at.asc())
        }  # a última ocorrência de cada tenant_id na iteração ordenada vence

        conversations = []
        for tenant in tenants:
            conversations.append({
                "tenant": tenant,
                "last_message": last_messages_by_tenant.get(tenant.id),
                "has_unread": tenant.id in unread_ids,
            })

        conversations.sort(
            key=lambda c: (
                not c["has_unread"],  # não lidas primeiro
                c["last_message"] is None,  # depois quem já tem conversa
                -(c["last_message"].created_at.timestamp() if c["last_message"] else 0),  # mais recente primeiro
                c["tenant"].name.lower(),  # sem conversa nenhuma: ordem alfabética
            )
        )
        return conversations
