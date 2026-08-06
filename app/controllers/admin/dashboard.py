from flask import render_template

from app.models import Tenant, TenantStatus
from app.services.admin_metrics_service import AdminMetricsService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    # Contagens simples direto do model (não pelo service) só pra manter
    # o comentário histórico de precisar bater exatamente com o filtro
    # pra onde cada card leva (tenants_list?status=active/blocked_payment).
    total_tenants = Tenant.query.count()
    active_tenants = Tenant.query.filter_by(status=TenantStatus.ACTIVE).count()
    blocked_tenants = Tenant.query.filter_by(status=TenantStatus.BLOCKED_PAYMENT).count()

    metrics = AdminMetricsService()
    kpis = metrics.tenant_kpis()

    return render_template(
        "admin/dashboard.html",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        blocked_tenants=blocked_tenants,
        new_this_month=kpis["new_this_month"],
        mrr_cents=metrics.mrr_cents(),
        open_invoices_cents=metrics.open_invoices_cents(),
        tenant_growth_series=metrics.tenant_growth_series(),
        revenue_series=metrics.revenue_series(),
        tenants_by_status=metrics.tenants_by_status(),
        tenants_by_plan=metrics.tenants_by_plan(),
    )
