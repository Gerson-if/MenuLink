from flask import flash, make_response, redirect, render_template, request, url_for

from app.forms.admin_forms import SubscriptionCycleForm, TenantCreateForm, TenantEditForm, TenantStatusReasonForm
from app.forms.chat_forms import ChatMessageForm
from app.models import ChatSenderRole
from app.repositories.plan_repository import PlanRepository
from app.services.admin_billing_service import AdminBillingService
from app.services.admin_tenant_service import AdminTenantService, TenantAdminError
from app.services.chat_service import ChatService
from app.utils.decorators import super_admin_required
from app.utils.htmx import hx_fragment_response

from app.controllers.admin import admin_bp


def _populate_plan_choices(form):
    plans = PlanRepository().list_ordered()
    form.plan_id.choices = [(0, "Sem plano")] + [(p.id, p.name) for p in plans]


def _rerender_tenant_fragment(tenant, *, trigger=None, toast=None):
    """Ver _rerender_banner_fragment em lojista/banners.py — mesmo padrão
    pra gaveta de "Editar dados" no detalhe do lojista: devolve só o
    fragmento, com HX-Trigger opcional pra avisar o painel "Dados da
    loja" (ver tenants/detail.html, hx-trigger="tenantSaved from:body")
    que precisa se atualizar, e um toast de confirmação (ver
    app/utils/htmx.py) — sem ele, salvar pelo drawer não dava nenhum
    sinal visível de que a gravação deu certo."""
    form = TenantEditForm(obj=tenant)
    form.plan_id.data = tenant.plan_id or 0
    _populate_plan_choices(form)
    html = render_template(
        "admin/tenants/_form_fragment.html", form=form, tenant=tenant,
        form_action=url_for("admin.tenants_edit", tenant_id=tenant.id), hide_back_link=True,
    )
    return hx_fragment_response(html, trigger=trigger, toast=toast)


@admin_bp.route("/lojistas")
@super_admin_required
def tenants_list():
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)
    chat_filter = request.args.get("chat", "").strip()

    unread_chat_tenant_ids = ChatService.tenant_ids_with_unread_messages_for_admin()

    service = AdminTenantService()
    pagination = service.repo.search_paginated(
        page=page, per_page=20, term=term, status=status,
        tenant_ids=unread_chat_tenant_ids if chat_filter == "unread" else None,
    )

    return render_template(
        "admin/tenants/list.html",
        tenants=pagination.items,
        pagination=pagination,
        term=term,
        status=status,
        chat_filter=chat_filter,
        unread_chat_tenant_ids=unread_chat_tenant_ids,
    )


@admin_bp.route("/lojistas/novo", methods=["GET", "POST"])
@super_admin_required
def tenants_create():
    form = TenantCreateForm()
    _populate_plan_choices(form)
    is_hx = bool(request.headers.get("HX-Request"))

    if form.validate_on_submit():
        service = AdminTenantService()
        try:
            tenant = service.create(
                name=form.name.data,
                email=form.email.data,
                phone=form.phone.data,
                whatsapp_number=form.whatsapp_number.data,
                plan_id=form.plan_id.data or None,
                owner_name=form.owner_name.data,
                owner_email=form.owner_email.data,
                owner_password=form.owner_password.data,
            )
        except TenantAdminError as exc:
            flash(str(exc), "danger")
        else:
            flash(f"Loja '{tenant.name}' criada com sucesso.", "success")
            if is_hx:
                # Loja nova tem muita coisa pra configurar (fatura, plano,
                # status) — diferente de plano/banner, aqui faz mais
                # sentido navegar de vez pro detalhe dela do que ficar na
                # gaveta. HX-Redirect é o jeito do HTMX de forçar uma
                # navegação de página inteira a partir de uma resposta.
                response = make_response("")
                response.headers["HX-Redirect"] = url_for("admin.tenants_detail", tenant_id=tenant.id)
                return response
            return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))

    if is_hx:
        return render_template(
            "admin/tenants/_form_fragment.html", form=form, tenant=None,
            form_action=url_for("admin.tenants_create"), hide_back_link=True,
        )
    return render_template("admin/tenants/form.html", form=form, tenant=None)


