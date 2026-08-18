from app.extensions import db
from app.models import Plan, Subscription, Tenant
from app.repositories.plan_repository import PlanRepository
from app.utils.slugs import generate_unique_slug


class PlanAdminError(Exception):
    """Erro de regra de negócio, com mensagem segura para exibir ao Super Admin."""


class AdminPlanService:
    def __init__(self):
        self.repo = PlanRepository()

    def list_all(self):
        return self.repo.list_ordered()

    def get_or_404(self, plan_id: int) -> Plan:
        from flask import abort

        plan = self.repo.get_by_id(plan_id)
        if plan is None:
            abort(404)
        return plan

    def create(self, *, name: str, description: str, price_reais: float, billing_cycle: str,
               max_categories: int | None, max_products: int | None, max_images_per_product: int,
               is_featured: bool = False, display_order: int | None = 0,
               allow_qr_customization: bool = False, allow_whatsapp_notifications: bool = False) -> Plan:
        slug = generate_unique_slug(name, self.repo.get_by_slug)
        plan = Plan(
            name=name.strip(),
            slug=slug,
            description=(description or "").strip() or None,
            price_cents=round(price_reais * 100),
            billing_cycle=billing_cycle,
            max_categories=max_categories,
            max_products=max_products,
            max_images_per_product=max_images_per_product,
            is_featured=is_featured,
            display_order=display_order or 0,
            allow_qr_customization=allow_qr_customization,
            allow_whatsapp_notifications=allow_whatsapp_notifications,
        )
        db.session.add(plan)
        db.session.commit()
        return plan

    def update(self, plan: Plan, *, name: str, description: str, price_reais: float, billing_cycle: str,
               max_categories: int | None, max_products: int | None, max_images_per_product: int,
               is_featured: bool = False, display_order: int | None = 0,
               allow_qr_customization: bool = False, allow_whatsapp_notifications: bool = False) -> Plan:
        if name.strip() != plan.name:
            plan.slug = generate_unique_slug(name, self.repo.get_by_slug, current_id=plan.id)
        plan.name = name.strip()
        plan.description = (description or "").strip() or None
        plan.price_cents = round(price_reais * 100)
        plan.billing_cycle = billing_cycle
        plan.max_categories = max_categories
        plan.max_products = max_products
        plan.max_images_per_product = max_images_per_product
        plan.is_featured = is_featured
        plan.display_order = display_order or 0
        plan.allow_qr_customization = allow_qr_customization
        plan.allow_whatsapp_notifications = allow_whatsapp_notifications
        db.session.commit()
        return plan

    def toggle_active(self, plan: Plan) -> Plan:
        plan.is_active = not plan.is_active
        db.session.commit()
        return plan

    def delete(self, plan: Plan) -> None:
        """Exclui um plano permanentemente — só quando ele está
        completamente sem uso (nenhuma loja nele, nenhuma assinatura no
        histórico), pra nunca chegar a violar a foreign key de
        tenants.plan_id / subscriptions.plan_id (nenhuma delas tem
        `ondelete` definido, então o banco recusaria com IntegrityError).
        Se estiver em uso, recusa com uma mensagem clara em vez de deixar
        o erro do banco estourar — quem quiser excluir mesmo assim
        precisa primeiro migrar as lojas para outro plano (editando cada
        uma) ou simplesmente desativar o plano (ver toggle_active), que
        já impede que ele seja escolhido em lojas novas sem apagar o
        histórico de quem já está nele."""
        tenants_count = Tenant.query.filter_by(plan_id=plan.id).count()
        subscriptions_count = Subscription.query.filter_by(plan_id=plan.id).count()
        if tenants_count or subscriptions_count:
            parts = []
            if tenants_count:
                parts.append(f"{tenants_count} loja(s) atualmente")
            if subscriptions_count:
                parts.append(f"{subscriptions_count} assinatura(s) no histórico de cobrança")
            raise PlanAdminError(
                "Não é possível excluir: este plano ainda está em uso por "
                + " e ".join(parts)
                + ". Mude essas lojas para outro plano antes de excluir, ou apenas desative o plano "
                "(assim ele some das opções para lojas novas, sem apagar nada de quem já usa ele)."
            )
        db.session.delete(plan)
        db.session.commit()
