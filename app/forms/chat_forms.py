from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import SubmitField, TextAreaField
from wtforms.validators import Length, Optional

from app.services.chat_service import MAX_MESSAGE_LENGTH


class ChatMessageForm(FlaskForm):
    # `body` não tem mais `DataRequired`: com anexo, uma mensagem pode ser
    # só-imagem/só-PDF, sem texto nenhum — a regra "precisa ter PELO MENOS
    # um dos dois (texto OU anexo)" é validada na camada de serviço (ver
    # ChatService.send_message), não aqui no form, porque ela depende dos
    # dois campos juntos.
    body = TextAreaField("Mensagem", validators=[Optional(), Length(max=MAX_MESSAGE_LENGTH)])
    attachment = FileField(
        "Anexo",
        validators=[FileAllowed(["png", "jpg", "jpeg", "webp", "heic", "heif", "pdf"], message="Formato não suportado. Envie uma imagem (JPG, PNG, WEBP) ou um PDF.")],
    )
    submit = SubmitField("Enviar")
