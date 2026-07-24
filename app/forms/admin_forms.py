from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DateField, DecimalField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError

from app.models.platform_settings import ASAAS_ENVIRONMENT_CHOICES, LANDING_THEME_CHOICES
from app.utils.colors import normalize_hex
from app.utils.validators import not_blank, phone_number


class TenantCreateForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    email = StringField("E-mail da loja", validators=[DataRequired(), Length(max=180), Email()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    whatsapp_number = StringField("WhatsApp (com DDI+DDD, ex: 5567999998888)", validators=[Optional(), Length(max=20), phone_number()])
    plan_id = SelectField("Plano", coerce=int, validators=[Optional()])

    owner_name = StringField("Nome do responsável (lojista)", validators=[DataRequired(), Length(max=150), not_blank])
    owner_email = StringField("E-mail de login do lojista", validators=[DataRequired(), Length(max=180), Email()])
    owner_password = StringField("Senha inicial", validators=[DataRequired(), Length(min=6, max=128)])

    submit = SubmitField("Criar lojista")


class TenantEditForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    email = StringField("E-mail da loja", validators=[DataRequired(), Length(max=180), Email()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    whatsapp_number = StringField("WhatsApp (com DDI+DDD)", validators=[Optional(), Length(max=20), phone_number()])
    plan_id = SelectField("Plano", coerce=int, validators=[Optional()])
    submit = SubmitField("Salvar alterações")


class TenantStatusReasonForm(FlaskForm):
    reason = TextAreaField("Motivo (opcional)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Confirmar")


class PlanForm(FlaskForm):
    name = StringField("Nome do plano", validators=[DataRequired(), Length(max=80), not_blank])
    description = TextAreaField("Descrição (opcional)", validators=[Optional(), Length(max=500)])
    price = DecimalField("Preço (R$)", places=2, validators=[DataRequired(), NumberRange(min=0)])
    billing_cycle = SelectField("Ciclo de cobrança", choices=[("monthly", "Mensal"), ("yearly", "Anual")])
    max_categories = IntegerField("Limite de categorias (vazio = ilimitado)", validators=[Optional(), NumberRange(min=0)])
    max_products = IntegerField("Limite de produtos (vazio = ilimitado)", validators=[Optional(), NumberRange(min=0)])
    max_images_per_product = IntegerField("Limite de imagens por produto", default=5, validators=[DataRequired(), NumberRange(min=1, max=20)])
    is_featured = BooleanField('Destacar como "MAIS VENDIDO" na landing page')
    display_order = IntegerField("Ordem de exibição na landing (menor aparece primeiro)", default=0, validators=[Optional(), NumberRange(min=0, max=999)])
    submit = SubmitField("Salvar plano")


class InvoiceForm(FlaskForm):
    amount = DecimalField("Valor (R$)", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    due_date = DateField("Vencimento", validators=[DataRequired()])
    submit = SubmitField("Lançar fatura")


class AsaasSettingsForm(FlaskForm):
    """
    Chave de API e token de webhook nunca são pré-preenchidos no GET
    (só um indicador de "já configurado" é mostrado no template) — os
    campos abaixo servem para DEFINIR um novo valor. Deixar em branco
    mantém o valor atual sem alterá-lo; marcar a caixa "remover"
    desativa a respectiva configuração.
    """

    environment = SelectField("Ambiente", choices=ASAAS_ENVIRONMENT_CHOICES)

    api_key = StringField(
        "Nova chave de API (deixe em branco para manter a atual)",
        validators=[Optional(), Length(max=255)],
    )
    clear_api_key = BooleanField("Remover chave de API (desativa a integração)")

    webhook_token = StringField(
        "Novo token de webhook (deixe em branco para manter o atual)",
        validators=[Optional(), Length(min=16, max=255, message="Use pelo menos 16 caracteres — prefira o botão \"Gerar token aleatório\".")],
    )
    clear_webhook_token = BooleanField("Remover token de webhook")

    submit = SubmitField("Salvar configurações")

    def validate_api_key(self, field):
        if field.data and self.clear_api_key.data:
            raise ValidationError("Não dá pra definir uma nova chave e marcar \"remover\" ao mesmo tempo.")

    def validate_webhook_token(self, field):
        if field.data and self.clear_webhook_token.data:
            raise ValidationError("Não dá pra definir um novo token e marcar \"remover\" ao mesmo tempo.")


class AdminAppearanceForm(FlaskForm):
    """Cor de destaque do painel administrativo (Super Admin + painel
    do lojista compartilham o mesmo design system) — opcional, some
    dono de plataforma pode querer trocar o vermelho-tijolo padrão pela
    cor da própria marca."""

    accent_color = StringField(
        "Cor de destaque do painel",
        validators=[Optional(), Length(max=7)],
        render_kw={"type": "color"},
    )
    reset_to_default = BooleanField("Voltar para a cor padrão")
    submit = SubmitField("Salvar aparência")

    def validate_accent_color(self, field):
        if field.data and not self.reset_to_default.data and not normalize_hex(field.data):
            raise ValidationError("Cor inválida.")


class LandingContentForm(FlaskForm):
    """Textos editáveis da landing page pública (marketing/landing.html)
    — título/subtítulo do hero e as 3 caixas de destaque. Estrutura fixa
    (3 slots) em vez de uma lista dinâmica, pra manter o form simples;
    bate exatamente com o layout atual da página."""

    hero_title = StringField("Título principal", validators=[DataRequired(), Length(max=200), not_blank])
    hero_subtitle = TextAreaField("Subtítulo", validators=[DataRequired(), Length(max=500), not_blank])
    hero_image = FileField("Arte de destaque (opcional)", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])
    hero_video = FileField(
        "Vídeo de fundo do hero (opcional — substitui a arte de destaque)",
        validators=[FileAllowed(["mp4", "webm"], message="Formato não suportado. Use MP4 ou WEBM.")],
    )
    theme = SelectField("Tema da landing page", choices=LANDING_THEME_CHOICES, default="chili")

    feature1_icon = StringField("Ícone 1 (Bootstrap Icons)", validators=[DataRequired(), Length(max=40)])
    feature1_title = StringField("Título 1", validators=[DataRequired(), Length(max=100), not_blank])
    feature1_description = TextAreaField("Descrição 1", validators=[DataRequired(), Length(max=300), not_blank])
    feature1_image = FileField("Imagem 1 (opcional — substitui o ícone)", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])

    feature2_icon = StringField("Ícone 2 (Bootstrap Icons)", validators=[DataRequired(), Length(max=40)])
    feature2_title = StringField("Título 2", validators=[DataRequired(), Length(max=100), not_blank])
    feature2_description = TextAreaField("Descrição 2", validators=[DataRequired(), Length(max=300), not_blank])
    feature2_image = FileField("Imagem 2 (opcional — substitui o ícone)", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])

    feature3_icon = StringField("Ícone 3 (Bootstrap Icons)", validators=[DataRequired(), Length(max=40)])
    feature3_title = StringField("Título 3", validators=[DataRequired(), Length(max=100), not_blank])
    feature3_description = TextAreaField("Descrição 3", validators=[DataRequired(), Length(max=300), not_blank])
    feature3_image = FileField("Imagem 3 (opcional — substitui o ícone)", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")])

    submit = SubmitField("Salvar landing page")
