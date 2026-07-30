"""
Sugestões e reportes de erro — recebidos tanto de visitantes anônimos da
landing page (sem conta, sem loja) quanto de lojistas já logados no
painel. Modelo único (`Feedback`) para os dois casos, distinguidos por
`tenant_id`/`submitted_by_user_id` nulos ou não — evita duplicar telas de
gestão no Super Admin para "sugestão de visitante" vs "sugestão de
lojista", que no fundo são a mesma coisa (uma mensagem com um tipo e um
status de acompanhamento).

Diferente do ChatMessage (conversa contínua de suporte por tenant), aqui
é um formulário avulso: sem fio de conversa, sem resposta em tempo real —
o Super Admin só acompanha e muda o status conforme trata cada item.
"""

import enum

from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin, str_enum


class FeedbackKind(str, enum.Enum):
    SUGGESTION = "suggestion"
    BUG_REPORT = "bug_report"


class FeedbackStatus(str, enum.Enum):
    NEW = "new"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


FEEDBACK_KIND_LABELS = {
    FeedbackKind.SUGGESTION.value: "Sugestão",
    FeedbackKind.BUG_REPORT.value: "Reporte de erro",
}

FEEDBACK_STATUS_LABELS = {
    FeedbackStatus.NEW.value: "Novo",
    FeedbackStatus.IN_REVIEW.value: "Em análise",
    FeedbackStatus.RESOLVED.value: "Resolvido",
    FeedbackStatus.ARCHIVED.value: "Arquivado",
}


class Feedback(db.Model, TimestampMixin):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True)

    # Nulo quando é um visitante anônimo da landing page (sem conta, sem
    # loja) — por isso NÃO usa TenantScopedMixin (que exige tenant_id
    # obrigatório). SET NULL em vez de CASCADE: se a loja for removida,
    # o histórico do que ela reportou continua útil para o Super Admin.
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    kind = Column(str_enum(FeedbackKind, "feedback_kind"), nullable=False)
    status = Column(str_enum(FeedbackStatus, "feedback_status"), nullable=False, default=FeedbackStatus.NEW)

    # Preenchidos só no formulário público (visitante anônimo) — o
    # lojista logado já tem nome/e-mail na própria conta, não precisa
    # redigitar.
    name = Column(String(150), nullable=True)
    email = Column(String(255), nullable=True)

    message = Column(Text, nullable=False)

    # Anotação livre do Super Admin ao tratar o item (ex: "corrigido na
    # versão X", "não reproduzido") — só ele vê, não é uma resposta
    # enviada de volta a quem reportou.
    admin_notes = Column(Text, nullable=True)

    tenant = relationship("Tenant")
    submitted_by = relationship("User")

    @property
    def kind_label(self) -> str:
        value = self.kind.value if hasattr(self.kind, "value") else self.kind
        return FEEDBACK_KIND_LABELS.get(value, value)

    @property
    def status_label(self) -> str:
        value = self.status.value if hasattr(self.status, "value") else self.status
        return FEEDBACK_STATUS_LABELS.get(value, value)

    def __repr__(self):
        return f"<Feedback id={self.id} kind={self.kind} status={self.status}>"
