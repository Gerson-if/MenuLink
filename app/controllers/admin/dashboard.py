from flask import render_template

from app.models import Tenant, TenantStatus
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    total_tenants = Tenant.query.count()
    # Precisa bater exatamente com o filtro pra onde o card "Contas
    # ativas" leva (tenants_list?status=active, que filtra só
    # TenantStatus.ACTIVE — mesmo status chamado de "Ativa" no dropdown
    # de app/templates/admin/tenants/list.html). Antes este número
    # somava TRIAL + ACTIVE, então o card prometia um total maior do
    # que a lista filtrada realmente mostrava — lojas em teste entravam
    # na contagem mas "sumiam" ao clicar, parecendo que o filtro estava
    # quebrado.
    active_tenants = Tenant.query.filter_by(status=TenantStatus.ACTIVE).count()
    blocked_tenants = Tenant.query.filter_by(status=TenantStatus.BLOCKED_PAYMENT).count()

    return render_template(
        "admin/dashboard.html",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        blocked_tenants=blocked_tenants,
    )
