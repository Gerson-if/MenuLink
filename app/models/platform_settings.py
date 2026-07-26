"""
Configurações globais da plataforma, geridas pelo Super Administrador
(não são escopadas por tenant — não herdam TenantScopedMixin).

Hoje guarda só as credenciais da integração de cobrança com o Asaas
(usada para cobrar as faturas dos lojistas, ver
app/services/payment_gateway/ e app/services/admin_billing_service.py).
Antes só dava pra configurar via variável de ambiente (ASAAS_API_KEY
etc.) — isso continua funcionando como valor padrão/fallback (ver
`get_gateway()`), mas agora o Super Admin pode configurar e trocar pela
tela de administração, sem precisar de acesso ao servidor.

Tabela de linha única (singleton): sempre o registro de id=1, obtido
via `PlatformSettings.get_or_create()`.
"""

from sqlalchemy import JSON, Column, Date, Integer, String, Text

from app.extensions import db
from app.models.base import TimestampMixin

ASAAS_ENVIRONMENT_CHOICES = [
    ("sandbox", "Sandbox (testes)"),
    ("production", "Produção"),
]

# Forma de cobrança usada ao gerar uma cobrança Asaas (ver
# AsaasGateway.create_charge). PIX é o padrão e a recomendação da
# Comanda: o Asaas confirma o pagamento via webhook quase em tempo real
# (evento PAYMENT_RECEIVED, ver webhooks_controller.py), liberando o
# lojista na hora — diferente de boleto (compensação bancária, dias de
# atraso) ou cartão (temos evitado, por trazer taxa maior e disputa de
# chargeback). As outras opções ficam disponíveis para o dia em que a
# plataforma decidir habilitá-las, sem precisar mexer em código.
ASAAS_BILLING_TYPE_CHOICES = [
    ("PIX", "Pix (recomendado — liberação em tempo real)"),
    ("UNDEFINED", "Cliente escolhe (Pix, boleto ou cartão)"),
    ("BOLETO", "Somente boleto"),
    ("CREDIT_CARD", "Somente cartão de crédito"),
]

# Textos atuais de app/templates/marketing/landing.html, hardcoded lá
# até agora — viram o default aqui, então nada muda visualmente pra quem
# nunca editar a landing page pelo painel do Super Admin.
LANDING_THEME_CHOICES = [
    ("chili", "Atual (marca)"),
    ("blue", "Escuro alternativo"),
    ("light", "Claro"),
]

# Mensagem padrão do lembrete automático de fim do período de teste,
# mandada pelo chat de suporte (ver app/services/trial_service.py).
# Placeholders substituídos na hora do envio: {loja}, {dias_restantes},
# {data_fim} — documentados também na tela de configuração (Super Admin).
DEFAULT_TRIAL_REMINDER_MESSAGE = (
    "Olá, {loja}! 👋 Passando para lembrar que seu período de teste grátis no "
    "Comanda termina em {dias_restantes} dia(s), no dia {data_fim}. Se estiver "
    "gostando do sistema, escolha um plano quando quiser para continuar "
    "aproveitando o cardápio digital sem interrupções — qualquer dúvida, é só "
    "chamar a gente por aqui! 🙂"
)

DEFAULT_LANDING_CONTENT = {
    "hero_title": "O Cardápio Digital que seu Delivery merece.",
    "hero_subtitle": (
        "Monte seu cardápio digital em minutos, deixe seus clientes pedirem direto "
        "pelo link e receba tudo organizado no WhatsApp — sem comissão por pedido."
    ),
    "hero_image": None,
    "hero_image_opacity": 100,
    "hero_image_blur": 0,
    "hero_video": None,
    "theme": "chili",
    "features": [
        {
            "icon": "bi-phone-vibrate",
            "image": None,
            "title": "Cardápio Ultra Rápido",
            "description": "Seus clientes visualizam fotos otimizadas, escolhem complementos direto do navegador, sem downloads.",
        },
        {
            "icon": "bi-qr-code-scan",
            "image": None,
            "title": "Gerador de QR Code",
            "description": "O sistema gera automaticamente o endereço do seu estabelecimento e disponibiliza o QR Code para impressão.",
        },
        {
            "icon": "bi-whatsapp",
            "image": None,
            "title": "Pedidos via WhatsApp",
            "description": "Cada pedido é registrado no seu painel e enviado com um clique para o WhatsApp da loja, pronto para confirmar.",
        },
    ],
}


