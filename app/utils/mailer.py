"""
Envio do e-mail de recuperação de senha.

Antes deste módulo ganhar um serviço de e-mail de verdade
(app/services/email_service.py, configurável em Integrações > E-mail no
painel do Super Admin), o link de redefinição só era registrado no log
da aplicação — nenhum e-mail era enviado de verdade, mesmo em produção.

Agora: se o SMTP estiver configurado, o e-mail é enviado de verdade. Se
não estiver (ambiente de desenvolvimento, ou produção que ainda não
configurou a integração), continua caindo no comportamento antigo — só
loga o link — para não quebrar o fluxo de "esqueci minha senha" em quem
ainda não configurou nada.
"""

from flask import current_app, url_for

from app.services.email_service import EmailError, EmailService


def send_password_reset_email(user, token: str) -> str:
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    email_service = EmailService()
    if email_service.is_configured:
        try:
            email_service.send(
                to=user.email,
                subject="Redefinição de senha — Comanda",
                body_text=(
                    f"Olá, {user.name}.\n\n"
                    "Recebemos um pedido para redefinir sua senha no Comanda. "
                    f"Clique no link abaixo para escolher uma nova senha (válido por 1 hora):\n\n"
                    f"{reset_url}\n\n"
                    "Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma."
                ),
                body_html=(
                    f"<p>Olá, {user.name}.</p>"
                    "<p>Recebemos um pedido para redefinir sua senha no Comanda. "
                    "Clique no botão abaixo para escolher uma nova senha (válido por 1 hora):</p>"
                    f'<p><a href="{reset_url}" style="background:#E54A36;color:#fff;padding:10px 20px;'
                    'border-radius:6px;text-decoration:none;display:inline-block;">Redefinir senha</a></p>'
                    f'<p style="color:#888;font-size:0.85em;">Ou copie e cole este link: {reset_url}</p>'
                    "<p>Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma.</p>"
                ),
            )
        except EmailError as exc:
            # Não deixamos o fluxo de "esqueci minha senha" quebrar pro
            # usuário final por causa de um SMTP mal configurado — ainda
            # registramos o link no log (o Super Admin consegue ajudar
            # manualmente) e também o motivo da falha, pra facilitar o
            # diagnóstico em Integrações > E-mail.
            current_app.logger.warning(
                "[recuperação de senha] Falha ao enviar e-mail para %s (%s) — link: %s",
                user.email, exc, reset_url,
            )
    else:
        current_app.logger.info(
            "[recuperação de senha] Link para %s: %s (válido por 1 hora)", user.email, reset_url
        )

    return reset_url
