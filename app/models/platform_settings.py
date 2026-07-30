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

# Tema VISUAL UNIFICADO de todo o sistema (landing page pública + painel
# do lojista + painel do Super Admin) — escolhido pelo Super Admin em
# Configurações → Aparência (substituiu os 2 seletores que existiam
# antes, separados: "tema da landing page" e "cor de destaque do
# painel"). Cada opção é uma paleta completa (não só uma cor), definida
# em app/static/css/comanda.css como uma classe .theme-<chave> aplicada
# no <body> (ver body_class de layouts/admin_panel.html,
# layouts/lojista_panel.html, layouts/auth.html e marketing/landing.html).
# Modo claro/escuro continua sendo um eixo À PARTE (atributo
# data-theme="light" no <html>, alternado pelo próprio usuário) — os 4
# temas abaixo têm as duas variações prontas.
SYSTEM_THEME_CHOICES = [
    ("chili", "Picante (padrão)"),
    ("ocean", "Oceano"),
    ("forest", "Floresta"),
    ("midnight", "Meia-noite"),
]

# Cores usadas só para desenhar a prévia (swatch) de cada tema na tela
# de configuração — não afetam o CSS de verdade (esse vive em
# comanda.css); mantidas aqui, ao lado das choices, para não duplicar a
# lista de temas em dois lugares.
SYSTEM_THEME_SWATCHES = {
    "chili": "linear-gradient(135deg,#1A1612,#E54A36)",
    "ocean": "linear-gradient(135deg,#0B1220,#2E86AB)",
    "forest": "linear-gradient(135deg,#101A12,#3E8E5B)",
    "midnight": "linear-gradient(135deg,#0C0A16,#8B5CF6)",
}

# Só a cor de destaque (--accent) de cada tema — usada como atalho
# rápido de cor no seletor livre de accent_color do CARDÁPIO PÚBLICO de
# cada lojista (app/templates/lojista/settings/appearance.html): clicar
# num atalho preenche o seletor de cor com o valor abaixo, sem trocar
# de tela nem exigir um campo de formulário novo. Precisa bater com o
# --accent de cada classe .theme-<chave> em comanda.css.
SYSTEM_THEME_ACCENTS = {
    "chili": "#E54A36",
    "ocean": "#2E86AB",
    "forest": "#3E8E5B",
    "midnight": "#8B5CF6",
}

# Mantido só por compatibilidade com dados antigos já gravados em
# landing_content["theme"] (loja que nunca abrir a tela de Aparência de
# novo) — não é mais editável por nenhuma tela; o tema agora é sempre
# lido de `PlatformSettings.system_theme`.
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


