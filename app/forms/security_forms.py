"""
Formulários da seção "Segurança" — comuns ao Super Admin e ao lojista
(troca de senha e edição do próprio perfil de login). Ver
app/controllers/lojista/security.py e app/controllers/admin/security.py.
"""

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from app.utils.validators import bounded_length, not_blank


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Senha atual", validators=[DataRequired(message="Informe a senha atual.")])
    new_password = PasswordField(
        "Nova senha",
        validators=[DataRequired(message="Informe a nova senha."), Length(min=6, max=128, message="A senha deve ter entre 6 e 128 caracteres.")],
    )
    confirm_password = PasswordField(
        "Confirme a nova senha",
        validators=[DataRequired(message="Confirme a nova senha."), EqualTo("new_password", message="As senhas não coincidem.")],
    )
    submit = SubmitField("Trocar senha")

    def validate_new_password(self, field):
        if field.data and self.current_password.data and field.data == self.current_password.data:
            raise ValidationError("A nova senha precisa ser diferente da atual.")


class MyProfileForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(message="Informe seu nome."), Length(max=150), not_blank])
    email = StringField(
        "E-mail de login",
        validators=[DataRequired(message="Informe o e-mail."), bounded_length(180), Email(message="E-mail inválido.")],
    )
    submit = SubmitField("Salvar dados")
