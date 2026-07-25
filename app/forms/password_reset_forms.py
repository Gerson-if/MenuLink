from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo

from app.utils.validators import bounded_length


class ForgotPasswordForm(FlaskForm):
    email = StringField(
        "E-mail cadastrado",
        validators=[
            DataRequired(message="Informe o e-mail."),
            bounded_length(180),
            Email(message="E-mail inválido."),
        ],
    )
    submit = SubmitField("Enviar link de recuperação")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "Nova senha",
        validators=[
            DataRequired(message="Informe a nova senha."),
            Length(min=6, max=128, message="A senha deve ter entre 6 e 128 caracteres."),
        ],
    )
    confirm_password = PasswordField(
        "Confirme a nova senha",
        validators=[
            DataRequired(message="Confirme a nova senha."),
            Length(max=128),
            EqualTo("password", message="As senhas não coincidem."),
        ],
    )
    submit = SubmitField("Redefinir senha")
