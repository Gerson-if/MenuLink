"""
Envio de e-mail via SMTP, usando as credenciais configuradas pelo Super
Admin em Integrações > E-mail (ver app/models/platform_settings.py e
app/controllers/admin/settings.py:settings_email).

Usado hoje por app/utils/mailer.py (recuperação de senha) — pensado
também para cobrança de faturas e outras notificações no futuro, todas
passando por este mesmo ponto único de envio.

Sem SMTP configurado, `EmailService.is_configured` é False e quem chama
decide o que fazer (o mailer.py, por exemplo, cai de volta pro
comportamento antigo de só logar o link em vez de enviar de verdade —
assim o ambiente de desenvolvimento continua funcionando sem SMTP).
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.models.platform_settings import PlatformSettings

SEND_TIMEOUT_SECONDS = 10


class EmailError(Exception):
    """Erro ao enviar e-mail — mensagem já pronta pra mostrar pro Super Admin."""


class EmailService:
    def __init__(self, settings: PlatformSettings | None = None):
        self.settings = settings or PlatformSettings.get_or_create()

    @property
    def is_configured(self) -> bool:
        return self.settings.email_configured

    def send(self, *, to: str, subject: str, body_text: str, body_html: str | None = None) -> None:
        """Envia um e-mail agora. Levanta EmailError com uma mensagem
        legível (sem vazar detalhes internos do smtplib) se o SMTP não
        estiver configurado ou a conexão/autenticação falhar."""
        if not self.is_configured:
            raise EmailError(
                "Nenhum servidor de e-mail configurado ainda. Configure em "
                "Integrações > E-mail antes de tentar enviar."
            )

        from_name = self.settings.smtp_from_name or "Comanda"
        from_email = self.settings.smtp_from_email

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_name} <{from_email}>"
        message["To"] = to
        message.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            message.attach(MIMEText(body_html, "html", "utf-8"))

        host = self.settings.smtp_host
        port = self.settings.smtp_port or 587
        username = self.settings.smtp_username
        password = self.settings.smtp_password
        use_ssl = self.settings.smtp_encryption == "ssl"

        try:
            if use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, timeout=SEND_TIMEOUT_SECONDS, context=context) as server:
                    self._authenticate_and_send(server, username, password, from_email, to, message)
            else:
                with smtplib.SMTP(host, port, timeout=SEND_TIMEOUT_SECONDS) as server:
                    server.ehlo()
                    if self.settings.smtp_encryption == "tls":
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    self._authenticate_and_send(server, username, password, from_email, to, message)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(
                "O servidor recusou o usuário/senha configurados (autenticação SMTP falhou)."
            ) from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
            raise EmailError(
                f"Não consegui conectar ao servidor SMTP ({host}:{port}) — confira o endereço, a "
                f"porta e se o firewall do servidor permite saída nessa porta. Detalhe: {exc}"
            ) from exc
        except smtplib.SMTPException as exc:
            raise EmailError(f"O servidor de e-mail recusou o envio: {exc}") from exc

    @staticmethod
    def _authenticate_and_send(server, username, password, from_email, to, message):
        if username:
            server.login(username, password or "")
        server.sendmail(from_email, [to], message.as_string())
