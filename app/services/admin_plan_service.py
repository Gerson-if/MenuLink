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
               allow_qr_customization: bool = False, allow_whatsapp_notifications: bool = False,
               allow_order_print_ticket: bool = False) -> Plan:
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
            allow_order_print_ticket=allow_order_print_ticket,
        )
        db.session.add(plan)
        if is_featured:
            self._unfeature_other_plans(plan)
        db.session.commit()
        return plan

    def update(self, plan: Plan, *, name: str, description: str, price_reais: float, billing_cycle: str,
               max_categories: int | None, max_products: int | None, max_images_per_product: int,
               is_featured: bool = False, display_order: int | None = 0,
               allow_qr_customization: bool = False, allow_whatsapp_notifications: bool = False,
               allow_order_print_ticket: bool = False) -> Plan:
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
        plan.allow_order_print_ticket = allow_order_print_ticket
        if is_featured:
            self._unfeature_other_plans(plan)
        db.session.commit()
        return plan

    def _unfeature_other_plans(self, plan: Plan) -> None:
        """`is_featured` é exclusivo — só um plano por vez deve levar o
        selo "MAIS VENDIDO" na landing page (ver marketing/landing.html).
        Sem isso, marcar um segundo plano como destaque deixava a
        landing com dois selos ao mesmo tempo, o que não faz sentido
        pro visitante."""
        query = Plan.query.filter(Plan.is_featured.is_(True))
        if plan.id is not None:
            query = query.filter(Plan.id != plan.id)
        query.update({"is_featured": False}, synchronize_session=False)

    def toggle_active(self, plan: Plan) -> Plan:
        plan.is_active = not plan.is_active
        db.session.commit()
        return plan

    def delete(self, plan: Plan) -> None:
        """Exclui um plano permanentemente — só quando ele está
        completamente sem uso (nenhuma loja nele, nenhuma assinatura no
        histórico, nenhum downgrade agendado apontando pra ele), pra
        nunca chegar a violar a foreign key de tenants.plan_id /
        subscriptions.plan_id / subscriptions.pending_plan_id (nenhuma
        delas tem `ondelete` definido, então o banco recusaria com
        IntegrityError). Se estiver em uso, recusa com uma mensagem
        clara em vez de deixar o erro do banco estourar — quem quiser
        excluir mesmo assim precisa primeiro migrar as lojas para outro
        plano (editando cada uma) ou simplesmente desativar o plano
        (ver toggle_active), que já impede que ele seja escolhido em
        lojas novas sem apagar o histórico de quem já está nele.

        `pending_plan_id` (ver Subscription — downgrade agendado pro
        fim do ciclo atual, ver TenantSettingsService._schedule_downgrade)
        é checado à parte: um plano pode não ter NENHUM tenant/assinatura
        apontando pra ele hoje (os dois primeiros contadores dão zero) e
        ainda assim ser o destino de um downgrade agendado de outra
        loja — sem essa checagem, excluir um plano "aparentemente sem
        uso" quebrava com IntegrityError na hora da próxima renovação
        daquela assinatura."""
        tenants_count = Tenant.query.filter_by(plan_id=plan.id).count()
        subscriptions_count = Subscription.query.filter_by(plan_id=plan.id).count()
        pending_downgrades_count = Subscription.query.filter_by(pending_plan_id=plan.id).count()
        if tenants_count or subscriptions_count or pending_downgrades_count:
            parts = []
            if tenants_count:
                parts.append(f"{tenants_count} loja(s) atualmente")
            if subscriptions_count:
                parts.append(f"{subscriptions_count} assinatura(s) no histórico de cobrança")
            if pending_downgrades_count:
                parts.append(f"{pending_downgrades_count} downgrade(s) agendado(s) pra ele")
            raise PlanAdminError(
                "Não é possível excluir: este plano ainda está em uso por "
                + " e ".join(parts)
                + ". Mude essas lojas para outro plano antes de excluir, ou apenas desative o plano "
                "(assim ele some das opções para lojas novas, sem apagar nada de quem já usa ele)."
            )
        db.session.delete(plan)
        db.session.commit()

    def tenants_exceeding_limits(self, plan: Plan) -> list[str]:
        """Lojas ATUALMENTE neste plano cujo uso (produtos/categorias)
        já ultrapassa os limites deste plano — chamado depois de salvar
        uma edição que apertou max_products/max_categories, só pra
        avisar o Super Admin (não bloqueia nem altera nada: apertar o
        limite de um plano em uso é uma decisão válida do Super Admin,
        só não deve pegar ninguém de surpresa)."""
        from app.services.plan_limits import PlanLimitExceeded, check_plan_limits

        names = []
        for tenant in plan.tenants:
            try:
                check_plan_limits(tenant, plan)
            except PlanLimitExceeded:
                names.append(tenant.name)
        return names