@admin_bp.route("/lojistas/<int:tenant_id>")
@super_admin_required
def tenants_detail(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)

    billing_service = AdminBillingService()
    invoices = billing_service.list_invoices_for_tenant(tenant.id)
    reason_form = TenantStatusReasonForm()
    subscription = billing_service.get_subscription_for_tenant(tenant.id)

    cycle_form = SubscriptionCycleForm()
    if subscription is not None:
        cycle_form.billing_cycle.data = subscription.billing_cycle.value

    chat_service = ChatService(tenant.id)
    chat_service.mark_read(reader_role=ChatSenderRole.SUPER_ADMIN)

    return render_template(
        "admin/tenants/detail.html",
        tenant=tenant,
        invoices=invoices,
        reason_form=reason_form,
        asaas_configured=billing_service.is_asaas_configured(),
        subscription=subscription,
        cycle_form=cycle_form,
        chat_messages=chat_service.list_messages(),
        chat_form=ChatMessageForm(),
    )


@admin_bp.route("/lojistas/<int:tenant_id>/editar", methods=["GET", "POST"])
@super_admin_required
def tenants_edit(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    is_hx = bool(request.headers.get("HX-Request"))

    form = TenantEditForm(obj=tenant)
    if request.method == "GET":
        form.plan_id.data = tenant.plan_id or 0
    _populate_plan_choices(form)

    if form.validate_on_submit():
        try:
            service.update(
                tenant,
                name=form.name.data,
                email=form.email.data,
                phone=form.phone.data,
                whatsapp_number=form.whatsapp_number.data,
                plan_id=form.plan_id.data or None,
            )
        except TenantAdminError as exc:
            flash(str(exc), "danger")
        else:
            if is_hx:
                return _rerender_tenant_fragment(tenant, trigger="tenantSaved", toast="Dados da loja atualizados.")
            flash("Dados da loja atualizados.", "success")
            return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))

    if is_hx:
        return render_template(
            "admin/tenants/_form_fragment.html", form=form, tenant=tenant,
            form_action=url_for("admin.tenants_edit", tenant_id=tenant.id), hide_back_link=True,
        )
    return render_template("admin/tenants/form.html", form=form, tenant=tenant)


@admin_bp.route("/lojistas/<int:tenant_id>/excluir", methods=["POST"])
@super_admin_required
def tenants_delete(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    name = tenant.name

    service.delete(tenant)
    flash(f"Loja '{name}' e todos os seus dados foram excluídos permanentemente.", "info")
    return redirect(url_for("admin.tenants_list"))


@admin_bp.route("/lojistas/<int:tenant_id>/ativar", methods=["POST"])
@super_admin_required
def tenants_activate(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    service.activate(tenant)
    flash(f"Loja '{tenant.name}' ativada.", "success")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/lojistas/<int:tenant_id>/suspender", methods=["POST"])
@super_admin_required
def tenants_suspend(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    reason = request.form.get("reason")
    service.suspend(tenant, reason=reason)
    flash(f"Loja '{tenant.name}' suspensa.", "warning")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/lojistas/<int:tenant_id>/bloquear", methods=["POST"])
@super_admin_required
def tenants_block(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    reason = request.form.get("reason")
    service.block_for_payment(tenant, reason=reason)
    flash(f"Loja '{tenant.name}' bloqueada por inadimplência.", "warning")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/lojistas/<int:tenant_id>/cancelar", methods=["POST"])
@super_admin_required
def tenants_cancel(tenant_id):
    service = AdminTenantService()
    tenant = service.get_or_404(tenant_id)
    reason = request.form.get("reason")
    service.cancel(tenant, reason=reason)
    flash(f"Loja '{tenant.name}' cancelada.", "danger")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))
