"""
Camada de integração com gateway de pagamento (cobrança das faturas dos
lojistas pelo Super Admin).

Hoje só existe um gateway implementado (Asaas — asaas.com, popular no
Brasil para cobrança de SaaS via boleto/Pix/cartão), mas a estrutura em
`base.py` deixa explícito o contrato que qualquer outro gateway
precisaria implementar, caso o projeto queira adicionar um segundo no
futuro (Stripe, Pagar.me, etc.) sem reescrever `AdminBillingService`.

Uso:
    from app.services.payment_gateway import get_gateway

    gateway = get_gateway()
    if gateway is None:
        # ASAAS_API_KEY não configurada — integração desligada,
        # cobrança continua manual.
        ...
    else:
        gateway.ensure_customer(tenant)
        charge = gateway.create_charge(tenant, invoice)
"""

from flask import current_app

from app.services.payment_gateway.asaas_gateway import AsaasGateway


def get_gateway():
    """
    Retorna a instância do gateway configurado, ou None se nenhuma
    integração de pagamento estiver habilitada.

    Fonte de verdade: a tela de configurações do Super Admin
    (PlatformSettings, tabela `platform_settings`). Se o Super Admin
    ainda não configurou nada por lá, cai de volta para as variáveis de
    ambiente ASAAS_API_KEY/ASAAS_ENVIRONMENT (compatibilidade com
    instalações que só usam .env) — isso permite migrar de env vars
    para a tela sem quebrar quem já tinha configurado do jeito antigo.
    """
    from app.models.platform_settings import PlatformSettings

    settings = PlatformSettings.get_or_create()
    api_key = settings.asaas_api_key or current_app.config.get("ASAAS_API_KEY")
    if not api_key:
        return None

    environment = settings.asaas_environment or current_app.config.get("ASAAS_ENVIRONMENT", "sandbox")
    billing_type = settings.asaas_billing_type or "PIX"
    return AsaasGateway(api_key=api_key, environment=environment, billing_type=billing_type)
