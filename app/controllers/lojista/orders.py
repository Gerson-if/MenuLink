from datetime import date, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for

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

# Filtro de período do Histórico. Por padrão mostra só "hoje" — a tela
# tende a acumular meses/anos de pedidos encerrados, então sem um recorte
# padrão ela viraria uma lista enorme e pouco útil; "hoje" cobre o caso de
# uso mais comum (conferir o movimento do dia) e os demais períodos ficam
# a um clique de distância pra quem quiser olhar mais pra trás.
HISTORY_PERIODS = ["hoje", "semana", "mes", "ano", "todos", "personalizado"]
HISTORY_PERIOD_LABELS = {
    "hoje": "Hoje",
    "semana": "Últimos 7 dias",
    "mes": "Este mês",
    "ano": "Este ano",
    "todos": "Todos",
    "personalizado": "Personalizado",
}


def _history_date_range(period: str, date_from_str: str, date_to_str: str):
    """Traduz o período selecionado num par (date_from, date_to) pra
    OrderRepository.paginated — date_to já vem como "dia seguinte" (veja o
    docstring de paginated) pra incluir o dia final inteiro sem depender
    de hora."""
    today = date.today()
    tomorrow = today + timedelta(days=1)

    if period == "semana":
        return today - timedelta(days=6), tomorrow
    if period == "mes":
        return today.replace(day=1), tomorrow
    if period == "ano":
        return today.replace(month=1, day=1), tomorrow
    if period == "todos":
        return None, None
    if period == "personalizado":
        try:
            start = date.fromisoformat(date_from_str) if date_from_str else today
        except ValueError:
            start = today
        try:
            end = date.fromisoformat(date_to_str) if date_to_str else today
        except ValueError:
            end = today
        if end < start:
            start, end = end, start
        return start, end + timedelta(days=1)
    # "hoje" (padrão)
    return today, tomorrow


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


@lojista_bp.route("/pedidos/novos/contagem")
@lojista_required
def orders_pending_count():
    """Consultada via polling JS em toda página do painel (ver
    layouts/lojista_panel.html) pra tocar um som e atualizar o badge da
    sidebar assim que um pedido novo chega — sem precisar HTMX/recarregar
    a tela inteira a cada poll, já que isso roda em TODA página, não só
    em /pedidos. Só a contagem, de propósito: o JS decide se toca som
    comparando com o valor anterior, então o servidor não precisa saber
    "o que já foi visto"."""
    tenant = get_current_tenant()
    service = OrderService(tenant)
    count = service.order_repo.count_by_status("pending")
    return jsonify({"count": count})


@lojista_bp.route("/pedidos/historico")
@lojista_required
def orders_history():
    tenant = get_current_tenant()
    status = request.args.get("status", "").strip()
    if status and status not in HISTORY_STATUSES:
        status = ""

    period = request.args.get("period", "hoje").strip()
    if period not in HISTORY_PERIODS:
        period = "hoje"
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    date_from, date_to = _history_date_range(period, date_from_str, date_to_str)

    page = request.args.get("page", 1, type=int)

    service = OrderService(tenant)
    pagination = service.order_repo.paginated(
        page=page, per_page=15, status=status or None, statuses=None if status else HISTORY_STATUSES,
        date_from=date_from, date_to=date_to,
    )

    # Datas "de exibição" (formato ISO, prontas pro atributo value dos
    # <input type="date">): date_to volta um dia pra virar inclusivo de
    # novo, já que foi guardado como exclusivo pra query.
    display_from = date_from.isoformat() if date_from else ""
    display_to = (date_to - timedelta(days=1)).isoformat() if date_to else ""

    return render_template(
        "lojista/orders/history.html",
        orders=pagination.items,
        pagination=pagination,
        selected_status=status,
        status_labels=STATUS_LABELS,
        all_statuses=HISTORY_STATUSES,
        selected_period=period,
        period_labels=HISTORY_PERIOD_LABELS,
        all_periods=HISTORY_PERIODS,
        display_from=display_from,
        display_to=display_to,
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
    order = service.get_locked_or_404(order_id)

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
    order = service.get_locked_or_404(order_id)

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
    order = service.get_locked_or_404(order_id)

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
    order = service.get_locked_or_404(order_id)

    new_status = request.form.get("new_status", "")
    try:
        service.update_status(order, new_status)
    except OrderValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Pedido #{order.order_number} atualizado para '{STATUS_LABELS.get(new_status, new_status)}'.", "success")

    return redirect(url_for("lojista.orders_detail", order_id=order.id))
