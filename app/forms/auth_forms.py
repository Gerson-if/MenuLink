from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length

from app.utils.validators import bounded_length


class LoginForm(FlaskForm):
    email = StringField(
        "E-mail",
        validators=[
            DataRequired(message="Informe o e-mail."),
            bounded_length(180, message="E-mail muito longo (máximo 180 caracteres)."),
            Email(message="E-mail inválido."),
        ],
    )
    password = PasswordField(
        "Senha",
        validators=[
            DataRequired(message="Informe a senha."),
            Length(min=6, max=128, message="A senha deve ter entre 6 e 128 caracteres."),
        ],
    )
    remember_me = BooleanField("Manter conectado")
    submit = SubmitField("Entrar")
