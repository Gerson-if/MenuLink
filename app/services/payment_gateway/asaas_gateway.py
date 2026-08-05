"""
Integração com a API do Asaas (https://docs.asaas.com) para cobrança das
faturas dos lojistas.

IMPORTANTE — leia antes de usar em produção: esta implementação segue a
estrutura pública documentada da API v3 do Asaas (endpoints, payload e
nomes de campo) a partir do conhecimento disponível no momento em que
foi escrita. Como não há uma conta Asaas real conectada neste ambiente,
**não foi possível testar contra a API de verdade** — antes de usar em
produção, valide contra o ambiente sandbox do Asaas
(https://sandbox.asaas.com) com uma chave de API de teste e confira a
documentação oficial atual, caso a API tenha mudado.

Como habilitar: defina as variáveis de ambiente `ASAAS_API_KEY` (chave
gerada no painel do Asaas, em Configurações → Integrações → API) e,
opcionalmente, `ASAAS_ENVIRONMENT=production` (o padrão é `sandbox`,
ambiente de testes do próprio Asaas). Enquanto `ASAAS_API_KEY` não
estiver definida, `get_gateway()` retorna `None` e o sistema continua
funcionando normalmente com lançamento manual de fatura.
"""

import requests

from app.services.payment_gateway.base import PaymentGateway, PaymentGatewayError
from app.utils.validators import only_digits

REQUEST_TIMEOUT_SECONDS = 15


_VALID_BILLING_TYPES = {"PIX", "UNDEFINED", "BOLETO", "CREDIT_CARD"}


class AsaasGateway(PaymentGateway):
    def __init__(self, api_key: str, environment: str = "sandbox", billing_type: str = "PIX"):
        self.api_key = api_key
        self.base_url = (
            "https://api.asaas.com/v3"
            if environment == "production"
            else "https://sandbox.asaas.com/api/v3"
        )
        # "PIX" é o padrão e a recomendação do Menu Link (liberação via
        # webhook quase em tempo real, ver create_charge/módulo
        # webhooks_controller.py) — configurável pelo Super Admin em
        # /admin/configuracoes/asaas (PlatformSettings.asaas_billing_type),
        # deixado pronto para o dia em que boleto/cartão forem habilitados
        # sem precisar mexer em código. Um valor desconhecido cai de
        # volta pro padrão seguro em vez de quebrar a geração de cobrança.
        self.billing_type = billing_type if billing_type in _VALID_BILLING_TYPES else "PIX"

    @property
    def _headers(self) -> dict:
        return {
            "access_token": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "MenuLinkSaaS/1.0",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method, url, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(
                "Não foi possível conectar ao Asaas. Tente novamente em instantes."
            ) from exc

        if response.status_code >= 400:
            # O Asaas retorna {"errors": [{"description": "..."}]} em erros
            # de validação — extrai uma mensagem legível quando possível,
            # sem vazar o corpo bruto da resposta.
            detail = "Erro desconhecido."
            try:
                errors = response.json().get("errors", [])
                if errors:
                    detail = errors[0].get("description", detail)
            except ValueError:
                pass
            raise PaymentGatewayError(f"Asaas recusou a requisição: {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise PaymentGatewayError("Resposta inesperada do Asaas.") from exc

    def ensure_customer(self, tenant) -> str:
        if tenant.asaas_customer_id:
            return tenant.asaas_customer_id

        payload = {
            "name": tenant.name,
            "email": tenant.email,
            "externalReference": str(tenant.id),
        }
        if tenant.whatsapp_number:
            payload["mobilePhone"] = tenant.whatsapp_number
        if tenant.phone:
            payload["phone"] = tenant.phone
        # O Asaas EXIGE cpfCnpj pra criar um cliente de cobrança de
        # verdade — sem isso, a API real recusa a requisição (ver
        # validação equivalente em AdminBillingService.generate_asaas_charge,
        # que barra a geração da cobrança antes de chegar aqui se o
        # tenant não tiver documento cadastrado). Só números, sem
        # pontuação (ver TenantSettingsService.update_store_info, que já
        # salva `document` só com dígitos).
        if tenant.document:
            payload["cpfCnpj"] = only_digits(tenant.document)

        data = self._request("POST", "/customers", json=payload)
        customer_id = data.get("id")
        if not customer_id:
            raise PaymentGatewayError("Asaas não retornou um ID de cliente válido.")

        tenant.asaas_customer_id = customer_id
        return customer_id

    def create_charge(self, tenant, invoice) -> dict:
        customer_id = self.ensure_customer(tenant)

        payload = {
            "customer": customer_id,
            # Ver comentário em __init__ — "PIX" por padrão (liberação em
            # tempo real via webhook), configurável pelo Super Admin.
            "billingType": self.billing_type,
            "value": round(invoice.amount_cents / 100, 2),
            "dueDate": invoice.due_date.isoformat(),
            "description": f"Fatura #{invoice.id} — {tenant.name}",
            "externalReference": str(invoice.id),
        }

        data = self._request("POST", "/payments", json=payload)
        charge_id = data.get("id")
        if not charge_id:
            raise PaymentGatewayError("Asaas não retornou um ID de cobrança válido.")

        return {
            "id": charge_id,
            "payment_url": data.get("invoiceUrl"),
            "status": data.get("status"),
        }

    def get_charge_status(self, charge_id: str) -> dict:
        data = self._request("GET", f"/payments/{charge_id}")
        return {"id": data.get("id"), "status": data.get("status")}

    def get_pix_qr_code(self, charge_id: str) -> dict:
        # GET /payments/{id}/pixQrCode (ver docs.asaas.com/reference/
        # obter-qr-code-para-pagamentos-via-pix) — só funciona para uma
        # cobrança com billingType PIX (ou UNDEFINED, que aceita Pix
        # entre outras formas); para BOLETO/CREDIT_CARD o Asaas recusa,
        # e o chamador deve usar o payment_url (checkout hospedado) em
        # vez desta tela embutida.
        data = self._request("GET", f"/payments/{charge_id}/pixQrCode")
        return {
            "encoded_image": data.get("encodedImage"),
            "payload": data.get("payload"),
            "expiration_date": data.get("expirationDate"),
        }

    def ping(self) -> None:
        # /myAccount é um endpoint leve de leitura só pra confirmar que
        # a chave de API é aceita — não cria nem altera nada no Asaas.
        # `_request` já levanta PaymentGatewayError em qualquer erro
        # (chave inválida, ambiente errado, falha de rede, etc.).
        self._request("GET", "/myAccount")
