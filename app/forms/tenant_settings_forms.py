from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DecimalField, FormField, SelectField, StringField, SubmitField
from wtforms import Form as NoCsrfForm
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp, ValidationError

from app.models.tenant import WEEKDAY_KEYS, WEEKDAY_LABELS
from app.utils.colors import normalize_hex
from app.utils.validators import not_blank, phone_number, slug_format

# HH:MM, 24 horas (aceita "9:00" ou "09:00")
_TIME_FORMAT = Regexp(r"^([01]?\d|2[0-3]):[0-5]\d$", message="Use o formato HH:MM (ex: 18:30).")


class StoreInfoForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    whatsapp_number = StringField("WhatsApp (com DDI+DDD)", validators=[Optional(), Length(max=20), phone_number()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    logo = FileField("Logo da loja", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])

    address_street = StringField("Rua", validators=[Optional(), Length(max=180)])
    address_number = StringField("Número", validators=[Optional(), Length(max=20)])
    address_neighborhood = StringField("Bairro", validators=[Optional(), Length(max=100)])
    address_city = StringField("Cidade", validators=[Optional(), Length(max=100)])

    submit = SubmitField("Salvar dados da loja")


class MenuSettingsForm(FlaskForm):
    slug = StringField("Endereço do cardápio", validators=[DataRequired(), Length(min=3, max=150), slug_format])
    pickup_enabled = BooleanField("Aceitar retirada no local", default=True)
    delivery_enabled = BooleanField("Aceitar entrega", default=False)
    show_price_from_label = BooleanField('Mostrar "a partir de" no preço de produtos com variação/complemento', default=True)
    notes_placeholder = StringField(
        "Texto de exemplo no campo de observações do checkout",
        validators=[Optional(), Length(max=150)],
    )
    submit = SubmitField("Salvar")


class CheckoutSettingsForm(FlaskForm):
    delivery_fee = DecimalField("Taxa de entrega (R$)", places=2, validators=[Optional(), NumberRange(min=0)])
    free_delivery_above = DecimalField("Entrega grátis acima de (R$) — opcional", places=2, validators=[Optional(), NumberRange(min=0)])
    min_order = DecimalField("Pedido mínimo (R$) — opcional", places=2, validators=[Optional(), NumberRange(min=0)])

    accept_pix = BooleanField("Pix", default=True)
    accept_card = BooleanField("Cartão", default=True)
    accept_cash = BooleanField("Dinheiro", default=True)
    accept_other = BooleanField("Outro", default=True)

    submit = SubmitField("Salvar")

    def validate_accept_other(self, field):
        # Precisa sobrar pelo menos uma forma de pagamento marcada — sem
        # isso o checkout público fica sem nenhuma opção selecionável.
        if not any([self.accept_pix.data, self.accept_card.data, self.accept_cash.data, field.data]):
            raise ValidationError("Marque pelo menos uma forma de pagamento.")


class AppearanceSettingsForm(FlaskForm):
    accent_color = StringField(
        "Cor de destaque do cardápio",
        validators=[Optional(), Length(max=7)],
        render_kw={"type": "color"},
    )
    reset_to_default = BooleanField("Voltar para a cor padrão (âmbar)")
    theme_mode = SelectField(
        "Tema do cardápio",
        choices=[("dark", "Escuro (atual)"), ("light", "Claro")],
        default="dark",
    )
    submit = SubmitField("Salvar aparência")

    def validate_accent_color(self, field):
        if field.data and not self.reset_to_default.data and not normalize_hex(field.data):
            raise ValidationError("Cor inválida.")


class DayHoursForm(NoCsrfForm):
    """Subformulário de um único dia da semana (sem CSRF próprio — é
    aninhado dentro de OpeningHoursForm via FormField)."""

    closed = BooleanField("Fechado o dia todo")
    open = StringField("Abre", validators=[Optional(), _TIME_FORMAT])
    close = StringField("Fecha", validators=[Optional(), _TIME_FORMAT])


class OpeningHoursForm(FlaskForm):
    """
    Um FormField por dia da semana (mon..sun). Renderizado no template
    iterando `WEEKDAY_KEYS` e acessando `form[key]` — evita repetir 7
    blocos de campo manualmente.
    """

    mon = FormField(DayHoursForm, label=WEEKDAY_LABELS["mon"])
    tue = FormField(DayHoursForm, label=WEEKDAY_LABELS["tue"])
    wed = FormField(DayHoursForm, label=WEEKDAY_LABELS["wed"])
    thu = FormField(DayHoursForm, label=WEEKDAY_LABELS["thu"])
    fri = FormField(DayHoursForm, label=WEEKDAY_LABELS["fri"])
    sat = FormField(DayHoursForm, label=WEEKDAY_LABELS["sat"])
    sun = FormField(DayHoursForm, label=WEEKDAY_LABELS["sun"])
    submit = SubmitField("Salvar horário de funcionamento")

    def days(self):
        """Itera (chave, subform) na ordem de WEEKDAY_KEYS, para o template."""
        for key in WEEKDAY_KEYS:
            yield key, getattr(self, key)
