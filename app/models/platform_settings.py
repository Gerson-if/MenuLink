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

from sqlalchemy import JSON, Column, Integer, String

from app.extensions import db
from app.models.base import TimestampMixin

ASAAS_ENVIRONMENT_CHOICES = [
    ("sandbox", "Sandbox (testes)"),
    ("production", "Produção"),
]

# Textos atuais de app/templates/marketing/landing.html, hardcoded lá
# até agora — viram o default aqui, então nada muda visualmente pra quem
# nunca editar a landing page pelo painel do Super Admin.
LANDING_THEME_CHOICES = [
    ("chili", "Atual (marca)"),
    ("blue", "Escuro alternativo"),
    ("light", "Claro"),
]

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
