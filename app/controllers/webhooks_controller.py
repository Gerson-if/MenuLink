"""
Webhooks de serviços externos.

O Asaas notifica eventos de pagamento (confirmado, recebido, vencido,
estornado...) via POST para uma URL configurada manualmente no painel
do Asaas (Configurações → Integrações → Webhooks). Configure lá:

    URL:   https://SEU-DOMINIO/webhooks/asaas
    Token: o mesmo valor configurado em /admin/configuracoes/asaas
           (ou ASAAS_WEBHOOK_TOKEN, se ainda não migrou para a tela)

O Asaas reenvia o token configurado no cabeçalho `asaas-access-token`
em toda chamada de webhook — validamos isso antes de processar
qualquer coisa, para que não seja possível forjar uma notificação de
"pagamento confirmado" e liberar uma loja bloqueada de graça.
"""

import hmac

from flask import Blueprint, current_app, jsonify, request

from app.extensions import csrf
from app.models.platform_settings import PlatformSettings
from app.services.admin_billing_service import AdminBillingService

webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

# Eventos do Asaas que indicam que o dinheiro entrou de fato — outros
# eventos (ex: PAYMENT_CREATED, PAYMENT_UPDATED, PAYMENT_DELETED) são
# reconhecidos (200 OK) mas não disparam nenhuma ação.
#
# PAYMENT_RECEIVED é o que importa para Pix (forma de cobrança padrão
# da Comanda, ver PlatformSettings.asaas_billing_type/AsaasGateway) — o
# Asaas dispara esse evento assim que o Pix cai na conta, em tempo
# real, sem esperar nenhuma compensação. PAYMENT_CONFIRMED é o
# equivalente para boleto/cartão (quando/se habilitados no futuro).
# Tratamos os dois do mesmo jeito porque ambos significam "dinheiro
# confirmado", mas o caminho comum em produção com Pix é sempre o
# primeiro.
_PAID_EVENTS = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}


@webhooks_bp.route("/asaas", methods=["POST"])
@csrf.exempt  # webhook externo não tem sessão/CSRF do navegador — autenticado pelo token abaixo
def asaas_webhook():
    settings = PlatformSettings.get_or_create()
    expected_token = settings.asaas_webhook_token or current_app.config.get("ASAAS_WEBHOOK_TOKEN")

    if not expected_token:
        # Sem token configurado, recusamos processar qualquer webhook —
        # não dá pra saber se a chamada é legítima. O Super Admin precisa
        # configurar o token em /admin/configuracoes/asaas antes de
        # cadastrar a URL no painel do Asaas.
        current_app.logger.warning("Webhook do Asaas recebido, mas o token de webhook não está configurado.")
        return jsonify({"error": "not configured"}), 503

    received_token = request.headers.get("asaas-access-token", "")
    if not hmac.compare_digest(received_token, expected_token):
        current_app.logger.warning("Webhook do Asaas recebido com token inválido.")
        return jsonify({"error": "invalid token"}), 401

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    payment = payload.get("payment") or {}
    payment_id = payment.get("id")

    if event in _PAID_EVENTS and payment_id:
        invoice = AdminBillingService().mark_paid_by_asaas_payment_id(payment_id)
        if invoice:
            current_app.logger.info(
                "Fatura #%s marcada como paga via webhook Asaas (payment_id=%s).",
                invoice.id, payment_id,
            )

    # O Asaas espera um 200 rápido — qualquer coisa diferente disso faz
    # ele reenviar o webhook várias vezes.
    return jsonify({"status": "ok"}), 200
