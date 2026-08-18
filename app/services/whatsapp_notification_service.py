"""
Decide QUANDO e O QUE mandar por WhatsApp quando um pedido muda de
status — a chamada HTTP em si fica em app/services/whatsapp_gateway.py.

Chamado por OrderService.update_status (único ponto por onde toda
mudança de status do painel do lojista passa, seja aceitar pedido,
marcar "saiu para entrega" etc. — ver app/controllers/lojista/orders.py).

Uma falha de envio NUNCA deve impedir/desfazer a mudança de status em
si (é só uma notificação, não faz parte do fluxo crítico do pedido) —
por isso todo erro é silenciosamente engolido aqui dentro, nunca
propagado pro chamador.
"""

import logging

from flask import current_app

from app.services.whatsapp_gateway import WhatsAppGatewayError, get_gateway
from app.utils.validators import only_digits

logger = logging.getLogger(__name__)

# Só esses status geram aviso pro cliente — "pending" é o estado inicial
# (nada mudou ainda do ponto de vista dele) e não está aqui. Um status
# sem template configurado pelo Super Admin (ver
# PlatformSettings.whatsapp_status_templates) simplesmente não dispara
# nada, mesmo estando nesta lista.
NOTIFIABLE_STATUSES = (
    "confirmed", "preparing", "out_for_delivery", "ready_for_pickup", "completed", "canceled",
)


def _to_whatsapp_phone(raw_phone: str | None) -> str | None:
    """Converte o telefone salvo no pedido — formatado, ex:
    "(67) 99999-9998" (ver app/utils/phone.py:normalize_br_phone,
    aplicado na criação do pedido) — para o formato E.164 sem "+" que a
    Cloud API espera (só dígitos, com DDI): "5567999998888". Retorna
    None se não der pra montar um número plausível."""
    digits = only_digits(raw_phone or "")
    if not digits:
        return None
    country_code = current_app.config.get("WHATSAPP_DEFAULT_COUNTRY_CODE", "55")
    # Números já salvos com DDI (menos comum, mas possível se o cliente
    # digitou o próprio DDI no checkout) não devem levar o prefixo de
    # novo — heurística simples: um número BR válido (DDD+telefone) tem
    # 10 ou 11 dígitos; qualquer coisa mais longa já deve ter DDI.
    if len(digits) > 11:
        return digits
    return f"{country_code}{digits}"


def notify_order_status_change(order, new_status: str) -> None:
    tenant = order.tenant
    if new_status not in NOTIFIABLE_STATUSES:
        return
    if not (tenant.plan and tenant.plan.allow_whatsapp_notifications):
        return

    from app.models.platform_settings import PlatformSettings

    settings = PlatformSettings.get_or_create()
    template_name = settings.whatsapp_status_templates_or_default.get(new_status)
    if not template_name:
        return

    to_phone = _to_whatsapp_phone(order.customer_phone)
    if not to_phone:
        return

    gateway = get_gateway()
    if gateway is None:
        return

    try:
        gateway.send_template_message(to=to_phone, template_name=template_name, body_params=[tenant.name])
    except WhatsAppGatewayError:
        logger.warning(
            "Falha ao notificar cliente por WhatsApp (pedido #%s, tenant %s, status %s)",
            order.order_number, tenant.id, new_status, exc_info=True,
        )
