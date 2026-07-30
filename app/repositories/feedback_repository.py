from app.models import Feedback, FeedbackStatus
from app.repositories.base_repository import BaseRepository


class FeedbackRepository(BaseRepository[Feedback]):
    """Repositório global (não tenant-scoped) — visitantes anônimos da
    landing page mandam feedback sem loja nenhuma, então o isolamento por
    tenant do TenantScopedRepository não se aplica aqui. Quem quiser só
    os itens de UM tenant (ex: futura tela "minhas sugestões" do
    lojista) filtra explicitamente com `list_for_tenant`."""

    model = Feedback

    def list_ordered_paginated(self, page: int, per_page: int = 20, status: str = "", kind: str = ""):
        from sqlalchemy import select

        from app.extensions import db

        stmt = select(Feedback)
        if status:
            stmt = stmt.where(Feedback.status == status)
        if kind:
            stmt = stmt.where(Feedback.kind == kind)
        stmt = stmt.order_by(Feedback.created_at.desc())
        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    def list_for_tenant(self, tenant_id: int, limit: int = 20):
        return (
            Feedback.query.filter(Feedback.tenant_id == tenant_id)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
            .all()
        )

    def count_new(self) -> int:
        return Feedback.query.filter(Feedback.status == FeedbackStatus.NEW).count()
