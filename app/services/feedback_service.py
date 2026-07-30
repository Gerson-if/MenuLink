"""
Sugestões e reportes de erro — ver app/models/feedback.py para o desenho
do modelo (um só, compartilhado entre visitante anônimo da landing page e
lojista logado no painel).
"""

from flask import abort

from app.extensions import db
from app.models import Feedback, FeedbackKind, FeedbackStatus
from app.repositories.feedback_repository import FeedbackRepository

MAX_MESSAGE_LENGTH = 3000
MAX_NAME_LENGTH = 150
MAX_EMAIL_LENGTH = 255

VALID_KINDS = {FeedbackKind.SUGGESTION.value, FeedbackKind.BUG_REPORT.value}
VALID_STATUSES = {s.value for s in FeedbackStatus}


class FeedbackError(Exception):
    pass


class FeedbackService:
    def __init__(self):
        self.repo = FeedbackRepository()

    def submit(
        self, *, kind: str, message: str, tenant_id: int | None = None, submitted_by_user=None,
        name: str | None = None, email: str | None = None,
    ) -> Feedback:
        kind = (kind or "").strip()
        if kind not in VALID_KINDS:
            raise FeedbackError("Tipo de mensagem inválido.")

        message = (message or "").strip()
        if not message:
            raise FeedbackError("Escreva sua mensagem antes de enviar.")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise FeedbackError(f"Mensagem muito longa (máximo {MAX_MESSAGE_LENGTH} caracteres).")

        name = (name or "").strip() or None
        if name and len(name) > MAX_NAME_LENGTH:
            raise FeedbackError("Nome muito longo.")

        email = (email or "").strip() or None
        if email and len(email) > MAX_EMAIL_LENGTH:
            raise FeedbackError("E-mail muito longo.")

        feedback = Feedback(
            kind=kind,
            status=FeedbackStatus.NEW,
            tenant_id=tenant_id,
            submitted_by_user_id=submitted_by_user.id if submitted_by_user else None,
            name=name,
            email=email,
            message=message,
        )
        db.session.add(feedback)
        db.session.commit()
        return feedback

    def list_for_admin(self, page: int, status: str = "", kind: str = ""):
        return self.repo.list_ordered_paginated(page=page, status=status, kind=kind)

    def get_or_404(self, feedback_id: int) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            abort(404)
        return feedback

    def update_status(self, feedback: Feedback, *, status: str, admin_notes: str | None = None) -> Feedback:
        status = (status or "").strip()
        if status not in VALID_STATUSES:
            raise FeedbackError("Status inválido.")
        feedback.status = status
        if admin_notes is not None:
            feedback.admin_notes = admin_notes.strip() or None
        db.session.commit()
        return feedback

    @staticmethod
    def new_count() -> int:
        return FeedbackRepository().count_new()
