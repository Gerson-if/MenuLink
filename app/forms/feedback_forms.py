from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.services.feedback_service import MAX_EMAIL_LENGTH, MAX_MESSAGE_LENGTH, MAX_NAME_LENGTH

FEEDBACK_KIND_CHOICES = [
    ("suggestion", "Sugestão de melhoria"),
    ("bug_report", "Reportar um problema"),
]


class PublicFeedbackForm(FlaskForm):
    """Usado na landing page — visitante sem conta, então pede nome/e-mail
    (opcionais) para o Super Admin poder responder se quiser."""

    kind = SelectField("O que você quer nos dizer?", choices=FEEDBACK_KIND_CHOICES, validators=[DataRequired()])
    name = StringField("Seu nome (opcional)", validators=[Optional(), Length(max=MAX_NAME_LENGTH)])
    email = StringField(
        "Seu e-mail (opcional, caso queira resposta)",
        validators=[Optional(), Email(message="E-mail inválido."), Length(max=MAX_EMAIL_LENGTH)],
    )
    message = TextAreaField(
        "Mensagem",
        validators=[DataRequired(message="Escreva sua mensagem antes de enviar."), Length(max=MAX_MESSAGE_LENGTH)],
    )
    submit = SubmitField("Enviar")


class LojistaFeedbackForm(FlaskForm):
    """Usado no painel do lojista — já logado (nome/e-mail vêm da própria
    conta), então só pede o tipo e a mensagem."""

    kind = SelectField("O que você quer nos dizer?", choices=FEEDBACK_KIND_CHOICES, validators=[DataRequired()])
    message = TextAreaField(
        "Mensagem",
        validators=[DataRequired(message="Escreva sua mensagem antes de enviar."), Length(max=MAX_MESSAGE_LENGTH)],
    )
    submit = SubmitField("Enviar")


class FeedbackStatusForm(FlaskForm):
    """Usado pelo Super Admin para mudar o status e anotar o
    acompanhamento de um item (ver admin/feedback/index.html)."""

    status = SelectField(
        "Status",
        choices=[
            ("new", "Novo"),
            ("in_review", "Em análise"),
            ("resolved", "Resolvido"),
            ("archived", "Arquivado"),
        ],
        validators=[DataRequired()],
    )
    admin_notes = TextAreaField("Anotação interna (opcional)", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Salvar")
