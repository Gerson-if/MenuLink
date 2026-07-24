from flask import flash, redirect, render_template, request, url_for

from app.services.customer_service import CustomerService
from app.utils.decorators import lojista_required
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp

SORT_OPTIONS = ("recent", "spent", "orders")


@lojista_bp.route("/clientes")
@lojista_required
def customers_list():
    tenant = get_current_tenant()
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "recent")
    if sort not in SORT_OPTIONS:
        sort = "recent"

    service = CustomerService(tenant)
    pagination = service.paginated(page=page, sort=sort)

    return render_template("lojista/customers/list.html", pagination=pagination, selected_sort=sort)


@lojista_bp.route("/clientes/<int:customer_id>/remover", methods=["POST"])
@lojista_required
def customers_delete(customer_id):
    tenant = get_current_tenant()
    service = CustomerService(tenant)
    customer = service.get_or_404(customer_id)

    name = customer.name
    service.delete(customer)
    flash(f"Cliente '{name}' removido. Os pedidos já feitos continuam no seu histórico.", "info")
    return redirect(url_for("lojista.customers_list"))
