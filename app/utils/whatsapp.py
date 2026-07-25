"""
Monta a mensagem de texto do pedido e o link do WhatsApp (wa.me).

O envio em si é feito pelo NAVEGADOR do cliente final (abrindo o link
wa.me, que já vem com a mensagem pré-preenchida) — a aplicação não possui
integração com a API oficial do WhatsApp Business nesta fase. Isso é
suficiente para o fluxo "pedido registrado no sistema + enviado por
WhatsApp" pedido no escopo do projeto, sem depender de credenciais de
API externas.
"""

from urllib.parse import quote

from app.models import DeliveryType, PaymentMethod

PAYMENT_LABELS = {
    PaymentMethod.CASH.value: "Dinheiro",
    PaymentMethod.CARD.value: "Cartão",
    PaymentMethod.PIX.value: "Pix",
    PaymentMethod.OTHER.value: "Outro",
}


def build_order_message(order) -> str:
    lines = [f"*Pedido #{order.order_number} — {order.tenant.name}*", ""]

    for item in order.items:
        line = f"{item.quantity}x {item.product_name} — R$ {item.subtotal_cents / 100:.2f}"
        lines.append(line)
        for choice in item.choices:
            extra = f" (+R$ {choice.extra_price_cents * choice.quantity / 100:.2f})" if choice.extra_price_cents else ""
            prefix = f"{choice.quantity}x " if choice.quantity > 1 else ""
            lines.append(f"   • {choice.group_name}: {prefix}{choice.option_name}{extra}")

    lines.append("")
    lines.append(f"Subtotal: R$ {order.subtotal_cents / 100:.2f}")

    if order.delivery_type == DeliveryType.DELIVERY.value:
        lines.append(f"Taxa de entrega: R$ {order.delivery_fee_cents / 100:.2f}")

    lines.append(f"*Total: R$ {order.total_cents / 100:.2f}*")
    lines.append("")

    if order.delivery_type == DeliveryType.DELIVERY.value:
        address = f"{order.address_street}, {order.address_number}"
        if order.address_complement:
            address += f" ({order.address_complement})"
        address += f" — {order.address_neighborhood}"
        if order.address_city:
            address += f", {order.address_city}"
        lines.append("*Entrega*")
        lines.append(address)
        if order.address_reference:
            lines.append(f"Referência: {order.address_reference}")
    else:
        lines.append("*Retirada no local*")

    lines.append("")
    lines.append(f"Pagamento: {PAYMENT_LABELS.get(order.payment_method, order.payment_method)}")
    lines.append(f"Cliente: {order.customer_name} — {order.customer_phone}")

    if order.notes:
        lines.append("")
        lines.append(f"Obs: {order.notes}")

    return "\n".join(lines)


def build_whatsapp_url(tenant, message: str) -> str | None:
    if not tenant.whatsapp_number:
        return None
    return f"https://wa.me/{tenant.whatsapp_number}?text={quote(message)}"
