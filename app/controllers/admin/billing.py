from flask import flash, redirect, render_template, url_for

from app.forms.admin_forms import InvoiceForm, SubscriptionCycleForm
from app.models import BillingCycle
from app.services.admin_billing_service import AdminBillingService, BillingError
from app.services.admin_tenant_service import AdminTenantService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/lojistas/<int:tenant_id>/faturas/nova", methods=["POST"])
@super_admin_required
def invoices_create(tenant_id):
    tenant = AdminTenantService().get_or_404(tenant_id)
    form = InvoiceForm()

    if form.validate_on_submit():
        try:
            AdminBillingService().create_invoice(
                tenant, amount_reais=float(form.amount.data), due_date=form.due_date.data
            )
        except BillingError as exc:
            flash(str(exc), "danger")
        else:
            flash("Fatura lançada com sucesso.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/lojistas/<int:tenant_id>/assinatura/ciclo", methods=["POST"])
@super_admin_required
def subscription_update_cycle(tenant_id):
    tenant = AdminTenantService().get_or_404(tenant_id)
    form = SubscriptionCycleForm()

    if form.validate_on_submit():
        service = AdminBillingService()
        try:
            subscription = service.get_or_create_subscription(tenant)
            service.update_subscription_cycle(subscription, BillingCycle(form.billing_cycle.data))
        except BillingError as exc:
            flash(str(exc), "danger")
        else:
            flash("Ciclo de cobrança desta assinatura atualizado.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/lojistas/<int:tenant_id>/assinatura/gerar-proxima-fatura", methods=["POST"])
@super_admin_required
def subscription_generate_next_invoice(tenant_id):
    tenant = AdminTenantService().get_or_404(tenant_id)
    service = AdminBillingService()

    try:
        subscription = service.get_or_create_subscription(tenant)
        invoice = service.generate_next_invoice(subscription)
    except BillingError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Próxima fatura gerada: vencimento {invoice.due_date.strftime('%d/%m/%Y')}.", "success")

    return redirect(url_for("admin.tenants_detail", tenant_id=tenant.id))


@admin_bp.route("/faturas/<int:invoice_id>/pagar", methods=["POST"])
@super_admin_required
def invoices_mark_paid(invoice_id):
    service = AdminBillingService()
    invoice = service.get_invoice_or_404(invoice_id)
    tenant_id = invoice.tenant_id

    service.mark_paid(invoice)
    flash("Fatura marcada como paga. Se a loja estava bloqueada por inadimplência, o acesso foi liberado.", "success")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant_id))


@admin_bp.route("/faturas/<int:invoice_id>/vencida", methods=["POST"])
@super_admin_required
def invoices_mark_overdue(invoice_id):
    service = AdminBillingService()
    invoice = service.get_invoice_or_404(invoice_id)
    tenant_id = invoice.tenant_id

    service.mark_overdue(invoice)
    flash("Fatura marcada como vencida.", "warning")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant_id))


@admin_bp.route("/faturas/<int:invoice_id>/cancelar", methods=["POST"])
@super_admin_required
def invoices_cancel(invoice_id):
    service = AdminBillingService()
    invoice = service.get_invoice_or_404(invoice_id)
    tenant_id = invoice.tenant_id

    service.cancel_invoice(invoice)
    flash("Fatura cancelada.", "info")
    return redirect(url_for("admin.tenants_detail", tenant_id=tenant_id))


@admin_bp.route("/faturas/<int:invoice_id>/gerar-cobranca-asaas", methods=["POST"])
@super_admin_required
def invoices_generate_asaas_charge(invoice_id):
    service = AdminBillingService()
    invoice = service.get_invoice_or_404(invoice_id)
    tenant_id = invoice.tenant_id

    try:
        service.generate_asaas_charge(invoice)
    except BillingError as exc:
        flash(str(exc), "danger")
    else:
        flash("Cobrança gerada no Asaas. Link de pagamento disponível na fatura.", "success")

    return redirect(url_for("admin.tenants_detail", tenant_id=tenant_id))
