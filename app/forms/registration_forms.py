from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp

from app.utils.validators import bounded_length, not_blank, password_max_bytes, phone_number


class RegistrationForm(FlaskForm):
    owner_name = StringField(
        "Seu nome", validators=[DataRequired(message="Informe seu nome."), Length(max=150), not_blank]
    )
    whatsapp_number = StringField(
        "WhatsApp corporativo",
        validators=[DataRequired(message="Informe o WhatsApp."), Length(max=20), phone_number()],
    )
    store_name = StringField(
        "Nome da Loja / Restaurante",
        validators=[DataRequired(message="Informe o nome da loja."), Length(max=150), not_blank],
    )
    slug = StringField(
        "Endereço do seu cardápio",
        validators=[
            DataRequired(message="Escolha o endereço do seu cardápio."),
            Length(min=3, max=150),
            Regexp(r"^[a-z0-9-]+$", message="Use apenas letras minúsculas, números e hífens."),
        ],
    )
    email = StringField(
        "Seu melhor e-mail",
        validators=[
            DataRequired(message="Informe o e-mail."),
            bounded_length(180),
            Email(message="E-mail inválido."),
        ],
    )
    password = PasswordField(
        "Criar senha de acesso",
        validators=[
            DataRequired(message="Informe uma senha."),
            password_max_bytes(),
            Length(min=6, max=128, message="A senha deve ter entre 6 e 128 caracteres."),
        ],
    )
    password_confirm = PasswordField(
        "Confirmar senha",
        validators=[
            DataRequired(message="Confirme a senha."),
            EqualTo("password", message="As senhas não coincidem."),
        ],
    )
    # Texto curto DE PROPÓSITO: no cadastro em passos (auth/register.html),
    # este botão divide a mesma linha com o "Voltar" (`.reg-step-nav`,
    # dois botões lado a lado) — o texto mais longo de antes ("Criar Meu
    # Painel Administrativo") quebrava em várias linhas e chegava a
    # estourar a largura do botão em telas de celular estreitas.
    submit = SubmitField("Criar minha loja")