class PlatformSettings(db.Model, TimestampMixin):
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True)

    asaas_api_key = Column(String(255), nullable=True)
    asaas_environment = Column(String(20), nullable=False, default="sandbox", server_default="sandbox")
    asaas_webhook_token = Column(String(255), nullable=True)
    # "PIX" por padrão — ver ASAAS_BILLING_TYPE_CHOICES acima.
    asaas_billing_type = Column(String(20), nullable=False, default="PIX", server_default="PIX")

    # Cor de destaque (accent) do painel administrativo (Super Admin +
    # lojista compartilham o mesmo design system "chili"). Se vazio, usa
    # o vermelho-tijolo padrão da marca — nunca fica sem cor definida.
    admin_theme_accent = Column(String(7), nullable=True)

    # Logo da própria plataforma (não confundir com Tenant.logo_path, a
    # logo de cada loja) — usada na landing page, no favicon padrão do
    # site e nas telas de autenticação (login, cadastro, recuperação de
    # senha). Se vazio, usa o ícone/marca padrão do Comanda. Não aparece
    # em nenhuma tela de configuração do lojista nem no cardápio público
    # de cada loja — essas continuam só com a própria logo do tenant.
    platform_logo = Column(String(255), nullable=True)

    # WhatsApp de suporte da PLATAFORMA (diferente do whatsapp de cada
    # loja) — mostrado ao lojista na página de assinatura e, principalmente,
    # na página de conta bloqueada por inadimplência (ver
    # lojista/billing_lockout.html), pra ele conseguir ajuda sem precisar
    # já saber o contato de cor. Dígitos com DDI+DDD, ex: 5567999998888.
    support_whatsapp = Column(String(20), nullable=True)

    # Conteúdo editável da landing page (marketing/landing.html) — JSON
    # pra caber título/subtítulo do hero + lista de features sem exigir
    # uma migration nova a cada campo (mesmo padrão de Tenant.theme_settings).
    landing_content = Column(JSON, nullable=True)

    # --- Integração de e-mail (SMTP) ---
    # Usado por app/services/email_service.py para enviar e-mails de
    # verdade (recuperação de senha, cobrança de faturas etc.) — ver
    # app/utils/mailer.py. Sem isso configurado, o sistema continua
    # funcionando (o link de recuperação de senha só fica no log da
    # aplicação, como sempre foi), então essa integração é opcional.
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, nullable=False, default=587, server_default="587")
    smtp_username = Column(String(255), nullable=True)
    smtp_password = Column(String(255), nullable=True)
    # "tls" (STARTTLS, porta 587 normalmente) ou "ssl" (SMTPS implícito,
    # porta 465 normalmente) — a maioria dos provedores (Gmail, SendGrid,
    # Amazon SES, Mailgun) aceita STARTTLS na 587.
    smtp_encryption = Column(String(10), nullable=False, default="tls", server_default="tls")
    smtp_from_email = Column(String(255), nullable=True)
    smtp_from_name = Column(String(150), nullable=True)

    # --- Termos de Uso / Política de Privacidade ---
    # Texto em HTML simples (h2/p/ul/li/strong — mesmas tags já usadas nos
    # templates padrão) que SUBSTITUI o conteúdo hardcoded de
    # marketing/terms.html e marketing/privacy.html quando preenchido.
    # Vazio/None = continua mostrando o texto padrão do template (nada
    # muda pra quem nunca editar pelo painel). Ver
    # app/controllers/admin/settings.py:settings_legal.
    terms_content = Column(Text, nullable=True)
    terms_updated_at = Column(Date, nullable=True)
    privacy_content = Column(Text, nullable=True)
    privacy_updated_at = Column(Date, nullable=True)

    # --- Período de teste grátis (trial) ---
    # Controla o cadastro de TODA loja nova (ver AdminTenantService.create,
    # que grava Tenant.trial_ends_at = agora + trial_days no momento da
    # criação) e o job `flask process-trials` (ver app/services/trial_service.py),
    # que manda lembretes automáticos e suspende quem passar do prazo.
    trial_days = Column(Integer, nullable=False, default=20, server_default="20")
    # Tolerância extra, em dias, DEPOIS do fim do teste — a loja continua
    # acessando o painel normalmente nesse período, só para regularizar
    # (ver Tenant.trial_status). Só depois disso a conta é suspensa.
    trial_grace_days = Column(Integer, nullable=False, default=2, server_default="2")
    # Com quantos dias de antecedência o lembrete automático começa a ser
    # enviado (antes do fim do teste).
    trial_reminder_days_before = Column(Integer, nullable=False, default=5, server_default="5")
    # De quantos em quantos dias o lembrete se repete, enquanto ainda não
    # regularizado (evita mandar mensagem nova a cada execução do job).
    trial_reminder_cycle_days = Column(Integer, nullable=False, default=3, server_default="3")
    # Texto do lembrete — None/vazio usa DEFAULT_TRIAL_REMINDER_MESSAGE.
    trial_reminder_message = Column(Text, nullable=True)
    # A partir de quantos dias restantes o PAINEL do lojista troca do
    # aviso discreto (linha pequena) pra uma barra fixa e chamativa no
    # topo, pedindo pra assinar um plano — ver Tenant.trial_status e
    # layouts/lojista_panel.html. Continua chamativa durante toda a
    # tolerância também (ver `trial_grace_days` acima).
    trial_urgent_days = Column(Integer, nullable=False, default=3, server_default="3")

    # --- Dunning (falha de pagamento / fatura vencida) ---
    # Quantos dias de tolerância depois do vencimento de uma fatura
    # (Invoice.due_date) antes de bloquear a loja por inadimplência (ver
    # `flask process-dunning` / AdminBillingService.process_dunning).
    # Mesmo espírito de `trial_grace_days`, mas para faturas de uma loja
    # já paga (não em teste).
    dunning_grace_days = Column(Integer, nullable=False, default=3, server_default="3")

    @classmethod
    def get_or_create(cls) -> "PlatformSettings":
        settings = db.session.get(cls, 1)
        if settings is None:
            settings = cls(id=1, asaas_environment="sandbox")
            db.session.add(settings)
            db.session.commit()
        return settings

    @property
    def asaas_configured(self) -> bool:
        return bool(self.asaas_api_key)

    @property
    def asaas_webhook_configured(self) -> bool:
        return bool(self.asaas_webhook_token)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @property
    def admin_theme_css_vars(self) -> dict | None:
        """Variáveis CSS derivadas da cor de destaque escolhida, prontas
        para injetar num <style> — ou None se não houver customização
        (nesse caso o painel usa a cor padrão definida em comanda.css).
        comanda.css deriva os tons mais claros com color-mix(--accent)
        na hora, então só --accent/--accent-dark precisam ser
        sobrescritos para todo o resto do design system acompanhar."""
        if not self.admin_theme_accent:
            return None
        from app.utils.colors import darken_hex

        return {"accent": self.admin_theme_accent, "accent_dark": darken_hex(self.admin_theme_accent)}

    @property
    def trial_reminder_message_or_default(self) -> str:
        return self.trial_reminder_message or DEFAULT_TRIAL_REMINDER_MESSAGE

    @property
    def landing_content_or_default(self) -> dict:
        """Faz merge com DEFAULT_LANDING_CONTENT — se o Super Admin nunca
        editou (ou editou só uma parte), os campos que faltam caem no
        texto padrão em vez de aparecerem em branco na landing page."""
        stored = self.landing_content or {}
        merged = {**DEFAULT_LANDING_CONTENT, **stored}
        if not merged.get("features"):
            merged["features"] = DEFAULT_LANDING_CONTENT["features"]
        return merged

    def __repr__(self):
        return f"<PlatformSettings asaas_configured={self.asaas_configured}>"
