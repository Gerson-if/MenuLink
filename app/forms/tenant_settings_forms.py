from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DecimalField, FormField, IntegerField, SelectField, StringField, SubmitField
from wtforms import Form as NoCsrfForm
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp, ValidationError

from app.models.platform_settings import SYSTEM_THEME_CHOICES
from app.models.tenant import WEEKDAY_KEYS, WEEKDAY_LABELS
from app.utils.colors import normalize_hex
from app.utils.validators import cpf_cnpj, not_blank, phone_number, slug_format

# Escolha do PAINEL de gestão do próprio lojista (diferente do tema do
# cardápio público, controlado por accent_color/theme_mode abaixo) — a
# opção vazia ("") significa "acompanhar o tema definido pelo Super
# Admin para a plataforma inteira" (ver Tenant.panel_theme e
# PlatformSettings.system_theme). Reaproveita as mesmas 4 paletas do
# tema unificado do sistema (SYSTEM_THEME_CHOICES), pelo mesmo motivo:
# são as paletas já prontas/testadas no design system, sem inventar um
# conjunto de temas paralelo só para o lojista.
PANEL_THEME_CHOICES = [("", "Padrão da plataforma")] + SYSTEM_THEME_CHOICES

# Tema PRONTO do CARDÁPIO PÚBLICO (diferente de PANEL_THEME_CHOICES
# acima, que é para a área de gestão) — a opção vazia ("") significa
# "cor personalizada", isto é, continuar usando a cor livre escolhida
# em accent_color logo abaixo (comportamento padrão, sempre existiu).
# Mesmas 4 paletas do tema unificado do sistema, pelo mesmo motivo de
# PANEL_THEME_CHOICES: são atalhos para paletas já prontas, não um
# conjunto de temas paralelo.
PUBLIC_THEME_CHOICES = [("", "Personalizado (cor livre abaixo)")] + SYSTEM_THEME_CHOICES

# HH:MM, 24 horas (aceita "9:00" ou "09:00")
_TIME_FORMAT = Regexp(r"^([01]?\d|2[0-3]):[0-5]\d$", message="Use o formato HH:MM (ex: 18:30).")


class StoreInfoForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    menu_tagline = StringField(
        'Legenda do cardápio público (ex: "Pizzas Artesanais") — deixe em branco para usar "Cardápio Digital"',
        validators=[Optional(), Length(max=60)],
    )
    whatsapp_number = StringField("WhatsApp (com DDI+DDD)", validators=[Optional(), Length(max=20), phone_number()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    logo = FileField("Logo da loja", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])

    legal_name = StringField("Razão social (opcional)", validators=[Optional(), Length(max=150), not_blank])
    document = StringField("CPF ou CNPJ", validators=[Optional(), Length(max=20), cpf_cnpj])

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
    stale_order_alert_minutes = IntegerField(
        "Avisar pedido parado após quantos minutos no mesmo status",
        validators=[DataRequired(), NumberRange(min=1, max=240)],
        default=20,
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
    public_theme = SelectField(
        "Tema do cardápio",
        choices=PUBLIC_THEME_CHOICES,
        default="",
        validators=[Optional()],
    )
    accent_color = StringField(
        "Cor de destaque do cardápio",
        validators=[Optional(), Length(max=7)],
        render_kw={"type": "color"},
    )
    reset_to_default = BooleanField("Voltar para a cor padrão (âmbar)")
    theme_mode = SelectField(
        "Modo do cardápio",
        choices=[("dark", "Escuro (atual)"), ("light", "Claro")],
        default="dark",
    )
    panel_theme = SelectField(
        "Tema do meu painel de gestão",
        choices=PANEL_THEME_CHOICES,
        default="",
        validators=[Optional()],
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
