"""
Repositório de Tenant para uso do Super Administrador.

Diferente dos repositórios "TenantScoped" usados no painel do lojista,
este NÃO filtra por tenant — o Super Admin enxerga e gerencia todos os
lojistas da plataforma. Nunca deve ser usado a partir do painel do
lojista (só de rotas protegidas por @super_admin_required).
"""

from typing import Optional

from app.models import Tenant
from app.repositories.base_repository import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    model = Tenant

    def get_by_slug(self, slug: str) -> Optional[Tenant]:
        return self.model.query.filter_by(slug=slug).first()

    def get_by_email(self, email: str) -> Optional[Tenant]:
        return self.model.query.filter_by(email=email.lower().strip()).first()

    def search(self, term: str = "", status: str = ""):
        query = self.model.query

        if term:
            like = f"%{term.strip()}%"
            query = query.filter(
                (Tenant.name.ilike(like))
                | (Tenant.email.ilike(like))
                | (Tenant.slug.ilike(like))
            )

        if status:
            query = query.filter(Tenant.status == status)

        return query.order_by(Tenant.created_at.desc()).all()

    def search_paginated(self, page: int, per_page: int = 20, term: str = "", status: str = "", tenant_ids=None):
        from sqlalchemy import select

        from app.extensions import db

        stmt = select(Tenant)

        if term:
            like = f"%{term.strip()}%"
            stmt = stmt.where(
                (Tenant.name.ilike(like))
                | (Tenant.email.ilike(like))
                | (Tenant.slug.ilike(like))
            )

        if status:
            stmt = stmt.where(Tenant.status == status)

        if tenant_ids is not None:
            # Usado pelo atalho "Suporte" da sidebar do Super Admin (ver
            # tenants_list) — mostra só as lojas com mensagem não lida,
            # sem duplicar a lógica de busca/paginação.
            stmt = stmt.where(Tenant.id.in_(tenant_ids))

        stmt = stmt.order_by(Tenant.created_at.desc())
        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)
