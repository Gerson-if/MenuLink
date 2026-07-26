from app.models import Plan
from app.repositories.base_repository import BaseRepository


class PlanRepository(BaseRepository[Plan]):
    model = Plan

    def get_by_slug(self, slug: str):
        return self.model.query.filter_by(slug=slug).first()

    def list_ordered(self):
        return self.model.query.order_by(Plan.price_cents).all()

    def list_active_for_landing(self):
        return self.model.query.filter_by(is_active=True).order_by(Plan.display_order, Plan.price_cents).all()

    def get_cheapest_active(self):
        """O plano mais barato entre os disponíveis para contratação —
        usado como padrão para lojas novas que não têm um plano
        explicitamente escolhido (ver AdminTenantService.create), pra
        nenhum lojista ficar com plano nulo. Ordena por preço puro (não
        por `display_order`, que é só curadoria de marketing da landing
        page) — `None` se não houver nenhum plano ativo cadastrado
        ainda (instalação nova, antes do Super Admin criar o primeiro
        plano)."""
        return self.model.query.filter_by(is_active=True).order_by(Plan.price_cents, Plan.id).first()