# Texto padrão de Termos de Uso / Política de Privacidade — única fonte
# de verdade usada tanto pelas páginas públicas (marketing/terms.html,
# marketing/privacy.html) quanto para PRÉ-PREENCHER a tela de edição do
# Super Admin (app/controllers/admin/settings.py:settings_legal) com o
# texto efetivamente em vigor, mesmo antes de qualquer customização —
# sem isso, a tela de edição aparecia em branco até a primeira gravação,
# dando a impressão de só dar pra "inserir um texto novo" em vez de
# editar o que já estava publicado.
DEFAULT_TERMS_HTML = """<p>Estes Termos de Uso regulam o acesso e uso da plataforma Comanda ("Comanda", "plataforma", "nós"), um serviço de cardápio digital e gestão de pedidos via WhatsApp para estabelecimentos comerciais ("lojista", "loja"). Ao criar uma conta ou usar o cardápio público de uma loja hospedada na Comanda, você concorda com estes termos.</p>

<h2>1. O que é a Comanda</h2>
<p>A Comanda é uma plataforma de software (SaaS) que permite a um lojista criar e gerenciar um cardápio digital, receber pedidos de clientes finais e organizá-los em um painel de controle. A Comanda não é parte na relação comercial entre o lojista e seus clientes finais — não vendemos produtos, não processamos o pagamento dos pedidos e não somos responsáveis pela qualidade, preço, prazo ou entrega do que é vendido por cada loja.</p>

<h2>2. Cadastro do lojista</h2>
<ul>
  <li>O lojista é responsável por fornecer informações verdadeiras e mantê-las atualizadas (nome da loja, e-mail, telefone, endereço, horários de funcionamento e cardápio).</li>
  <li>O lojista é responsável pela guarda de suas credenciais de acesso e por toda atividade realizada em sua conta.</li>
  <li>É proibido usar a plataforma para fins ilícitos, para vender produtos proibidos por lei, ou para práticas enganosas com clientes finais.</li>
</ul>

<h2>3. Cardápio público e clientes finais</h2>
<p>Ao fazer um pedido pelo cardápio público de uma loja, o cliente final informa nome e telefone para identificação — esses dados pertencem ao contexto daquela loja específica e não são compartilhados com outras lojas da plataforma. O cliente final pode, a qualquer momento, consultar seus pedidos, editar seu cadastro ou solicitar a remoção da própria conta diretamente pelo cardápio ("Meus Dados"). Veja mais detalhes na nossa <a href="/politica-de-privacidade">Política de Privacidade</a>.</p>

<h2>4. Planos e pagamento da assinatura</h2>
<p>O uso da plataforma pelo lojista pode estar sujeito a um plano de assinatura pago, cobrado recorrentemente através do nosso parceiro de pagamentos (Asaas). O não pagamento de uma cobrança vencida pode resultar na suspensão temporária do acesso ao painel e/ou do cardápio público até a regularização. Cancelamentos podem ser solicitados a qualquer momento; o acesso permanece disponível até o fim do período já pago.</p>

<h2>5. Responsabilidades do lojista</h2>
<p>O lojista é o único responsável por: preços e disponibilidade dos produtos anunciados, atendimento e comunicação com seus clientes finais, cumprimento das obrigações fiscais e de consumo aplicáveis ao seu negócio, e pela confirmação/entrega dos pedidos recebidos. A Comanda apenas disponibiliza a ferramenta que organiza esse fluxo.</p>

<h2>6. Disponibilidade do serviço</h2>
<p>Fazemos esforços razoáveis para manter a plataforma disponível e funcionando corretamente, mas não garantimos operação ininterrupta ou livre de falhas. Manutenções programadas ou emergenciais podem causar indisponibilidade temporária.</p>

<h2>7. Propriedade intelectual</h2>
<p>O software, marca e identidade visual da Comanda são de nossa propriedade. O conteúdo do cardápio (fotos, descrições, preços, banners) cadastrado por cada lojista permanece de propriedade do respectivo lojista, que é responsável por ter os direitos necessários sobre o que publica.</p>

<h2>8. Suspensão e encerramento de conta</h2>
<p>Podemos suspender ou encerrar contas que violem estes termos, pratiquem fraude, ou deixem de pagar a assinatura por período prolongado, mediante aviso quando possível. O lojista pode encerrar sua conta a qualquer momento entrando em contato conosco.</p>

<h2>9. Alterações destes termos</h2>
<p>Podemos atualizar estes Termos de Uso periodicamente para refletir mudanças na plataforma ou na legislação aplicável. Alterações relevantes serão comunicadas por e-mail ou aviso no painel. O uso continuado da plataforma após uma atualização representa aceitação dos novos termos.</p>

<h2>10. Lei aplicável</h2>
<p>Estes termos são regidos pelas leis da República Federativa do Brasil.</p>

<h2>11. Contato</h2>
<p>Dúvidas sobre estes Termos de Uso podem ser enviadas para o e-mail de suporte informado no rodapé do site ou diretamente ao lojista responsável pela loja acessada, quando a dúvida for sobre um pedido específico.</p>"""

