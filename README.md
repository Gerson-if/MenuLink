# Menu Link — Plataforma Multi-Tenant de Cardápio Digital

![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-produção-336791?logo=postgresql&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-desenvolvimento-003B57?logo=sqlite&logoColor=white)
![Testes](https://img.shields.io/badge/testes-pytest-0A9EDC?logo=pytest&logoColor=white)
![Licença](https://img.shields.io/badge/licença-proprietária-lightgrey)

Menu Link é uma plataforma multi-tenant de cardápio digital: um Super
Administrador gerencia a plataforma e os planos, e cada Lojista tem seu
próprio cardápio, produtos, pedidos e clientes, totalmente isolados dos
demais.

## Índice

- [Stack](#stack)
- [Funcionalidades](#funcionalidades)
  - [Autenticação e multi-tenant](#autenticação-e-multi-tenant)
  - [Painel do lojista](#painel-do-lojista)
  - [Cardápio público e checkout](#cardápio-público-e-checkout)
  - [Pedidos, vendas e relatórios financeiros](#pedidos-vendas-e-relatórios-financeiros)
  - [Painel do Super Admin](#painel-do-super-admin)
  - [Integração com Asaas (cobrança de faturas)](#integração-com-asaas-cobrança-de-faturas)
- [Identidade visual](#identidade-visual)
- [Arquitetura](#arquitetura)
- [Estratégia multi-tenant](#estratégia-multi-tenant)
- [Testes automatizados](#testes-automatizados)
- [Como rodar localmente (SQLite)](#como-rodar-localmente-sqlite)
- [Deploy em produção](#deploy-em-produção)
  - [Requisitos](#requisitos)
  - [Instalação guiada](#instalação-guiada)
  - [Atualizando para uma versão mais nova](#atualizando-para-uma-versão-mais-nova)
  - [Backup e restauração (migração entre servidores)](#backup-e-restauração-migração-entre-servidores)
  - [Detalhes técnicos do que o instalador configura](#detalhes-técnicos-do-que-o-instalador-configura)
  - [Checklist de variáveis de ambiente (`.env`)](#checklist-de-variáveis-de-ambiente-env)
  - [Verificando que subiu certo](#verificando-que-subiu-certo)
- [Comandos úteis](#comandos-úteis)
- [Validações de dados](#validações-de-dados)
- [Fora do escopo atual](#fora-do-escopo-atual)
- [Licença](#licença)

## Stack

- **Backend**: Flask, SQLAlchemy, Alembic (migrations), Marshmallow
  (validação), Flask-Login, Flask-WTF (CSRF), Flask-Limiter (rate
  limiting).
- **Frontend**: Bootstrap 5 + Bootstrap Icons (identidade visual
  "Menu Link"), Alpine.js (estado reativo: carrinho, checkout, sidebar) e
  HTMX (atualizações parciais de página sem recarregar). Todos os
  assets críticos (Bootstrap, Alpine.js, HTMX, Chart.js) são servidos
  localmente pelo próprio Flask, sem dependência de CDN externo para a
  aplicação renderizar.
- **Banco de dados**: SQLite em desenvolvimento, PostgreSQL recomendado
  em produção.

[⬆ voltar ao índice](#índice)

## Funcionalidades

### Autenticação e multi-tenant

- Login único por e-mail/senha para Super Admin e Lojista, com
  contexto multi-tenant resolvido automaticamente e autorização por
  papel (`@super_admin_required` / `@lojista_required`, bloqueando com
  403 o acesso cruzado entre papéis).
- **Bloqueio de contas suspensas ou inadimplentes.** Suspensa/cancelada
  não consegue logar. Bloqueada por inadimplência (`BLOCKED_PAYMENT`) é
  um caso à parte: o login É permitido, mas cai direto numa página
  restrita só com as faturas em aberto (com link "Pagar agora" quando
  há cobrança Asaas gerada) e um atalho pro WhatsApp de suporte — sem
  acessar o resto do painel — pra conseguir regularizar sozinha, sem
  precisar acionar o suporte. O cardápio público da loja mostra uma
  página amigável de "indisponível no momento" nesse caso, sem revelar
  o motivo ao cliente final (ver `public/store_unavailable.html`).
- **Segurança da própria conta** (`/configuracoes/segurança`, tanto
  para o lojista quanto para o Super Admin): trocar a senha (exige a
  atual), editar nome/e-mail de login, e ver/encerrar sessões ativas
  (com IP, dispositivo e data do último acesso) — encerrar uma sessão
  específica ou "todas as outras de uma vez" derruba o acesso
  correspondente no próximo request dele (`UserSession` +
  `session_guard.py`, checado a cada requisição autenticada). Trocar a
  senha já encerra automaticamente todas as outras sessões sozinho.
- CSRF em todos os formulários e no endpoint JSON de checkout.
- Rate limiting no login, cadastro, recuperação e troca de senha (10
  tentativas por minuto por IP).
- Proteção contra *open redirect* no parâmetro `?next=` do login — só
  aceita caminhos internos (`/algo`), nunca uma URL completa ou
  protocol-relative (`//site.com`) que mandaria o usuário, já
  autenticado de verdade, pra um site de phishing.
- Validação de e-mail/senha protegida contra *ReDoS*: um valor
  absurdamente longo em qualquer um dos dois campos é recusado
  ANTES do validador de e-mail rodar (`bounded_length`, ver
  `app/utils/validators.py`) — sem isso, colar um texto muito grande no
  campo de e-mail deixava a validação visivelmente lenta (quase O(n²)).
- **Auto-cadastro público do lojista** (`/cadastro`): qualquer pessoa
  cria sua própria loja (status `trial`) e já entra logada no painel,
  com checagem de disponibilidade do endereço do cardápio em tempo
  real via HTMX.
- **Recuperação de senha** (`/recuperar-senha` → `/redefinir-senha/<token>`)
  com token stateless via `itsdangerous` (sem tabela extra no banco): o
  token embute o ID do usuário e um fragmento do hash da senha atual, e
  se invalida sozinho assim que a senha é trocada. A mensagem exibida
  ao usuário é sempre a mesma, exista ou não o e-mail, evitando
  enumeração de contas. Como o projeto não inclui serviço de e-mail
  configurado, o link é registrado no log da aplicação
  (`app/utils/mailer.py`), com um comentário indicando onde plugar um
  envio real (Flask-Mail/SendGrid/SES) em produção.

### Painel do lojista

- **Categorias**: criar, listar, editar, excluir — excluir uma
  categoria não apaga os produtos dela, eles ficam "sem categoria".
- **Produtos**: criar, listar (com filtro por categoria), editar,
  excluir. Preço digitado em reais (`94.90`) é convertido e armazenado
  em centavos (`9490`).
- **Variações e complementos de produto** (ex.: Tamanho, Molhos):
  grupos configuráveis como variação obrigatória ou complemento
  opcional, com escolha única ou múltipla, e opções com preço
  adicional — editados direto na página do produto, atualizando só
  aquele bloco via HTMX.
- **Imagens**: upload com validação real de conteúdo (Pillow),
  redimensionamento automático (máx. 1600px), armazenamento isolado por
  tenant em disco, definição de imagem principal, remoção.
- **Banners/carrossel promocional**: CRUD completo no painel, exibido
  automaticamente no topo do cardápio público quando há banners
  ativos.
- **Preço de custo e margem**: campo opcional no produto; quando
  preenchido, a margem (%) e o lucro por unidade são calculados e
  mostrados ao vivo enquanto o lojista digita preço de venda e custo.
- **QR Code do cardápio**, gerado localmente (sem API externa) a
  partir da URL pública da loja.
- **Configurações da loja** organizadas em abas: dados da loja (nome,
  **legenda do cardápio público** — substitui o texto fixo "CARDÁPIO
  DIGITAL" mostrado ao lado do nome da loja, ex: "Pizzas Artesanais" —,
  WhatsApp, logo), cardápio/link (slug editável, retirada/entrega),
  checkout (taxa de entrega, com isenção configurável acima de um
  valor mínimo), assinatura (plano atual, ciclo de cobrança, faturas
  com link de pagamento quando há cobrança Asaas gerada, e atalho pro
  WhatsApp de suporte) e segurança (senha, dados de login, sessões
  ativas).
- **Escolher/trocar de plano pelo próprio painel** (aba Assinatura):
  toda loja nova (self-service ou criada pelo Super Admin sem plano
  escolhido) já cai automaticamente no plano mais barato disponível —
  nenhuma loja fica com plano nulo. O lojista pode trocar para
  qualquer outro plano ativo a qualquer momento; a troca só é aplicada
  depois que ele confirma num modal (nunca automaticamente), e a tela
  deixa explícito que só associar/trocar de plano não gera cobrança
  nenhuma — a fatura em si continua sendo lançada separadamente.
- **Período de teste grátis**: toda loja nova começa em teste (duração
  configurável pelo Super Admin). O painel mostra quantos dias faltam
  e, quando o teste acaba, a loja ainda acessa normalmente por mais
  alguns dias de tolerância para regularizar — só depois disso o
  acesso é suspenso (ver detalhes na seção do Super Admin abaixo).
- **Limites de plano**: `Plan.max_categories` / `Plan.max_products`
  checados na criação.
- **Link do cardápio** exibido no dashboard com botão "Copiar link".
- **Suporte** (`/painel/suporte`): chat direto com o Super Admin, um
  fio de conversa por loja — mensagem nova aparece com um selo na
  sidebar e a lista atualiza sozinha (polling HTMX a cada 5s, sem
  WebSocket). O Super Admin responde pela própria tela de detalhe do
  lojista (`/admin/lojistas/<id>`), que também sinaliza mensagem nova
  na listagem de lojistas.

### Cardápio público e checkout

- **Página do cardápio** (`/loja/<slug>`): categorias e produtos
  ativos, organizados em seções com navegação rápida, carrinho
  flutuante e modais de carrinho/checkout, tudo em uma única página
  (Alpine.js), sem recarregar entre categorias.
- **Experiência mobile**: botão de carrinho discreto (ícone + badge, em
  vez de uma barra larga) que só aparece quando há itens; categorias
  sempre visíveis, sem rolagem horizontal; adicionar um produto não
  abre o carrinho automaticamente, só atualiza o contador; e o botão
  "voltar" do navegador fecha carrinho/modais/barra lateral em vez de
  sair do cardápio.
- **Modal de produto** com imagem, descrição, grupos de variação e
  complementos, com o preço final recalculado ao vivo. O carrinho é
  indexado por produto + combinação de opções escolhidas, então dois
  lançamentos do mesmo produto com opções diferentes viram linhas
  separadas.
- **Checkout** (`POST /loja/<slug>/pedido`, JSON): valida o payload com
  Marshmallow, cria o pedido no banco e retorna o link do WhatsApp.
  Regras aplicadas no `OrderService`:
  - O preço nunca vem do cliente — o carrinho envia só `product_id` +
    `quantity`; o preço usado é sempre o preço atual do produto no
    banco.
  - O produto precisa pertencer ao tenant do slug acessado e estar
    ativo.
  - Grupos/opções de variação e complemento são revalidados no
    servidor (`OrderService._resolve_options`): grupo obrigatório sem
    escolha é rejeitado, grupo de escolha única com mais de uma opção
    é rejeitado, e uma opção que não pertence àquele produto/tenant é
    rejeitada.
  - Entrega exige rua, número e bairro, reforçado tanto no schema
    quanto em `CHECK CONSTRAINT` no banco.
  - Respeita `Tenant.delivery_enabled` / `pickup_enabled` e
    `Tenant.min_order_cents`, quando configurados.
  - Cliente final é reconhecido pelo telefone — pedidos repetidos do
    mesmo número reaproveitam o mesmo `Customer`.
  - `order_number` sequencial por loja, com nova tentativa automática
    em caso de colisão sob concorrência.
- **WhatsApp**: `app/utils/whatsapp.py` monta uma mensagem formatada
  com itens, subtotal, taxa de entrega, endereço (se aplicável), forma
  de pagamento e observações, e gera o link `https://wa.me/<numero>?text=<mensagem>`.
  O envio é feito pelo navegador do cliente, sem depender de
  credenciais de API externa.

### Pedidos, vendas e relatórios financeiros

- **Gestão de pedidos** (`/painel/pedidos`): listagem paginada com
  filtro por status, e página de detalhe com itens, endereço de
  entrega (ou aviso de retirada), forma de pagamento e observações.
- **Fluxo de status validado no servidor**: `OrderService.update_status`
  valida contra um mapa de transições permitidas (`pending → confirmed
  → preparing → out_for_delivery/ready_for_pickup → completed`, com
  `canceled` disponível em qualquer ponto antes de `completed`). Pular
  etapas é rejeitado mesmo via requisição direta. Também é possível
  aceitar/rejeitar um pedido novo com um clique e reverter um passo, com
  um mapa de reversão próprio.
- **Vendas e relatórios** (`/painel/vendas`): receita de hoje, da
  semana, do mês e histórico total; contagem de pedidos por status;
  ranking dos produtos mais vendidos (quantidade e receita); gráfico de
  receita dos últimos 14 dias (Chart.js). Pedidos cancelados são
  excluídos do cálculo de receita.
- Todos os números são calculados a partir do tenant da sessão, nunca
  de um parâmetro de URL.

### Painel do Super Admin

- **Gestão de lojistas**: criar (já com o usuário lojista/dono, pronto
  para logar), listar com busca por nome/e-mail/slug e filtro por
  status, editar dados, excluir permanentemente (com cascade real no
  banco).
- **Transições de status da conta**, cada uma com motivo opcional
  registrado (`Tenant.blocked_reason` / `blocked_at`): ativar,
  suspender, bloquear por inadimplência (`TenantStatus.BLOCKED_PAYMENT`
  — o lojista continua conseguindo logar, só que confinado a uma
  página de regularização de pendências; o cardápio público mostra uma
  mensagem amigável de indisponibilidade, sem expor o motivo) e
  cancelar.
- **Planos**: CRUD completo (nome, preço, ciclo mensal/anual, limites
  de categorias/produtos/imagens), com ativar/desativar via clique no
  badge (HTMX).
- **Cobrança e recorrência**: fatura manual (valor e vencimento livres,
  sempre disponível) ou **"Gerar próxima fatura automaticamente"** —
  calcula o valor a partir do preço atual do plano e o vencimento a
  partir do ciclo da assinatura (respeitando calendário de verdade:
  "mensal" a partir de 31/jan cai em 28 ou 29/fev, não em 3/mar),
  recusando gerar se já existe uma fatura em aberto ou se o plano não
  tem preço definido. O **ciclo de cobrança é por assinatura** (não só
  por plano): o Super Admin pode sobrescrever mensal/anual pra uma loja
  específica sem mudar o plano dela nem afetar as demais. Uma
  `Subscription` ativa é criada automaticamente na primeira fatura.
  Marcar uma fatura como paga libera automaticamente um lojista
  bloqueado por inadimplência.
- **Aparência da plataforma** (`/admin/configuracoes/aparencia`): cor
  de destaque do back-office, logo do sistema (aparece na landing page,
  no favicon e nas telas de login/cadastro/recuperação de senha — nunca
  no painel do lojista nem no cardápio de cada loja, que têm identidade
  própria) e o WhatsApp de suporte mostrado ao lojista (assinatura e
  página de conta bloqueada).
- **Landing page** (`/admin/configuracoes/landing`): além dos textos e
  dos "3 destaques" já existentes, a arte de destaque do hero agora tem
  controles de opacidade e desfoque com pré-visualização ao vivo.
- **Período de teste grátis** (`/admin/configuracoes/teste-gratis`):
  duração do teste (padrão 20 dias), dias extras de tolerância após o
  teste acabar (padrão 2 dias — a loja continua acessando o painel
  normalmente nesse período, só para regularizar a situação), a partir
  de quantos dias restantes o painel do lojista fica com aviso
  chamativo (padrão 3 dias), e o lembrete automático mandado pelo chat
  de suporte da própria loja conforme o prazo se aproxima (com quantos
  dias de antecedência e a cada quantos dias repetir, tudo
  configurável, além do texto da mensagem). Além desse lembrete
  configurável, uma **régua de comunicação fixa** manda dois avisos
  extras com texto padronizado (não editável): um quando faltam 2 dias
  para o fim do teste ("seu cardápio está ficando incrível, mas seu
  teste acaba em 2 dias...") e outro no próprio dia em que o link sai
  do ar, ao fim da tolerância ("seu link sairá do ar hoje à
  meia-noite..."). Ao esgotar a tolerância sem regularizar, a loja é
  suspensa automaticamente (mesmo status/tela de "conta bloqueada" já
  usado para inadimplência). O ajuste de duração só vale para lojas
  criadas dali em diante — para estender o prazo de uma loja
  específica já existente, use "Ajustar prazo de teste" no detalhe
  dela (`/admin/lojistas/<id>`). Como não há agendador embutido na
  aplicação, quem processa lembretes/régua fixa/tolerância/suspensão é
  o comando `flask process-trials` (ver
  [Comandos úteis](#comandos-úteis)) — programe-o para rodar 1x por
  dia num cron do servidor:
  ```bash
  # roda todo dia às 6h da manhã
  0 6 * * * root cd /opt/menulink && venv/bin/flask process-trials >> /var/log/menulink-trials.log 2>&1
  ```
  No painel do lojista, o aviso fica discreto (uma linha pequena)
  durante a maior parte do teste, e vira uma barra fixa e chamativa no
  topo só nos últimos dias e durante a tolerância — pedindo pra
  assinar um plano, sem deixar isso passar despercebido perto do
  prazo.
- **Confirmação de plano ainda durante o trial**: se o lojista escolher
  um plano pago (`/painel/configuracoes/assinatura`) antes do teste
  acabar, o sistema só registra a intenção (`Tenant.plan_confirmed_at`)
  — nenhuma assinatura ou cobrança nasce na hora. Quando `flask
  process-trials` detecta que o teste acabou e há um plano confirmado,
  ele ativa a loja (`ACTIVE`) e cria a assinatura com o primeiro ciclo
  começando exatamente no fim do trial, não no dia em que o job rodou:
  o lojista não perde nenhum dia grátis. Sem confirmação, a loja segue
  a régua normal de tolerância/suspensão.
- **Upgrade e downgrade de plano** (mesma tela): um upgrade (plano mais
  caro) é liberado imediatamente e gera uma fatura extra, prorateada
  pela fração de dias que ainda faltam no ciclo atual (só a diferença
  de valor entre os dois planos, não o preço cheio do novo). Um
  downgrade (plano mais barato) exige que o uso atual já caiba nos
  limites do plano menor — senão pede pra excluir produtos/categorias
  primeiro — e, se couber, nunca gera estorno em dinheiro: a troca só
  entra em vigor no fim do ciclo já pago (o lojista continua no plano
  atual até lá), aplicada automaticamente na próxima fatura gerada por
  `generate_next_invoice`.
- **Dunning (fatura vencida)**: o comando `flask process-dunning` marca
  como vencida (`OVERDUE`) toda fatura pendente cujo vencimento já
  passou e bloqueia (`BLOCKED_PAYMENT`) a loja cuja fatura estiver
  vencida há mais dias do que a tolerância configurada
  (`PlatformSettings.dunning_grace_days`, padrão 3 dias) sem
  pagamento. Programe também para rodar 1x por dia num cron do
  servidor:
  ```bash
  0 7 * * * root cd /opt/menulink && venv/bin/flask process-dunning >> /var/log/menulink-dunning.log 2>&1
  ```
- Todas as rotas protegidas por `@super_admin_required`.

### Integração com Asaas (cobrança de faturas)

Integração opcional com o [Asaas](https://www.asaas.com) para gerar
cobrança real (boleto/Pix/cartão) e liberar a loja automaticamente
quando o pagamento é confirmado.

> O código segue a estrutura pública documentada da API v3 do Asaas
> (endpoints, payload, nomes de campo). Os testes automatizados cobrem
> que o código monta a requisição e trata a resposta corretamente via
> mocks, não que a API do Asaas se comporta assim em produção — valide
> contra o ambiente sandbox do Asaas com uma chave de teste antes de
> usar em produção.

Como funciona:

- **Desligado por padrão.** Sem chave de API configurada, o fluxo
  manual de fatura continua como era.
- **Como ligar**: o jeito recomendado é pelo próprio painel, em
  **Super Admin → Cobrança (Asaas)** (`/admin/configuracoes/asaas`) —
  cadastre a chave de API, escolha o ambiente (sandbox/produção) e
  gere o token de webhook por lá, sem precisar mexer no servidor. A
  tela também tem um botão "Testar conexão" para validar a chave antes
  de contar com a integração, e avisa explicitamente quando a chave
  está configurada mas o webhook não (situação que impede a liberação
  automática do lojista). Também é possível configurar via variáveis
  de ambiente (`ASAAS_API_KEY`, `ASAAS_ENVIRONMENT`,
  `ASAAS_WEBHOOK_TOKEN`) — os valores do painel têm prioridade quando
  ambos existem.
- Com a chave configurada, cada fatura pendente na tela de detalhe do
  lojista (`/admin/lojistas/<id>`) ganha um botão "Gerar cobrança
  Asaas", que cria (ou reaproveita) um cliente no Asaas vinculado ao
  tenant e gera uma cobrança com Pix/boleto/cartão.
- **Pix é a forma de cobrança padrão** (`PlatformSettings.asaas_billing_type`,
  configurável em **Super Admin → Cobrança (Asaas)**) — por decisão de
  produto, o Menu Link prioriza Pix: o Asaas confirma o pagamento quase
  em tempo real (webhook `PAYMENT_RECEIVED`), liberando a loja na hora,
  sem depender de compensação bancária (boleto) nem de disputa de
  chargeback (cartão). As outras opções (cliente escolhe entre
  Pix/boleto/cartão, só boleto, ou só cartão) ficam disponíveis pra
  quando/se a plataforma decidir habilitá-las — trocar não exige
  nenhuma mudança de código, só selecionar na tela.
- **Validações antes de chamar o Asaas** (evitam um erro genérico da
  API — ou pior, uma cobrança quebrada — por um problema que dava pra
  pegar antes): recusa se a fatura já tem cobrança gerada, se não está
  pendente/vencida (ex: já paga ou cancelada), se o vencimento já
  passou (o Asaas não aceita data retroativa), se a loja não tem e-mail
  cadastrado, ou se a loja não tem CPF/CNPJ cadastrado (`Tenant.document`,
  editável pelo próprio lojista em Configurações da loja — ambos
  exigidos pelo Asaas pra criar o cliente de cobrança). O valor
  lançado manualmente também tem limites sãos (entre R$ 0,01 e R$
  100.000,00, vencimento até 2 anos à frente) pra pegar erro de
  digitação antes de virar cobrança real.
- **Webhook de confirmação**: configure no painel do Asaas a URL
  `https://SEU-DOMINIO/webhooks/asaas` com o token gerado na tela de
  configurações (ou definido em `ASAAS_WEBHOOK_TOKEN`), para que
  ninguém consiga forjar uma notificação de pagamento confirmado. Sem
  token configurado, o endpoint recusa qualquer chamada (503). Quando
  o Asaas confirma o pagamento, o webhook aciona a mesma lógica de
  "marcar como paga" — a loja bloqueada por inadimplência é liberada
  automaticamente.

Arquitetura da integração:

```
app/services/payment_gateway/
├── base.py            # contrato abstrato (facilita trocar/adicionar gateway no futuro)
├── asaas_gateway.py    # implementação real (requests → API do Asaas)
└── __init__.py         # get_gateway() — retorna None se não configurado
app/controllers/webhooks_controller.py   # POST /webhooks/asaas
```

`AdminBillingService.generate_asaas_charge(invoice)` orquestra:
cria/reaproveita o cliente Asaas do tenant → cria a cobrança → salva
`asaas_payment_id` e `payment_link_url` na fatura.
`mark_paid_by_asaas_payment_id` é chamado pelo webhook para casar a
notificação com a fatura certa.

[⬆ voltar ao índice](#índice)

## Identidade visual

Duas identidades visuais, cada uma customizável em tempo de execução
(sem precisar editar CSS nem redeployar):

- **`.theme-chili`** (vermelho-chili `#E54A36` por padrão) — marketing
  (landing, login, cadastro, recuperação de senha), painel do lojista
  **e** painel do Super Admin, todos compartilhando os mesmos
  componentes (`app/static/css/comanda.css`: `.panel`, `.kpi-card`,
  `.status-badge`, `.sidebar`). Os dois painéis internos usam a mesma
  identidade de marca — o Super Admin pode trocar a cor de destaque em
  **Configurações → Aparência** (`PlatformSettings.admin_theme_accent`),
  o que atualiza o tema tanto do seu próprio painel quanto do painel de
  todos os lojistas (é uma identidade de back-office compartilhada).
- **Cardápio público** (`store_menu.html`) — paleta âmbar/terracota
  própria (`#E8A33D` por padrão), tipografia serifada (Fraunces),
  pensada para parecer um cardápio de restaurante de verdade. Cada
  lojista escolhe sua própria cor de destaque em **Configurações →
  Aparência** no painel dele (`Tenant.theme_settings["accent"]`) — essa
  cor é só do cardápio daquela loja, independente da cor do painel
  administrativo.

Tipografia: Archivo Black, Inter e JetBrains Mono para o tema
"Menu Link"; Fraunces + Plus Jakarta Sans para o cardápio público. Todas
auto-hospedadas (`app/static/vendor/fonts/`), sem depender do Google
Fonts em tempo de execução.

`app/templates/layouts/lojista_panel.html` e `admin_panel.html`
implementam a mesma casca (sidebar retrátil, colapsável em desktop,
drawer em mobile) parametrizada por tema. Toda página de painel
estende um desses layouts e só preenche `page_title`/`panel_content`.

[⬆ voltar ao índice](#índice)

## Arquitetura

```
app/
├── config.py              # Config por ambiente (Development/Testing/Production)
├── extensions.py           # db, migrate, login_manager, bcrypt, csrf, ma, limiter
├── cli.py                  # Comandos flask (seed-db, create-admin)
├── models/                  # Camada de dados (SQLAlchemy)
├── repositories/            # Acesso a dados — isolamento multi-tenant centralizado aqui
│   ├── base_repository.py    # BaseRepository / TenantScopedRepository
│   ├── user_repository.py
│   ├── category_repository.py
│   ├── product_repository.py
│   ├── product_image_repository.py
│   ├── order_repository.py     # geração de order_number sequencial por tenant
│   └── customer_repository.py  # busca/reaproveita cliente por telefone
├── services/                 # Regras de negócio
│   ├── auth_service.py
│   ├── category_service.py
│   ├── product_service.py
│   ├── order_service.py       # preço sempre do servidor, regras de entrega/mínimo
│   └── payment_gateway/        # integração Asaas
├── schemas/
│   └── checkout_schema.py     # validação Marshmallow do payload de checkout
├── forms/                     # Formulários HTML com CSRF (Flask-WTF)
├── controllers/
│   ├── auth_controller.py
│   ├── admin/                    # painel do Super Admin
│   ├── lojista/                  # painel do lojista, um blueprint em vários módulos
│   ├── public_controller.py    # cardápio público + endpoint de checkout (JSON)
│   └── webhooks_controller.py  # webhook de confirmação de pagamento (Asaas)
├── utils/
│   ├── tenant_context.py     # Resolução do tenant atual (g.tenant)
│   ├── decorators.py          # @super_admin_required, @lojista_required
│   ├── slugs.py                # slug único por tenant
│   ├── uploads.py              # validação/redimensionamento de imagens (Pillow)
│   └── whatsapp.py             # monta mensagem do pedido + link wa.me
├── static/{css,uploads}/
└── templates/
    └── public/store_menu.html    # cardápio + carrinho + checkout (Alpine.js)
tests/                          # pytest — auth, multi-tenant, CRUD, checkout, WhatsApp
```

**Camadas**: Controller (rota) → Service (regra de negócio) → Repository
(query) → Model (tabela). Controllers nunca acessam o banco diretamente.

[⬆ voltar ao índice](#índice)

## Estratégia multi-tenant

Banco compartilhado, schema compartilhado, isolamento por coluna
`tenant_id` (`TenantScopedMixin`). Reforçado em código por
`TenantScopedRepository` (exige `tenant_id` no construtor) e, na camada
de requisição, por `app/utils/tenant_context.py`:

- **Lojista autenticado**: `g.tenant` é resolvido automaticamente a
  partir de `current_user.tenant` em todo request (`before_request`).
- **Visitante do cardápio público** (`/loja/<slug>`): `g.tenant` é
  resolvido pelo slug da URL. Lojas com conta suspensa, bloqueada por
  inadimplência ou cancelada retornam 404.

`get_current_tenant()` é o único ponto de leitura do tenant atual usado
pelo resto da aplicação.

[⬆ voltar ao índice](#índice)

## Testes automatizados

```bash
pytest tests/ -v
```

A suíte cobre, entre outros pontos: login válido/inválido e
redirecionamento por papel; bloqueio de acesso cruzado (403) e de
conta por inadimplência; resolução multi-tenant por slug; CRUD de
categorias e produtos; conversão de preço para centavos; upload de
imagem válida/inválida; isolamento entre lojistas; limites de plano;
checkout público (preço sempre do servidor mesmo com payload
manipulado, numeração sequencial por loja, validação de endereço,
regras de entrega/retirada, pedido mínimo, produto de outro tenant
rejeitado, link do WhatsApp, cliente recorrente reaproveitado);
interações HTMX; painel do Super Admin (CRUD de lojistas e planos,
transições de status, ciclo completo de bloqueio/fatura/liberação);
pedidos e relatórios (transições de status, cálculo de receita,
ranking de produtos); auto-cadastro e recuperação de senha; CRUD de
banners e de grupos/opções de complemento; cálculo de margem; recorrência
de cobrança (cálculo de ciclo mensal/anual respeitando calendário,
geração automática da próxima fatura e suas validações, validações
extras antes de gerar cobrança Asaas); conta bloqueada por
inadimplência (login permitido só até a página de pendências, página
amigável no cardápio público); segurança de sessão (rastreamento e
revogação de sessões ativas, troca de senha derrubando as demais
sessões, proteção contra open redirect no `?next=` do login); chat
lojista↔Super Admin; e testes de produção (assets críticos servidos
localmente sem depender de CDN, variáveis de ambiente obrigatórias
validadas, QR Code gerado localmente).

[⬆ voltar ao índice](#índice)

## Como rodar localmente (SQLite)

> **Dependência de sistema — `ffmpeg`**: o upload de vídeo de fundo da
> landing page (Super Admin → Landing Page → Vídeo de fundo do hero) é
> recomprimido via o binário `ffmpeg` (não é `pip install`, precisa
> estar no `PATH`) — sem isso o navegador só consegue começar a tocar o
> vídeo depois de baixá-lo inteiro, o que parece "travado" em conexões
> mais lentas, e um `.webm` sem recompressão simplesmente **não toca**
> no Safari/iPhone/iPad (sem suporte nativo a WebM). Por isso:
> - Um `.webm` enviado sem `ffmpeg` disponível é **recusado** (peça pra
>   instalar o `ffmpeg` ou envie `.mp4` diretamente).
> - Um `.mp4` enviado sem `ffmpeg` é aceito sem recomprimir, mas o
>   Super Admin recebe um aviso na hora avisando que o vídeo não foi
>   otimizado.
>
> `deploy/install.sh` já instala `ffmpeg` automaticamente em servidores
> novos. Localmente, instale o pacote da distribuição (ex:
> `apt install ffmpeg`, ou `choco install ffmpeg` no Windows) — sem ele,
> o upload de vídeo na landing continua funcionando dentro das
> limitações acima, nada quebra.

```bash
# 1. Ambiente virtual
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Dependências
pip install -r requirements.txt

# 3. Variáveis de ambiente (.flaskenv já define FLASK_APP/FLASK_ENV)
cp .env.example .env

# 4. Banco de dados (SQLite, criado em instance/dev.db)
flask db upgrade

# 5. Dados iniciais (super admin + plano + loja demo)
flask seed-db

# 6. Rodar
flask run
```

Acesse `http://127.0.0.1:5000/` para ver a landing page pública, ou vá
direto para `http://127.0.0.1:5000/login` com:

| Papel        | E-mail                          | Senha       |
|--------------|----------------------------------|-------------|
| Super Admin  | admin@cardapio.saas              | admin123    |
| Lojista      | lojista@braseiroecia.com.br      | lojista123  |

Cardápio público de demonstração: `http://127.0.0.1:5000/loja/braseiro-cia`
(crie uma categoria e um produto pelo painel do lojista antes, para ver
o cardápio populado).

Você também pode criar sua própria loja sem passar pelo Super Admin,
via auto-cadastro: `http://127.0.0.1:5000/cadastro` — o endereço do
cardápio é validado em tempo real enquanto você digita.

Para testar a recuperação de senha (`/recuperar-senha`), como não há
serviço de e-mail configurado em desenvolvimento, o link de redefinição
aparece no console/log do `flask run`, não na tela.

Como Super Admin (`admin@cardapio.saas`), acesse `/admin/lojistas` para
criar novos lojistas (o formulário já cria a loja e o usuário de login
dele juntos), gerenciar planos em `/admin/planos`, e usar as ações de
bloquear/suspender/ativar/cancelar e lançar faturas na página de
detalhe de cada lojista.

Troque essas senhas antes de qualquer uso além de desenvolvimento
local.

[⬆ voltar ao índice](#índice)

## Deploy em produção

O jeito recomendado de colocar o Menu Link no ar é um VPS Ubuntu comum
(sem Docker), usando o instalador guiado em `deploy/install.sh`. Ele
cuida de tudo: dependências de sistema, banco de dados, ambiente
virtual, migrations, serviço systemd, Nginx e, se você tiver um
domínio, certificado SSL gratuito com renovação automática.

> **Nota sobre nomes**: o produto se chama "Menu Link" (marca exibida
> pro usuário final — navbar, e-mails, PWA etc.), o repositório no
> GitHub é `Gerson-if/MenuLink`, e os identificadores técnicos de
> infraestrutura que o instalador cria (usuário de sistema, serviço
> systemd, site do Nginx, banco de dados, diretório sugerido) usam
> `menulink` por padrão — tudo alinhado com a marca. Única exceção de
> propósito: **arquivos-fonte do código** (`app/static/css/comanda.css`,
> `comanda.js`) mantêm o nome de código original — são detalhes internos
> de implementação, invisíveis pra quem usa o sistema. Se você tem uma
> instalação MAIS ANTIGA já em produção com os identificadores do nome
> anterior (`comanda`), rodar os scripts de novo não renomeia nada
> sozinho — um `git pull` não migra serviço systemd, banco de dados nem
> diretório já existentes; continue usando `comanda` nessa instalação
> (ex: `SERVICE_NAME=comanda sudo -E bash deploy/update.sh`) ou migre
> manualmente.

### Requisitos

- Um servidor com **Ubuntu 22.04 ou 24.04** (VPS de qualquer provedor),
  acessado via SSH como root (ou um usuário com `sudo`).
- O código do projeto já clonado no servidor, por exemplo em
  `/opt/menulink`:
  ```bash
  sudo git clone https://github.com/Gerson-if/MenuLink.git /opt/menulink
  cd /opt/menulink
  ```
- Se for usar um domínio com SSL, aponte o DNS dele (registro `A`) para
  o IP do servidor **antes** de rodar o instalador.

### Instalação guiada

```bash
sudo bash deploy/install.sh
```

O script pergunta, em ordem, e explica cada decisão antes de perguntar:

1. **Domínio** — informe um domínio (ex: `cardapio.seusite.com.br`) ou
   deixe em branco para acessar direto pelo IP do servidor. Se
   informar, o script pergunta se `www.` também deve funcionar (e
   configura o Nginx e o certificado SSL para os dois nomes).
2. **SSL** (só se você informou um domínio) — se quer configurar
   HTTPS agora, e com qual autoridade certificadora gratuita:
   **Let's Encrypt** (via `certbot`, recomendado) ou **ZeroSSL** (via
   `acme.sh`). Sem domínio, a aplicação sobe em HTTP simples — você
   pode rodar o instalador de novo mais tarde, depois de configurar um
   domínio, para habilitar o SSL.
3. **Banco de dados** — só PostgreSQL em produção (local, com o script
   criando o banco e o usuário para você, ou uma `DATABASE_URL`
   externa que você já tenha — RDS, Supabase, Neon, etc.). **SQLite
   não é oferecido aqui de propósito** — trava sob concorrência e não
   segura múltiplos lojistas gravando ao mesmo tempo; continua sendo a
   opção usada só no desenvolvimento local. Escolhendo PostgreSQL
   local, você define o nome do banco, o usuário e a senha (ou deixa o
   script gerar uma senha forte sozinho) — essas credenciais são
   geradas de forma independente a cada instalação, para que o vazamento
   de uma não comprometa outro servidor, e são mostradas (uma única
   vez) ao final da instalação.
4. **Usuário de sistema** que vai rodar a aplicação, sem privilégios de
   root. Por padrão o script sugere o próprio usuário que você já usa
   para dar deploy por SSH (detectado via `sudo`) — se ele já existir,
   é só reaproveitado, sem criar nada novo. Se preferir um usuário
   dedicado só para a aplicação, digite outro nome: se não existir,
   o script cria na hora.
5. **Firewall (ufw)**, opcional — libera só SSH (a porta é detectada
   automaticamente), HTTP e HTTPS, bloqueando o resto.

A partir daí é tudo automático: instala os pacotes necessários, cria o
ambiente virtual e instala as dependências Python, gera uma
`SECRET_KEY` aleatória e o arquivo `.env` de produção (permissão 600),
roda as migrations, oferece para criar o Super Admin
(`flask create-admin`, interativo), sobe o Gunicorn como serviço
systemd com um sandboxing básico (reinicia sozinho se cair ou se o
servidor reiniciar) e configura o Nginx como proxy reverso — com
certificado SSL já instalado, se você escolheu essa opção.

Ao final, o script mostra a URL de acesso, os comandos mais usados
(ver logs, reiniciar, atualizar) e — se criou um PostgreSQL local — o
nome do banco, o usuário e a senha gerados, além de salvar essa mesma
informação uma única vez em `/root/menulink-db-credentials-<data>.txt`
(permissão 600, só o root lê), para você guardar num gerenciador de
senhas e depois apagar o arquivo.

É seguro rodar o instalador de novo (ex: para reconfigurar Nginx/SSL
depois de apontar um domínio) — ele avisa antes de sobrescrever o que
já existe e nunca apaga dados do banco.

### Atualizando para uma versão mais nova

```bash
sudo bash deploy/update.sh
```

Esse script busca a versão mais recente do repositório e aplica a
atualização com segurança:

1. Garante que só uma atualização roda por vez (trava com `flock`) e
   que não há espaço em disco insuficiente para o backup.
2. Confere que não há mudanças locais não commitadas no servidor (ex:
   alguém editou um arquivo manualmente por SSH) — se houver, para
   sem tocar em nada, para não perder essas mudanças nem arriscar um
   merge malfeito.
3. Faz backup do banco de dados (dump do PostgreSQL, ou cópia do
   arquivo se for uma instalação antiga em SQLite) antes de mudar
   qualquer coisa. **Por padrão, se o backup falhar a atualização é
   abortada** — sem backup, um rollback não teria como restaurar os
   dados. Para forçar mesmo assim (não recomendado):
   `ALLOW_UPDATE_WITHOUT_BACKUP=1 sudo -E bash deploy/update.sh`.
4. Atualiza o código só por *fast-forward*.
5. Atualiza as dependências Python e roda as migrations pendentes.
6. Reinicia o serviço e confere se a aplicação voltou a responder
   (`GET /healthz`).
7. **Se qualquer passo a partir do 4 falhar**, reverte automaticamente
   o código para a versão anterior, restaura o backup do banco e
   reinicia o serviço — a aplicação não fica fora do ar por causa de
   uma atualização quebrada.

Os backups ficam em `deploy/backups/` (diretório e arquivos com
permissão restrita, só o usuário de sistema e o root leem — um dump
do banco contém dados de todos os lojistas).

Programe-o para rodar sozinho (ex: `cron` semanal) se quiser manter o
servidor sempre atualizado sem intervenção manual:

```bash
# roda toda segunda-feira às 4h da manhã
0 4 * * 1 root bash /opt/menulink/deploy/update.sh >> /var/log/menulink-update.log 2>&1
```

### Backup e restauração (migração entre servidores)

Além do backup automático que o `update.sh` faz antes de cada
atualização (pensado para rollback local), há um par de scripts
dedicado a **migrar o sistema inteiro para outra VPS** sem perder os
dados dos lojistas — banco de dados, uploads (logos, fotos do
cardápio) e o `.env`.

A forma mais simples de usar é pelo próprio menu do instalador:

```bash
sudo bash deploy/install.sh
```

Depois de detectar uma instalação existente, escolha a opção
**"Backup e restauração"** no menu principal. O submenu oferece:

- **Fazer backup agora** — gera um arquivo `.tar.gz` em
  `deploy/backups/`, pronto para copiar para outro servidor.
- **Listar backups existentes** — mostra os arquivos já gerados nesse
  servidor.
- **Restaurar um backup** — lista os backups disponíveis para escolher
  por número (ou aceita um caminho digitado), pede confirmação antes
  de continuar e então executa a restauração.

Também dá para rodar os scripts diretamente, sem passar pelo menu:

```bash
# Gerar um backup
sudo bash deploy/backup.sh

# Restaurar um backup (peça para digitar "restaurar" como confirmação)
sudo bash deploy/restore.sh deploy/backups/menulink_backup_2026-07-25_12-00-00.tar.gz.gpg
```

**O que o backup contém:**

1. Dump do banco de dados (`pg_dump --clean --if-exists` no
   PostgreSQL, ou cópia do arquivo no caso de SQLite).
2. Os uploads (`app/static/uploads/`) compactados.
3. O `.env` (chaves e credenciais).
4. Um `MANIFESTO.txt` com data, versão e o que está incluído.

Tudo isso é compactado num único `.tar.gz` e, se o `gpg` estiver
disponível no servidor, **criptografado com AES-256** (senha via
`BACKUP_PASSPHRASE` ou digitada na hora) — importante porque o backup
contém segredos de produção. É gerado também um checksum `.sha256`
para conferir a integridade do arquivo antes de restaurar. Use
`--no-encrypt` em `backup.sh` para pular a criptografia (não
recomendado fora de um teste local).

**Migrando para outra VPS**, o fluxo é:

1. Na VPS antiga: `sudo bash deploy/backup.sh` (ou pelo menu).
2. Copie o arquivo gerado (`.tar.gz` ou `.tar.gz.gpg` + `.sha256`) para
   a VPS nova — por exemplo com `scp`.
3. Na VPS nova, com o Menu Link já instalado (`deploy/install.sh` rodado
   uma vez): `sudo bash deploy/restore.sh <arquivo>` (ou pelo menu).

**Segurança da restauração** — como é uma operação destrutiva
(sobrescreve o banco e os uploads atuais), `restore.sh` tem várias
camadas de proteção:

- Pede para digitar a palavra `restaurar` como confirmação explícita
  (a menos que rode com `--yes`).
- Antes de tocar em qualquer coisa, tira um **snapshot de segurança do
  estado atual** em `deploy/backups/pre_restore_<timestamp>/` — mesmo
  que a restauração dê errado ou seja feita por engano, dá para voltar
  atrás manualmente.
- Confere o checksum `.sha256` do backup antes de usá-lo.
- Recusa restaurar um backup de um tipo de banco diferente do
  configurado no servidor atual (ex: backup de PostgreSQL num servidor
  configurado com SQLite).
- Roda `flask db upgrade` no final, garantindo que o schema fique
  compatível com a versão de código já instalada na VPS nova.

Use `--skip-env` em `restore.sh` se quiser manter o `.env` atual do
servidor (por exemplo, se o domínio/URLs mudaram entre uma VPS e
outra) em vez de sobrescrevê-lo com o `.env` do backup.

### Detalhes técnicos do que o instalador configura

- **Serviço systemd** (`/etc/systemd/system/menulink.service`): roda o
  Gunicorn com o usuário de sistema dedicado, reinicia automaticamente
  em caso de falha. Comandos úteis: `systemctl status menulink`,
  `systemctl restart menulink`, `journalctl -u menulink -f`.
- **Nginx** (`/etc/nginx/sites-available/menulink`): proxy reverso para
  o Gunicorn (que só escuta localmente) e serve `/static` diretamente,
  com cache.
- **SSL**: com Let's Encrypt, o `certbot` já configura a renovação
  automática (`certbot.timer`, do próprio pacote do Ubuntu); com
  ZeroSSL, o `acme.sh` instala sozinho uma tarefa periódica de
  renovação durante sua própria instalação.
- Os templates usados para gerar esses arquivos ficam em
  `deploy/templates/`, caso queira revisar ou ajustar antes de rodar o
  instalador.

### Checklist de variáveis de ambiente (`.env`)

O instalador já gera o `.env` automaticamente, mas se preferir
configurar manualmente (ex: outra plataforma de hospedagem), os pontos
obrigatórios são:

1. **`SECRET_KEY`** — obrigatória, o app recusa iniciar com o valor
   padrão de desenvolvimento.
2. **`DATABASE_URL`** — obrigatória em produção.
3. **HTTPS** — por padrão o cookie de sessão exige conexão HTTPS
   (`SESSION_COOKIE_SECURE=True`). Sem HTTPS configurado ainda, defina
   `FORCE_HTTPS=false` temporariamente, ou o login vai "não funcionar"
   silenciosamente.
4. **Migrations** — rode `flask db upgrade` antes do primeiro start.
5. **Rate limiting com múltiplos workers** — o limitador padrão é em
   memória (não compartilhado entre processos). Com mais de um worker
   do Gunicorn, configure `RATELIMIT_STORAGE_URI=redis://...` para um
   limite realmente global.

### Verificando que subiu certo

- `GET /healthz` deve responder `{"status": "ok"}`.
- Se a tela ficar em branco, abra o DevTools do navegador (F12 → aba
  Console/Network): qualquer erro de carregamento de asset ou
  JavaScript aparece ali. Todos os assets críticos (Bootstrap,
  Bootstrap Icons, Alpine.js, HTMX, Chart.js e as fontes) são servidos
  localmente pelo Flask a partir de `app/static/vendor/` — nada é
  baixado de CDN em runtime nem em deploy, então não há risco de
  firewall corporativo, CSP ou serviço externo fora do ar quebrando a
  renderização inicial. O QR Code também é gerado localmente, sem
  depender de API externa.

[⬆ voltar ao índice](#índice)

## Comandos úteis

```bash
flask db migrate -m "descrição"   # gera nova migration a partir de mudanças nos models
flask db upgrade                   # aplica migrations pendentes
flask db downgrade                 # reverte a última migration
flask seed-db                      # popula dados mínimos (idempotente)
flask create-admin                 # cria/atualiza um super admin específico
flask process-trials               # lembretes/tolerância/suspensão de teste grátis (rodar 1x/dia via cron)
flask process-dunning               # marca faturas vencidas e bloqueia inadimplentes (rodar 1x/dia via cron)
pytest tests/ -v                    # roda a suíte de testes
```

[⬆ voltar ao índice](#índice)

## Validações de dados

- **Telefone/WhatsApp**: extrai os dígitos e exige uma quantidade
  plausível (10 a 15), tolerando formatação com
  parênteses/hífens/espaços. Aplicado no auto-cadastro, nos formulários
  do Super Admin, nas configurações da loja e no checkout público.
- **Nomes só com espaço** (categoria, produto, banner, grupo/opção de
  complemento) são rejeitados.
- **Slug do cardápio** valida formato (letras minúsculas, números,
  hífen).
- **Preço de custo maior que preço de venda** é bloqueado no formulário
  de produto.
- **Endereço de entrega** não aceita string só de espaços em branco.

[⬆ voltar ao índice](#índice)

## Fora do escopo atual

- **Envio real de e-mail**: a recuperação de senha funciona de ponta a
  ponta, mas o link é registrado em log em vez de enviado — não há
  serviço de e-mail (SMTP/SendGrid/SES) configurado no projeto. A
  integração real é uma função só (`app/utils/mailer.py`).
- **Gateway de pagamento para o lojista final**: a integração com
  Asaas cobre a cobrança das faturas dos lojistas junto ao Super Admin;
  não há checkout de assinatura self-service.
- **API oficial do WhatsApp Business**: o envio continua via link
  `wa.me` aberto pelo navegador do cliente, não uma integração
  server-to-server.
- **Testes de navegador (visual)**: toda a cobertura de testes é via
  `pytest` + test client Flask (HTTP, HTML, regras de negócio) — não há
  Selenium/Playwright validando renderização real em um navegador.

[⬆ voltar ao índice](#índice)

## Licença

Software proprietário — todos os direitos reservados. Uso, cópia,
modificação e redistribuição exigem autorização expressa do titular.
Ver texto completo em [`LICENSE`](LICENSE).
