"""
Tenant = a "loja" (lojista/cliente) dentro da plataforma SaaS.
Plan   = plano de assinatura oferecido pelo Super Administrador.

Todo dado de domínio de uma loja (produtos, categorias, pedidos, clientes,
imagens) referencia tenants.id. O isolamento entre lojistas acontece
filtrando SEMPRE por tenant_id nas queries (ver base_repository.py).
"""

import enum
import math
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin, str_enum

# Dias da semana usados como chave no JSON de `opening_hours`, na ordem
# de exibição do formulário (segunda a domingo). O valor de cada dia é
# {"closed": true} ou {"open": "HH:MM", "close": "HH:MM"}.
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_LABELS = {
    "mon": "Segunda", "tue": "Terça", "wed": "Quarta", "thu": "Quinta",
    "fri": "Sexta", "sat": "Sábado", "sun": "Domingo",
}


class TenantStatus(str, enum.Enum):
    TRIAL = "trial"                    # período de teste
    ACTIVE = "active"                  # conta ativa e em dia
    SUSPENDED = "suspended"            # suspensa manualmente pelo Super Admin
    BLOCKED_PAYMENT = "blocked_payment"  # bloqueada por inadimplência
    CANCELED = "canceled"              # cancelada definitivamente


class BillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Plan(db.Model, TimestampMixin):
    """Plano de assinatura (ex: Básico, Pro, Enterprise)."""

    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    slug = Column(String(80), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    price_cents = Column(Integer, nullable=False, default=0)
    billing_cycle = Column(
        str_enum(BillingCycle, "billing_cycle"), nullable=False,
        default=BillingCycle.MONTHLY,
    )

    max_products = Column(Integer, nullable=True)   # null = ilimitado
    max_categories = Column(Integer, nullable=True)  # null = ilimitado
    max_images_per_product = Column(Integer, nullable=False, default=5)

    is_active = Column(Boolean, nullable=False, default=True)  # plano disponível p/ contratação

    # Exibição na landing page pública (marketing/landing.html) — antes o
    # selo "MAIS VENDIDO" e a ordem dos cards eram hardcoded (2º plano
    # sempre em destaque, ordem = data de criação). Agora o Super Admin
    # controla os dois.
    is_featured = Column(Boolean, nullable=False, default=False, server_default="0")
    display_order = Column(Integer, nullable=False, default=0, server_default="0")

    tenants = relationship("Tenant", back_populates="plan")

    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_plan_price_non_negative"),
    )

    def __repr__(self):
        return f"<Plan {self.slug}>"


