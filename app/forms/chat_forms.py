from flask_wtf import FlaskForm
from wtforms import SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length

from app.services.chat_service import MAX_MESSAGE_LENGTH


class ChatMessageForm(FlaskForm):
    body = TextAreaField(
        "Mensagem",
        validators=[DataRequired(message="Escreva uma mensagem antes de enviar."), Length(max=MAX_MESSAGE_LENGTH)],
    )
    submit = SubmitField("Enviar")
