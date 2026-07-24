from flask import flash, redirect, render_template, request, url_for

from app.services.order_service import OrderService, OrderValidationError
from app.utils.decorators import lojista_required
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp

STATUS_LABELS = {
    "pending": "Recebido",
    "confirmed": "Confirmado",
    "preparing": "Em preparo",
    "out_for_delivery": "Saiu para entrega",
    "ready_for_pickup": "Pronto para retirada",
    "completed": "Concluído",
    "canceled": "Cancelado",
}

# Pedidos "em andamento" (ainda exigem atenção do lojista) vs. pedidos
# encerrados (concluídos ou cancelados). A tela de Pedidos mostra só os
# ativos por padrão — assim que um pedido é concluído ou cancelado, ele
# "sai" dali e passa a viver só no Histórico, mantendo a área de controle
# de pedidos enxuta e focada no que ainda precisa de ação.
ACTIVE_STATUSES = ["pending", "confirmed", "preparing", "out_for_delivery", "ready_for_pickup"]
HISTORY_STATUSES = ["completed", "canceled"]


@lojista_bp.route("/pedidos")
@lojista_required
def orders_list():
    tenant = get_current_tenant()
    status = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    service = OrderService(tenant)
    pagination = service.order_repo.paginated(
        page=page, per_page=15, status=status or None, statuses=None if status else ACTIVE_STATUSES
    )

    return render_template(
        "lojista/orders/list.html",
        orders=pagination.items,
        pagination=pagination,
        selected_status=status,
        status_labels=STATUS_LABELS,
        all_statuses=ACTIVE_STATUSES,
    )


@lojista_bp.route("/pedidos/historico")
@lojista_required
def orders_history():
    tenant = get_current_tenant()
    status = request.args.get("status", "").strip()
    if status and status not in HISTORY_STATUSES:
        status = ""
    page = request.args.get("page", 1, type=int)

    service = OrderService(tenant)
    pagination = service.order_repo.paginated(
        page=page, per_page=15, status=status or None, statuses=None if status else HISTORY_STATUSES
    )

    return render_template(
        "lojista/orders/history.html",
        orders=pagination.items,
        pagination=pagination,
        selected_status=status,
        status_labels=STATUS_LABELS,
        all_statuses=HISTORY_STATUSES,
    )


@lojista_bp.route("/pedidos/<int:order_id>")
@lojista_required
def orders_detail(order_id):
    tenant = get_current_tenant()
    service = OrderService(tenant)
    order = service.get_or_404(order_id)

    return render_template(
        "lojista/orders/detail.html",
        order=order,
        status_labels=STATUS_LABELS,
        available_transitions=service.available_transitions(order),
        can_revert=service.can_revert(order),
    )


@lojista_bp.route("/pedidos/<int:order_id>/voltar", methods=["POST"])
@lojista_required
def orders_revert_status(order_id):
    tenant = get_current_tenant()
    service = OrderService(tenant)
    order = service.get_or_404(order_id)

    try:
        service.revert_status(order)
    except OrderValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Pedido #{order.order_number} voltou para '{STATUS_LABELS.get(order.status.value)}'.", "info")

    return redirect(url_for("lojista.orders_detail", order_id=order.id))





@lojista_bp.route("/pedidos/<int:order_id>/aceitar", methods=["POST"])
@lojista_required
def orders_accept(order_id):
    tenant = get_current_tenant()
    service = OrderService(tenant)
    order = service.get_or_404(order_id)

    try:
        service.update_status(order, "confirmed")
    except OrderValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Pedido #{order.order_number} aceito.", "success")

    return redirect(request.referrer or url_for("lojista.orders_list", status="pending"))


@lojista_bp.route("/pedidos/<int:order_id>/rejeitar", methods=["POST"])
@lojista_required
def orders_reject(order_id):
    tenant = get_current_tenant()
    service = OrderService(tenant)
    order = service.get_or_404(order_id)

    try:
        service.update_status(order, "canceled")
    except OrderValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Pedido #{order.order_number} rejeitado.", "info")

    return redirect(request.referrer or url_for("lojista.orders_list", status="pending"))


@lojista_bp.route("/pedidos/<int:order_id>/status", methods=["POST"])
@lojista_required
def orders_update_status(order_id):
    tenant = get_current_tenant()
    service = OrderService(tenant)
    order = service.get_or_404(order_id)

    new_status = request.form.get("new_status", "")
    try:
        service.update_status(order, new_status)
    except OrderValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Pedido #{order.order_number} atualizado para '{STATUS_LABELS.get(new_status, new_status)}'.", "success")

    return redirect(url_for("lojista.orders_detail", order_id=order.id))