DEFAULT_PRIVACY_HTML = """<p>Esta Política de Privacidade explica como a Comanda coleta, usa e protege dados pessoais de lojistas (usuários do painel) e de clientes finais (visitantes do cardápio público), em conformidade com a Lei Geral de Proteção de Dados (Lei nº 13.709/2018 — LGPD).</p>

<h2>1. Quem somos e a quem esta política se aplica</h2>
<p>A Comanda é a operadora da plataforma. Esta política se aplica a duas categorias de titulares de dados: (a) lojistas e demais usuários do painel administrativo, e (b) clientes finais que fazem pedidos pelo cardápio público de uma loja.</p>

<h2>2. Dados que coletamos</h2>
<p><strong>Do lojista/usuário do painel:</strong> nome, e-mail, senha (armazenada apenas de forma criptografada, nunca em texto puro), dados da loja (nome, endereço, telefone/WhatsApp, horários, logotipo, cardápio) e dados necessários para cobrança da assinatura, processados pelo nosso parceiro de pagamentos (Asaas).</p>
<p><strong>Do cliente final:</strong> nome e telefone informados no checkout de um pedido, e, quando aplicável, endereço de entrega. Esses dados servem para identificar pedidos recorrentes de um mesmo cliente dentro de uma loja específica — nunca são combinados com o histórico do mesmo telefone em outra loja da plataforma.</p>

<h2>3. Para que usamos esses dados</h2>
<ul>
  <li>Processar e organizar os pedidos feitos no cardápio público.</li>
  <li>Permitir que o cliente final consulte seu histórico de pedidos e edite ou remova seu próprio cadastro.</li>
  <li>Gerar relatórios e estatísticas de vendas e clientes para o lojista (ex.: total de pedidos, ticket médio, clientes recorrentes).</li>
  <li>Viabilizar a comunicação do pedido via WhatsApp entre cliente final e lojista.</li>
  <li>Processar a cobrança da assinatura do lojista.</li>
  <li>Prevenir fraude e abuso (ex.: limites de tentativas de login).</li>
</ul>

<h2>4. Com quem compartilhamos dados</h2>
<p>Os dados de um cliente final ficam visíveis apenas para o lojista da loja onde o pedido foi feito — nunca para outros lojistas da plataforma. Compartilhamos dados de cobrança com nosso processador de pagamentos (Asaas) estritamente para viabilizar a cobrança da assinatura. Não vendemos nem alugamos dados pessoais a terceiros para fins de marketing.</p>

<h2>5. Seus direitos como titular de dados</h2>
<p>Você pode, a qualquer momento:</p>
<ul>
  <li><strong>Cliente final:</strong> acessar seu histórico de pedidos, corrigir seu nome/telefone, ou remover seu cadastro diretamente pelo cardápio público, na seção "Meus Dados". Pedidos já realizados permanecem no histórico do lojista (para fins fiscais e de gestão do negócio), mas perdem o vínculo com o cadastro removido.</li>
  <li><strong>Lojista:</strong> corrigir seus dados cadastrais pelo painel, ou solicitar o encerramento da conta e a exclusão dos dados da loja entrando em contato conosco.</li>
</ul>

<h2>6. Retenção de dados</h2>
<p>Mantemos os dados pelo tempo necessário para cumprir as finalidades descritas acima e obrigações legais/fiscais aplicáveis ao lojista. Após a remoção de uma conta (de cliente final ou de lojista), os dados pessoais diretamente identificáveis são removidos ou anonimizados, exceto quando a lei exigir sua conservação por período determinado.</p>

<h2>7. Segurança</h2>
<p>Adotamos medidas técnicas para proteger os dados armazenados, incluindo senhas com hash (nunca texto puro), conexão criptografada (HTTPS) e isolamento lógico entre os dados de cada loja — nenhum lojista tem acesso aos dados de clientes ou pedidos de outra loja.</p>

<h2>8. Cookies e sessão</h2>
<p>Usamos cookies estritamente necessários para manter sua sessão ativa (login do painel do lojista/Super Admin, e a identificação opcional do cliente final no cardápio público). Não usamos cookies de rastreamento publicitário.</p>

<h2>9. Menores de idade</h2>
<p>A plataforma não é direcionada a menores de idade para fins de cadastro como lojista. O uso do cardápio público por menores deve ocorrer sob supervisão de um responsável, conforme a política de cada estabelecimento.</p>

<h2>10. Alterações desta política</h2>
<p>Podemos atualizar esta Política de Privacidade periodicamente. A data da última atualização é exibida no topo desta página, e alterações relevantes serão comunicadas pelos canais habituais.</p>

<h2>11. Contato</h2>
<p>Para exercer seus direitos como titular de dados ou tirar dúvidas sobre esta política, entre em contato pelo e-mail de suporte informado no rodapé do site.</p>"""


class PlatformSettings(db.Model, TimestampMixin):
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True)

    asaas_api_key = Column(String(255), nullable=True)
    asaas_environment = Column(String(20), nullable=False, default="sandbox", server_default="sandbox")
    asaas_webhook_token = Column(String(255), nullable=True)
    # "PIX" por padrão — ver ASAAS_BILLING_TYPE_CHOICES acima.
    asaas_billing_type = Column(String(20), nullable=False, default="PIX", server_default="PIX")

    # Tema visual unificado do sistema inteiro — ver SYSTEM_THEME_CHOICES
    # acima. Controla a classe .theme-<chave> aplicada no <body> da
    # landing page, do painel do lojista e do painel do Super Admin.
    system_theme = Column(String(20), nullable=False, default="chili", server_default="chili")

    # Cor de destaque (accent) do painel administrativo (Super Admin +
    # lojista compartilham o mesmo design system "chili") — sobrescreve
    # SÓ a cor de destaque do tema escolhido acima, para quem quiser
    # ajustar ainda mais fino sem trocar de tema. Se vazio, usa a cor de
    # destaque padrão do tema selecionado.
    admin_theme_accent = Column(String(7), nullable=True)

    # Vídeo de fundo (em loop) das telas de autenticação — login,
    # cadastro de loja e recuperação de senha (app/templates/layouts/auth.html).
    # Opcional: sem vídeo enviado, essas telas continuam com o fundo
    # padrão (sólido), como sempre foi. Mesmo formato/validação de
    # landing_content["hero_video"] (ver app/utils/uploads.py:save_platform_video),
    # mas guardado num campo próprio (não é conteúdo editorial da
    # landing page, é decoração compartilhada por 4 telas diferentes).
    auth_background_video = Column(String(255), nullable=True)

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
    def system_theme_or_default(self) -> str:
        """Sempre retorna uma chave válida de SYSTEM_THEME_CHOICES — nunca
        None nem um valor obsoleto/desconhecido (ex.: linha antiga sem a
        coluna preenchida, migração parcial etc.), então os templates
        podem usar direto na classe .theme-<valor> sem checagem extra."""
        valid_keys = {key for key, _label in SYSTEM_THEME_CHOICES}
        return self.system_theme if self.system_theme in valid_keys else "chili"

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
    def terms_content_or_default(self) -> str:
        return self.terms_content or DEFAULT_TERMS_HTML

    @property
    def privacy_content_or_default(self) -> str:
        return self.privacy_content or DEFAULT_PRIVACY_HTML

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