class Tenant(db.Model, TimestampMixin):
    """Uma loja/lojista dentro da plataforma."""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)

    # Identificação
    name = Column(String(150), nullable=False)          # nome fantasia (exibido no cardápio)
    legal_name = Column(String(150), nullable=True)      # razão social (opcional)
    document = Column(String(20), nullable=True)         # CPF/CNPJ (opcional)
    slug = Column(String(150), nullable=False, unique=True, index=True)  # usado na URL pública

    # Contato
    email = Column(String(180), nullable=False, unique=True, index=True)
    phone = Column(String(20), nullable=True)
    whatsapp_number = Column(String(20), nullable=True)  # número completo com DDI+DDD p/ wa.me

    # Plano / assinatura
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    plan = relationship("Plan", back_populates="tenants")

    # Status da conta (controlado pelo Super Admin)
    status = Column(
        str_enum(TenantStatus, "tenant_status"), nullable=False,
        default=TenantStatus.TRIAL, index=True,
    )
    blocked_reason = Column(String(255), nullable=True)
    blocked_at = Column(db.DateTime(timezone=True), nullable=True)

    # --- Período de teste grátis ---
    # Preenchido em AdminTenantService.create() com base em
    # PlatformSettings.trial_days no momento do cadastro (ajustável pelo
    # Super Admin, mas o valor já gravado aqui não muda retroativamente
    # se a configuração global mudar depois). None para tenants criados
    # já ACTIVE (ex: a loja demo do `flask seed-db`) ou para instalações
    # antigas, anteriores a este recurso — nesses casos trial_status()
    # retorna None e nenhum aviso é mostrado.
    trial_ends_at = Column(db.DateTime(timezone=True), nullable=True)
    # Marca se o aviso ÚNICO de "seu teste acabou, você tem N dias de
    # tolerância" já foi mandado pelo chat de suporte (ver
    # app/services/trial_service.py) — evita reenviar a cada execução do
    # `flask process-trials`. Reseta pra False quando o Super Admin
    # estende o prazo manualmente (ver AdminTenantService.update_trial_deadline).
    trial_grace_notice_sent = Column(Boolean, nullable=False, default=False, server_default="0")
    # Última vez que o lembrete "faltam N dias" foi enviado (respeita o
    # ciclo configurado em PlatformSettings.trial_reminder_cycle_days,
    # pra não mandar mensagem repetida toda hora).
    trial_reminder_last_sent_at = Column(db.DateTime(timezone=True), nullable=True)
    # Duas mensagens FIXAS da régua de comunicação (texto não editável,
    # diferente do lembrete configurável acima) — ver
    # app/services/trial_service.py: um aviso único a 2 dias do fim do
    # teste, e outro no próprio dia em que o link efetivamente sai do ar
    # (fim da tolerância). Ambas resetam em
    # AdminTenantService.update_trial_deadline, pra poderem disparar de
    # novo se o Super Admin estender o prazo depois de já terem sido
    # enviadas.
    trial_d2_notice_sent = Column(Boolean, nullable=False, default=False, server_default="0")
    trial_final_notice_sent = Column(Boolean, nullable=False, default=False, server_default="0")

    # Marca que o lojista CONFIRMOU um plano pago ainda durante o
    # período de teste (ver TenantSettingsService.change_plan) — não é o
    # mesmo que `plan_id` estar preenchido, já que toda loja nova já
    # ganha o plano mais barato automaticamente (ver
    # AdminTenantService.create) sem isso significar uma adesão de
    # verdade. Enquanto o teste ainda não acabou, nenhuma assinatura
    # nem cobrança é criada — o lojista não perde os dias grátis
    # restantes (ver `flask process-trials` / TrialService, que ativa a
    # assinatura de verdade exatamente em `trial_ends_at` quando este
    # campo está preenchido).
    plan_confirmed_at = Column(db.DateTime(timezone=True), nullable=True)

    # Configurações do cardápio / operação
    delivery_enabled = Column(Boolean, nullable=False, default=False)
    delivery_fee_cents = Column(Integer, nullable=True)
    free_delivery_above_cents = Column(Integer, nullable=True)  # entrega grátis acima deste valor (opcional)
    min_order_cents = Column(Integer, nullable=True)
    pickup_enabled = Column(Boolean, nullable=False, default=True)

    # Mostra "a partir de R$ X" no card de produtos com variação/complemento
    # (preço mínimo, não o preço final). Alguns lojistas preferem esconder
    # esse texto e deixar só o preço base.
    show_price_from_label = Column(Boolean, nullable=False, default=True, server_default="1")

    # Formas de pagamento aceitas no checkout público — todas ligadas por
    # padrão (compatível com o comportamento anterior, quando não havia
    # essa configuração). Desligar uma aqui a esconde do cliente no
    # cardápio, evitando que ele escolha "Cartão"/"Dinheiro" numa loja que
    # só trabalha com Pix, por exemplo.
    accept_pix = Column(Boolean, nullable=False, default=True, server_default="1")
    accept_card = Column(Boolean, nullable=False, default=True, server_default="1")
    accept_cash = Column(Boolean, nullable=False, default=True, server_default="1")
    accept_other = Column(Boolean, nullable=False, default=True, server_default="1")

    # Aparência (tema do cardápio público)
    logo_path = Column(String(255), nullable=True)
    banner_path = Column(String(255), nullable=True)
    theme_settings = Column(JSON, nullable=True)  # cores, fontes etc.

    # Legenda curta mostrada ao lado/abaixo do nome da loja no cardápio
    # público (sidebar e topbar — ver public/store_menu.html), tipo um
    # subtítulo. Antes era um texto fixo ("CARDÁPIO DIGITAL") igual pra
    # toda loja; agora o lojista pode trocar por algo próprio (ex: "Pizzas
    # Artesanais", "Delivery & Retirada"). None/vazio mantém o texto
    # padrão (ver `menu_tagline_or_default` abaixo) — nada muda pra quem
    # nunca customizar.
    menu_tagline = Column(String(60), nullable=True)

    # Integração de cobrança (Asaas) — preenchido automaticamente na
    # primeira cobrança gerada para este tenant. Ver
    # app/services/payment_gateway/.
    asaas_customer_id = Column(String(60), nullable=True)

    # Endereço da loja (exibido no cardápio público — rodapé/sobre a
    # loja). Todos opcionais: uma loja só de entrega por raio, por
    # exemplo, pode preferir não divulgar o endereço físico.
    address_street = Column(String(180), nullable=True)
    address_number = Column(String(20), nullable=True)
    address_neighborhood = Column(String(100), nullable=True)
    address_city = Column(String(100), nullable=True)

    # Horário de funcionamento. JSON por dia da semana (chaves em
    # WEEKDAY_KEYS): {"mon": {"open": "18:00", "close": "23:00"}, ...}
    # ou {"mon": {"closed": true}} para um dia fechado. `None`/vazio
    # significa "sem horário configurado" — o cardápio público não
    # mostra o status aberto/fechado nesse caso.
    opening_hours = Column(JSON, nullable=True)

    # Relacionamentos (definidos nos respectivos models via back_populates)
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="tenant", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="tenant", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="tenant", cascade="all, delete-orphan")
    banners = relationship("Banner", back_populates="tenant", cascade="all, delete-orphan", order_by="Banner.display_order")
    subscriptions = relationship("Subscription", back_populates="tenant", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="tenant", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenant_slug"),
    )

    @property
    def is_active_account(self) -> bool:
        return self.status in (TenantStatus.TRIAL, TenantStatus.ACTIVE)

    @property
    def menu_tagline_or_default(self) -> str:
        return (self.menu_tagline or "").strip() or "CARDÁPIO DIGITAL"

    def trial_status(self, now: datetime | None = None) -> dict | None:
        """
        Informações do período de teste gratuito, para exibir um aviso
        ao lojista no painel (ver layouts/lojista_panel.html). Retorna
        None se a loja não estiver em TRIAL ou não tiver `trial_ends_at`
        definido (contas já ativas/pagas, ou instalações antigas de
        antes deste recurso existir).

        Fases possíveis:
        - "trial": ainda dentro do período de teste normal.
        - "grace": o teste acabou, mas ainda dentro da tolerância extra
          configurada em PlatformSettings.trial_grace_days — o lojista
          continua acessando o painel normalmente para regularizar.
        - "expired": tolerância também esgotada — na prática o job
          `flask process-trials` já deveria ter suspendido a conta
          (mudando o status para BLOCKED_PAYMENT) antes disso aparecer
          aqui; mantido como rede de segurança caso o job ainda não
          tenha rodado.

        Também inclui `is_urgent` (bool): quando True, o painel troca o
        aviso discreto por uma barra fixa e chamativa no topo (ver
        layouts/lojista_panel.html) — True durante toda a fase "grace"/
        "expired", e na fase "trial" só nos últimos
        `PlatformSettings.trial_urgent_days` dias, pra não assustar
        quem acabou de se cadastrar.
        """
        if self.status != TenantStatus.TRIAL or not self.trial_ends_at:
            return None

        from app.models.platform_settings import PlatformSettings

        now = now or datetime.now(timezone.utc)
        trial_ends_at = self.trial_ends_at
        if trial_ends_at.tzinfo is None:
            # SQLite não guarda de fato o timezone (mesmo com
            # DateTime(timezone=True)) — volta "naive" na leitura. Sempre
            # gravamos em UTC (ver AdminTenantService.create), então é
            # seguro assumir UTC aqui (mesmo padrão de session_service.py
            # e report_service.py).
            trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)
        settings = PlatformSettings.get_or_create()
        grace_days = settings.trial_grace_days
        grace_ends_at = trial_ends_at + timedelta(days=grace_days)

        if now <= trial_ends_at:
            days_left = max(0, math.ceil((trial_ends_at - now).total_seconds() / 86400))
            return {
                "phase": "trial", "days_left": days_left, "ends_at": trial_ends_at,
                "is_urgent": days_left <= settings.trial_urgent_days,
            }
        if now <= grace_ends_at:
            days_left = max(0, math.ceil((grace_ends_at - now).total_seconds() / 86400))
            return {
                "phase": "grace", "days_left": days_left, "grace_ends_at": grace_ends_at,
                "is_urgent": True, "is_final_day": grace_ends_at.date() == now.date(),
            }
        return {
            "phase": "expired", "days_left": 0, "grace_ends_at": grace_ends_at,
            "is_urgent": True, "is_final_day": False,
        }

    @property
    def owner(self):
        """O usuário com papel OWNER (lojista responsável) desta loja, ou
        None se por algum motivo não houver nenhum (não deveria acontecer
        em uso normal — toda loja criada por AdminTenantService.create()
        já vem com um OWNER). Import tardio de UserRole evita import
        circular (app/models/user.py importa Tenant)."""
        from app.models.user import UserRole

        for user in self.users:
            if user.role == UserRole.OWNER:
                return user
        return None

    @property
    def panel_theme(self) -> str | None:
        """Tema escolhido pelo lojista para a PRÓPRIA área de acesso
        (painel de gestão, em `theme_settings["panel_theme"]`) —
        sobrescreve, só para esta loja, o tema padrão definido pelo Super
        Admin para a plataforma inteira (PlatformSettings.system_theme).
        None (nunca customizado, ou valor desconhecido/obsoleto) = usa o
        padrão da plataforma — ver body_class de layouts/lojista_panel.html.
        Diferente de `public_theme_css_vars` (cor livre do CARDÁPIO
        PÚBLICO, logo abaixo): aqui é sempre uma das 4 paletas prontas
        do sistema, não uma cor livre."""
        from app.models.platform_settings import SYSTEM_THEME_CHOICES

        value = (self.theme_settings or {}).get("panel_theme")
        valid_keys = {key for key, _label in SYSTEM_THEME_CHOICES}
        return value if value in valid_keys else None

    @property
    def public_theme_css_vars(self) -> dict | None:
        """
        Variáveis CSS derivadas da cor de destaque escolhida pelo
        lojista para o CARDÁPIO PÚBLICO (armazenada em
        `theme_settings["accent"]`) — ou None se o lojista nunca
        customizou, caso em que o cardápio usa o âmbar padrão do
        template. Diferente do tema do painel administrativo
        (PlatformSettings.admin_theme_css_vars): esta cor é a
        identidade visual da LOJA para os clientes dela, não do painel
        interno de gestão.
        """
        accent = (self.theme_settings or {}).get("accent")
        if not accent:
            return None
        from app.utils.colors import darken_hex, hex_to_rgba_css

        return {
            "accent": accent,
            "accent_dark": darken_hex(accent),
            "accent_soft": hex_to_rgba_css(accent, 0.14),
        }

    @property
    def accepted_payment_methods(self) -> list:
        """
        Lista (na ordem de exibição do checkout) das formas de pagamento
        que esta loja aceita, no formato usado pelo cardápio público.
        Se o lojista desmarcar todas por engano, caímos de volta para
        Pix — sempre precisa sobrar pelo menos uma opção selecionável no
        checkout (o formulário de configurações também bloqueia isso,
        mas este é um segundo cinto de segurança no lado público).
        """
        options = [
            {"value": "pix", "label": "Pix", "enabled": self.accept_pix},
            {"value": "card", "label": "Cartão", "enabled": self.accept_card},
            {"value": "cash", "label": "Dinheiro", "enabled": self.accept_cash},
            {"value": "other", "label": "Outro", "enabled": self.accept_other},
        ]
        enabled = [{"value": o["value"], "label": o["label"]} for o in options if o["enabled"]]
        return enabled or [{"value": "pix", "label": "Pix"}]

    @property
    def has_opening_hours(self) -> bool:
        return bool(self.opening_hours)

    def opening_status(self, now: datetime | None = None) -> dict | None:
        """
        Calcula o status "aberto agora" / "fechado agora" a partir de
        `opening_hours`, usando o horário local do servidor (não há
        fuso horário por tenant nesta versão — para operação em mais
        de um fuso, considere adicionar um campo de timezone).

        Retorna None se a loja não configurou horário de funcionamento
        (nesse caso, o cardápio público simplesmente não mostra o
        badge de status). Caso contrário, retorna um dict:
            {"open": bool, "label": "Aberto agora · fecha às 23:00"}
        """
        if not self.opening_hours:
            return None

        now = now or datetime.now()
        today_key = WEEKDAY_KEYS[now.weekday()]
        today = self.opening_hours.get(today_key) or {}

        if today.get("closed") or not today.get("open") or not today.get("close"):
            next_label = self._next_opening_label(now)
            return {"open": False, "label": f"Fechado agora{next_label}"}

        try:
            open_t = time.fromisoformat(today["open"])
            close_t = time.fromisoformat(today["close"])
        except (ValueError, TypeError):
            return None

        current_t = now.time()
        # Horário "cruzando a meia-noite" (ex: abre 18:00, fecha 02:00)
        is_open = (open_t <= current_t <= close_t) if open_t <= close_t else (current_t >= open_t or current_t <= close_t)

        if is_open:
            return {"open": True, "label": f"Aberto agora · fecha às {today['close']}"}
        if current_t < open_t:
            return {"open": False, "label": f"Fechado agora · abre às {today['open']}"}
        return {"open": False, "label": f"Fechado agora{self._next_opening_label(now)}"}

    def _next_opening_label(self, now: datetime) -> str:
        """Procura o próximo dia (a partir de amanhã) com horário configurado."""
        for offset in range(1, 8):
            idx = (now.weekday() + offset) % 7
            day = self.opening_hours.get(WEEKDAY_KEYS[idx]) or {}
            if not day.get("closed") and day.get("open"):
                day_label = "amanhã" if offset == 1 else WEEKDAY_LABELS[WEEKDAY_KEYS[idx]]
                return f" · abre {day_label} às {day['open']}"
        return ""

    def __repr__(self):
        return f"<Tenant {self.slug} ({self.status})>"
