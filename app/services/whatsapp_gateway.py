"""
Integração com a WhatsApp Cloud API (Meta) para notificar o CLIENTE
final quando um pedido muda de status — ver
app/services/whatsapp_notification_service.py para o ponto que decide
QUANDO/O QUE enviar; este módulo só sabe fazer a chamada HTTP.

IMPORTANTE — leia antes de usar em produção: segue a estrutura pública
documentada da Graph API da Meta (v20.0) a partir do conhecimento
disponível no momento em que foi escrita. Não há uma conta WhatsApp
Business real conectada neste ambiente, então não foi possível testar
contra a API de verdade — valide com um número de teste da Meta antes
de usar em produção, e confira a documentação oficial atual caso a API
tenha mudado.

Toda mensagem enviada por aqui é uma mensagem de TEMPLATE (nunca texto
livre) — é exigência da própria Meta para qualquer mensagem iniciada
pela empresa fora da janela de 24h desde a última mensagem do cliente,
e essa janela nunca existe aqui (o cliente fez o pedido pelo site, não
iniciou conversa nenhuma no WhatsApp). O Super Admin precisa criar e
submeter cada template pra aprovação direto no Meta Business Manager
antes de configurar o nome dele em
PlatformSettings.whatsapp_status_templates — isso é inevitável, não
tem como automatizar essa aprovação por aqui.

Como habilitar: configure o token de acesso e o Phone Number ID em
/admin/configuracoes/whatsapp (Super Admin). Enquanto não configurado,
`get_gateway()` retorna None e o sistema continua funcionando
normalmente, só sem notificar o cliente.
"""

import requests

REQUEST_TIMEOUT_SECONDS = 15
GRAPH_API_VERSION = "v20.0"
DEFAULT_LANGUAGE_CODE = "pt_BR"


class WhatsAppGatewayError(Exception):
    pass


class WhatsAppGateway:
    def __init__(self, access_token: str, phone_number_id: str):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.base_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}"

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method, url, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise WhatsAppGatewayError(
                "Não foi possível conectar ao WhatsApp (Meta). Tente novamente em instantes."
            ) from exc

        if response.status_code >= 400:
            # A Graph API retorna {"error": {"message": "..."}} em erro —
            # extrai uma mensagem legível quando possível, sem vazar o
            # corpo bruto da resposta.
            detail = "Erro desconhecido."
            try:
                detail = response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
            raise WhatsAppGatewayError(f"WhatsApp recusou o envio: {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise WhatsAppGatewayError("Resposta inesperada do WhatsApp (Meta).") from exc

    def send_template_message(
        self, *, to: str, template_name: str, body_params: list[str] | None = None,
        language_code: str = DEFAULT_LANGUAGE_CODE,
    ) -> str:
        """`to`: telefone em E.164 sem o "+" (só dígitos, com DDI) — ex:
        "5567999998888". `body_params`: valores, na ordem, das
        variáveis {{1}}, {{2}}... do corpo do template (o texto do
        template em si é gerenciado pela Meta, não por aqui). Retorna
        o ID da mensagem enviada."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if body_params:
            payload["template"]["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]}
            ]

        data = self._request("POST", "/messages", json=payload)
        messages = data.get("messages") or []
        if not messages:
            raise WhatsAppGatewayError("WhatsApp não retornou confirmação de envio.")
        return messages[0].get("id", "")

    def ping(self) -> None:
        # GET no próprio Phone Number ID — confirma que o token é aceito
        # e o número existe, sem enviar nenhuma mensagem. `_request` já
        # levanta WhatsAppGatewayError em qualquer erro (token inválido,
        # phone_number_id errado, falha de rede etc.).
        self._request("GET", "")


def get_gateway():
    """
    Retorna a instância do gateway configurado, ou None se a integração
    de WhatsApp não estiver habilitada.

    Fonte de verdade: PlatformSettings (configurada pelo Super Admin em
    /admin/configuracoes/whatsapp). Sem token+phone_number_id
    configurados, retorna None — mesmo espírito de
    app.services.payment_gateway.get_gateway.
    """
    from app.models.platform_settings import PlatformSettings

    settings = PlatformSettings.get_or_create()
    if not settings.whatsapp_configured:
        return None
    return WhatsAppGateway(
        access_token=settings.whatsapp_access_token,
        phone_number_id=settings.whatsapp_phone_number_id,
    )
