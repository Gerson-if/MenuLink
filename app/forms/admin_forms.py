from datetime import date, timedelta

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DateField, DecimalField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional, ValidationError

from app.models.platform_settings import ASAAS_BILLING_TYPE_CHOICES, ASAAS_ENVIRONMENT_CHOICES, SYSTEM_THEME_CHOICES
from app.utils.colors import normalize_hex
from app.utils.validators import bounded_length, not_blank, password_max_bytes, phone_number


class TenantCreateForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    email = StringField("E-mail da loja", validators=[DataRequired(), bounded_length(180), Email()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    whatsapp_number = StringField("WhatsApp (com DDI+DDD, ex: 5567999998888)", validators=[Optional(), Length(max=20), phone_number()])
    plan_id = SelectField("Plano", coerce=int, validators=[Optional()])

    owner_name = StringField("Nome do responsável (lojista)", validators=[DataRequired(), Length(max=150), not_blank])
    owner_email = StringField("E-mail de login do lojista", validators=[DataRequired(), bounded_length(180), Email()])
    owner_password = StringField(
        "Senha inicial", validators=[DataRequired(), password_max_bytes(), Length(min=6, max=128)]
    )

    submit = SubmitField("Criar lojista")


class TenantEditForm(FlaskForm):
    name = StringField("Nome da loja", validators=[DataRequired(), Length(max=150), not_blank])
    email = StringField("E-mail da loja", validators=[DataRequired(), bounded_length(180), Email()])
    phone = StringField("Telefone (opcional)", validators=[Optional(), Length(max=20), phone_number()])
    whatsapp_number = StringField("WhatsApp (com DDI+DDD)", validators=[Optional(), Length(max=20), phone_number()])
    plan_id = SelectField("Plano", coerce=int, validators=[Optional()])
    submit = SubmitField("Salvar alterações")


class TenantStatusReasonForm(FlaskForm):
    reason = TextAreaField("Motivo (opcional)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Confirmar")


class TenantOwnerPasswordForm(FlaskForm):
    """Redefinição de senha do lojista (dono da loja) pelo Super Admin —
    ver AdminTenantService.set_owner_password. Diferente de
    ChangePasswordForm (troca pelo próprio usuário), não pede a senha
    atual: quem preenche isto é o Super Admin, não o lojista."""

    new_password = PasswordField(
        "Nova senha do lojista",
        validators=[
            DataRequired(message="Informe a nova senha."),
            password_max_bytes(),
            Length(min=6, max=128, message="A senha deve ter entre 6 e 128 caracteres."),
        ],
    )
    confirm_password = PasswordField(
        "Confirme a nova senha",
        validators=[DataRequired(message="Confirme a nova senha."), EqualTo("new_password", message="As senhas não coincidem.")],
    )
    submit = SubmitField("Redefinir senha")


class TenantTrialForm(FlaskForm):
    """Super Admin ajusta manualmente o fim do período de teste de uma
    loja específica — ver AdminTenantService.update_trial_deadline."""

    trial_ends_at = DateField("Novo fim do período de teste", validators=[DataRequired()])
    submit = SubmitField("Atualizar prazo")


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
    amount = DecimalField(
        "Valor (R$)", places=2,
        validators=[DataRequired(), NumberRange(min=0.01, max=100000, message="Valor deve estar entre R$ 0,01 e R$ 100.000,00.")],
    )
    due_date = DateField("Vencimento", validators=[DataRequired()])
    submit = SubmitField("Lançar fatura")

    def validate_due_date(self, field):
        if not field.data:
            return
        max_future = date.today() + timedelta(days=730)
        if field.data > max_future:
            raise ValidationError("Vencimento muito distante (máximo 2 anos à frente) — confira a data.")


class SubscriptionCycleForm(FlaskForm):
    billing_cycle = SelectField(
        "Ciclo de cobrança desta assinatura",
        choices=[("monthly", "Mensal"), ("yearly", "Anual")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Salvar ciclo")


class AsaasSettingsForm(FlaskForm):
    """
    Chave de API e token de webhook nunca são pré-preenchidos no GET
    (só um indicador de "já configurado" é mostrado no template) — os
    campos abaixo servem para DEFINIR um novo valor. Deixar em branco
    mantém o valor atual sem alterá-lo; marcar a caixa "remover"
    desativa a respectiva configuração.
    """

    environment = SelectField("Ambiente", choices=ASAAS_ENVIRONMENT_CHOICES)
    billing_type = SelectField("Forma de cobrança gerada", choices=ASAAS_BILLING_TYPE_CHOICES, default="PIX")

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


SMTP_ENCRYPTION_CHOICES = [
    ("tls", "STARTTLS (recomendado — porta 587 na maioria dos provedores)"),
    ("ssl", "SSL/TLS implícito (porta 465 na maioria dos provedores)"),
    ("none", "Nenhuma (sem criptografia — só pra servidores internos/teste)"),
]


class EmailSettingsForm(FlaskForm):
    """
    Integração de e-mail (SMTP) — usada para recuperação de senha,
    avisos de cobrança de fatura etc (ver app/services/email_service.py).
    A senha nunca é pré-preenchida no GET (mesmo padrão da chave de API
    do Asaas) — deixar em branco mantém a senha atual.
    """

    host = StringField("Servidor SMTP (host)", validators=[DataRequired(), Length(max=255)])
    port = IntegerField("Porta", validators=[DataRequired(), NumberRange(min=1, max=65535)], default=587)
    encryption = SelectField("Criptografia", choices=SMTP_ENCRYPTION_CHOICES)

    username = StringField("Usuário SMTP", validators=[Optional(), Length(max=255)])
    password = StringField(
        "Nova senha (deixe em branco para manter a atual)", validators=[Optional(), Length(max=255)],
    )
    clear_password = BooleanField("Remover senha salva")

    from_email = StringField("E-mail remetente (de onde os e-mails são enviados)", validators=[DataRequired(), Email(), Length(max=255)])
    from_name = StringField("Nome do remetente", validators=[Optional(), Length(max=150)], default="Comanda")

    submit = SubmitField("Salvar configurações")

    def validate_password(self, field):
        if field.data and self.clear_password.data:
            raise ValidationError("Não dá pra definir uma nova senha e marcar \"remover\" ao mesmo tempo.")


class EmailTestForm(FlaskForm):
    to = StringField("Enviar e-mail de teste para", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Enviar teste")


class LegalContentForm(FlaskForm):
    """Termos de Uso e Política de Privacidade — texto em HTML simples
    (h2, p, ul/li, strong são as tags já usadas no texto padrão). Deixar
    em branco restaura o texto padrão do sistema para aquele documento."""

    terms_content = TextAreaField("Termos de Uso (HTML)", validators=[Optional(), Length(max=20000)])
    privacy_content = TextAreaField("Política de Privacidade (HTML)", validators=[Optional(), Length(max=20000)])
    submit = SubmitField("Salvar")


class TrialSettingsForm(FlaskForm):
    """Configurações globais do período de teste grátis — afeta o
    cadastro de lojas NOVAS (ver AdminTenantService.create) e o job
    `flask process-trials` (ver app/services/trial_service.py)."""

    trial_days = IntegerField(
        "Duração do período de teste (dias)",
        validators=[DataRequired(), NumberRange(min=1, max=365)],
    )
    trial_grace_days = IntegerField(
        "Dias extras de tolerância após o teste acabar",
        validators=[DataRequired(), NumberRange(min=0, max=60)],
    )
    trial_reminder_days_before = IntegerField(
        "Avisar com quantos dias de antecedência",
        validators=[DataRequired(), NumberRange(min=0, max=90)],
    )
    trial_reminder_cycle_days = IntegerField(
        "Repetir o lembrete a cada quantos dias",
        validators=[DataRequired(), NumberRange(min=1, max=30)],
    )
    trial_reminder_message = TextAreaField(
        "Mensagem do lembrete (enviada pelo chat de suporte)",
        validators=[Optional(), Length(max=2000)],
    )
    trial_urgent_days = IntegerField(
        "A partir de quantos dias restantes o painel do lojista fica com aviso chamativo",
        validators=[DataRequired(), NumberRange(min=0, max=30)],
    )
    submit = SubmitField("Salvar configurações")


class AdminAppearanceForm(FlaskForm):
    """Cor de destaque do painel administrativo (Super Admin + painel
    do lojista compartilham o mesmo design system) — opcional, some
    dono de plataforma pode querer trocar o vermelho-tijolo padrão pela
    cor da própria marca. Inclui também a logo do sistema (landing page,
    favicon e telas de autenticação — ver PlatformSettings.platform_logo;
    não afeta a logo de cada loja nem o painel do lojista)."""

    system_theme = SelectField(
        "Tema do sistema", choices=SYSTEM_THEME_CHOICES, default="chili", validators=[Optional()],
    )

    accent_color = StringField(
        "Cor de destaque do painel",
        validators=[Optional(), Length(max=7)],
        render_kw={"type": "color"},
    )
    reset_to_default = BooleanField("Voltar para a cor padrão")

    platform_logo = FileField(
        "Logo do sistema (opcional)",
        validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], message="Formato não suportado.")],
    )

    support_whatsapp = StringField(
        "WhatsApp de suporte (com DDI+DDD, ex: 5567999998888)",
        validators=[Optional(), Length(max=20), phone_number()],
    )

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
    hero_image_opacity = IntegerField(
        "Opacidade da arte de destaque (%)", default=100,
        validators=[Optional(), NumberRange(min=10, max=100)],
        render_kw={"type": "range", "min": 10, "max": 100, "step": 5},
    )
    hero_image_blur = IntegerField(
        "Desfoque da arte de destaque (px)", default=0,
        validators=[Optional(), NumberRange(min=0, max=20)],
        render_kw={"type": "range", "min": 0, "max": 20, "step": 1},
    )
    hero_video = FileField(
        "Vídeo de fundo do hero (opcional — substitui a arte de destaque)",
        validators=[FileAllowed(["mp4", "webm"], message="Formato não suportado. Use MP4 ou WEBM.")],
    )
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
