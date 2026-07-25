from flask import flash, redirect, render_template, request, url_for

from app.forms.admin_forms import PlanForm
from app.services.admin_plan_service import AdminPlanService
from app.utils.decorators import super_admin_required
from app.utils.htmx import hx_fragment_response

from app.controllers.admin import admin_bp


@admin_bp.route("/planos")
@super_admin_required
def plans_list():
    service = AdminPlanService()
    plans = service.list_all()
    return render_template("admin/plans/list.html", plans=plans)


def _rerender_plan_fragment(plan, *, trigger=None, toast=None):
    """Ver _rerender_banner_fragment em lojista/banners.py — mesmo padrão
    pro drawer de planos: devolve só o fragmento, com HX-Trigger opcional
    pra avisar a listagem de fundo (ver plans/list.html,
    hx-trigger="planSaved from:body") e um toast de confirmação (ver
    app/utils/htmx.py) — sem ele, salvar pelo drawer não dava nenhum
    sinal visível de que a gravação deu certo."""
    form = PlanForm(obj=plan)
    form.price.data = plan.price_cents / 100
    form.billing_cycle.data = plan.billing_cycle.value
    html = render_template(
        "admin/plans/_form_fragment.html", form=form, plan=plan,
        form_action=url_for("admin.plans_edit", plan_id=plan.id), hide_back_link=True,
    )
    return hx_fragment_response(html, trigger=trigger, toast=toast)


@admin_bp.route("/planos/novo", methods=["GET", "POST"])
@super_admin_required
def plans_create():
    form = PlanForm()
    is_hx = bool(request.headers.get("HX-Request"))

    if form.validate_on_submit():
        service = AdminPlanService()
        plan = service.create(
            name=form.name.data,
            description=form.description.data,
            price_reais=float(form.price.data),
            billing_cycle=form.billing_cycle.data,
            max_categories=form.max_categories.data,
            max_products=form.max_products.data,
            max_images_per_product=form.max_images_per_product.data,
            is_featured=form.is_featured.data,
            display_order=form.display_order.data,
        )
        if is_hx:
            return _rerender_plan_fragment(plan, trigger="planSaved", toast="Plano criado com sucesso.")
        flash("Plano criado com sucesso.", "success")
        return redirect(url_for("admin.plans_list"))

    if is_hx:
        return render_template(
            "admin/plans/_form_fragment.html", form=form, plan=None,
            form_action=url_for("admin.plans_create"), hide_back_link=True,
        )
    return render_template("admin/plans/form.html", form=form, plan=None)


@admin_bp.route("/planos/<int:plan_id>/editar", methods=["GET", "POST"])
@super_admin_required
def plans_edit(plan_id):
    service = AdminPlanService()
    plan = service.get_or_404(plan_id)
    is_hx = bool(request.headers.get("HX-Request"))

    form = PlanForm(obj=plan)
    if request.method == "GET":
        form.price.data = plan.price_cents / 100
        form.billing_cycle.data = plan.billing_cycle.value

    if form.validate_on_submit():
        service.update(
            plan,
            name=form.name.data,
            description=form.description.data,
            price_reais=float(form.price.data),
            billing_cycle=form.billing_cycle.data,
            max_categories=form.max_categories.data,
            max_products=form.max_products.data,
            max_images_per_product=form.max_images_per_product.data,
            is_featured=form.is_featured.data,
            display_order=form.display_order.data,
        )
        if is_hx:
            return _rerender_plan_fragment(plan, trigger="planSaved", toast="Plano atualizado com sucesso.")
        flash("Plano atualizado com sucesso.", "success")
        return redirect(url_for("admin.plans_list"))

    if is_hx:
        return render_template(
            "admin/plans/_form_fragment.html", form=form, plan=plan,
            form_action=url_for("admin.plans_edit", plan_id=plan.id), hide_back_link=True,
        )
    return render_template("admin/plans/form.html", form=form, plan=plan)


@admin_bp.route("/planos/<int:plan_id>/alternar", methods=["POST"])
@super_admin_required
def plans_toggle(plan_id):
    service = AdminPlanService()
    plan = service.get_or_404(plan_id)
    service.toggle_active(plan)
    return render_template("admin/plans/_status_badge.html", plan=plan)
