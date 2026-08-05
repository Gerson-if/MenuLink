"""
Contrato que qualquer gateway de pagamento precisa implementar para
funcionar com `AdminBillingService`. Hoje só `AsaasGateway` existe, mas
manter isso separado deixa claro o que precisaria ser implementado para
adicionar um segundo gateway (ex: Stripe) sem tocar no resto do sistema.
"""

from abc import ABC, abstractmethod


class PaymentGatewayError(Exception):
    """Erro de comunicação ou resposta inesperada do gateway de pagamento.
    Sempre com mensagem segura para exibir ao Super Admin (nunca vaza
    detalhes internos da API do gateway, como corpo bruto de erro)."""


class PaymentGateway(ABC):
    @abstractmethod
    def ensure_customer(self, tenant) -> str:
        """Garante que o tenant tem um "cliente" cadastrado no gateway,
        criando se necessário. Retorna o ID do cliente no gateway
        (também salvo em `tenant.asaas_customer_id` para reuso)."""

    @abstractmethod
    def create_charge(self, tenant, invoice) -> dict:
        """Cria uma cobrança no gateway para o valor/vencimento da
        Invoice. Retorna um dict com pelo menos `id` (ID da cobrança no
        gateway) e `payment_url` (link de pagamento para enviar ao
        lojista)."""

    @abstractmethod
    def get_charge_status(self, charge_id: str) -> dict:
        """Consulta o status atual de uma cobrança no gateway."""

    @abstractmethod
    def ping(self) -> None:
        """
        Faz uma chamada leve e somente-leitura ao gateway só para
        confirmar que as credenciais são válidas — usado pelo botão
        "Testar conexão" da tela de configurações do Super Admin, para
        detectar uma chave errada/expirada ANTES dela ser usada para
        gerar uma cobrança real. Levanta PaymentGatewayError se a
        credencial for inválida ou a chamada falhar.
        """
