# Comanda — Plataforma Multi-Tenant de Cardápio Digital

Comanda é uma plataforma multi-tenant de cardápio digital: um Super
Administrador gerencia a plataforma e os planos, e cada Lojista tem seu
próprio cardápio, produtos, pedidos e clientes, totalmente isolados dos
demais.

## Stack

- **Backend**: Flask, SQLAlchemy, Alembic (migrations), Marshmallow
  (validação), Flask-Login, Flask-WTF (CSRF), Flask-Limiter (rate
  limiting).
- **Frontend**: Bootstrap 5 + Bootstrap Icons (identidade visual
  "Comanda"), Alpine.js (estado reativo: carrinho, checkout, sidebar) e
  HTMX (atualizações parciais de página sem recarregar). Todos os
  assets críticos (Bootstrap, Alpine.js, HTMX, Chart.js) são servidos
  localmente pelo próprio Flask, sem dependência de CDN externo para a
  aplicação renderizar.
- **Banco de dados**: SQLite em desenvolvimento, PostgreSQL recomendado
  em produção.

## Funcionalidades

### Autenticação e multi-tenant

- Login único por e-mail/senha para Super Admin e Lojista, com
  contexto multi-tenant resolvido automaticamente e autorização por
  papel (`@super_admin_required` / `@lojista_required`, bloqueando com
  403 o acesso cruzado entre papéis).
- Bloqueio de contas suspensas ou inadimplentes.
- CSRF em todos os formulários e no endpoint JSON de checkout.
- Rate limiting no login (10 tentativas por minuto por IP).
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
  WhatsApp, logo), cardápio/link (slug editável, retirada/entrega),
  checkout (taxa de entrega, com isenção configurável acima de um
  valor mínimo), e assinatura (plano atual + histórico de faturas,
  somente leitura).
- **Limites de plano**: `Plan.max_categories` / `Plan.max_products`
  checados na criação.
- **Link do cardápio** exibido no dashboard com botão "Copiar link".

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
  suspender, bloquear por inadimplência (`TenantStatus.BLOCKED_PAYMENT`,
  que impede login do lojista e derruba o cardápio público para 404) e
  cancelar.
- **Planos**: CRUD completo (nome, preço, ciclo mensal/anual, limites
  de categorias/produtos/imagens), com ativar/desativar via clique no
  badge (HTMX).
- **Cobrança (faturas)**: o Super Admin lança faturas manualmente para
  um lojista. Uma `Subscription` ativa é criada automaticamente na
  primeira fatura, com o período calculado a partir do ciclo do plano.
  Marcar uma fatura como paga libera automaticamente um lojista
  bloqueado por inadimplência.
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
"Comanda"; Fraunces + Plus Jakarta Sans para o cardápio público. Todas
auto-hospedadas (`app/static/vendor/fonts/`), sem depender do Google
Fonts em tempo de execução.

`app/templates/layouts/lojista_panel.html` e `admin_panel.html`
implementam a mesma casca (sidebar retrátil, colapsável em desktop,
drawer em mobile) parametrizada por tema. Toda página de painel
estende um desses layouts e só preenche `page_title`/`panel_content`.

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
banners e de grupos/opções de complemento; cálculo de margem; e testes
de produção (assets críticos servidos localmente sem depender de CDN,
variáveis de ambiente obrigatórias validadas, QR Code gerado
localmente).

## Como rodar localmente (SQLite)

> **Dependência de sistema opcional — `ffmpeg`**: o upload de vídeo de
> fundo da landing page (Super Admin → Landing Page → Vídeo de fundo do
> hero) é recomprimido automaticamente via o binário `ffmpeg` (não é
> `pip install`, precisa estar no `PATH`). Sem ele, o upload continua
> funcionando normalmente, só que sem a recompressão — o arquivo
> original enviado é salvo como está. Em produção, instale o pacote
> `ffmpeg` da distribuição (ex: `apt install ffmpeg`) ou inclua-o na
> imagem/buildpack de deploy.

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

## Deploy em produção

O jeito recomendado de colocar o Comanda no ar é um VPS Ubuntu comum
(sem Docker), usando o instalador guiado em `deploy/install.sh`. Ele
cuida de tudo: dependências de sistema, banco de dados, ambiente
virtual, migrations, serviço systemd, Nginx e, se você tiver um
domínio, certificado SSL gratuito com renovação automática.

### Requisitos

- Um servidor com **Ubuntu 22.04 ou 24.04** (VPS de qualquer provedor),
  acessado via SSH como root (ou um usuário com `sudo`).
- O código do projeto já clonado no servidor, por exemplo em
  `/opt/comanda`:
  ```bash
  sudo git clone <url-do-seu-repositorio> /opt/comanda
  cd /opt/comanda
  ```
- Se for usar um domínio com SSL, aponte o DNS dele (registro `A`) para
  o IP do servidor **antes** de rodar o instalador.

### Instalação guiada

```bash
sudo bash deploy/install.sh
```

O script pergunta, em ordem:

1. **Domínio ou IP** — informe um domínio (ex: `cardapio.seusite.com.br`)
   ou deixe em branco para acessar direto pelo IP do servidor.
2. **SSL** (só se você informou um domínio) — se quer configurar
   HTTPS agora, e com qual autoridade certificadora gratuita:
   **Let's Encrypt** (via `certbot`, recomendado) ou **ZeroSSL** (via
   `acme.sh`). Sem domínio, a aplicação sobe em HTTP simples — você
   pode rodar o instalador de novo mais tarde, depois de configurar um
   domínio, para habilitar o SSL.
3. **Banco de dados** — PostgreSQL local (o script instala e configura
   sozinho, recomendado), SQLite (mais simples, sem serviço extra, ok
   para pouco tráfego) ou uma `DATABASE_URL` externa que você já tenha
   (RDS, Supabase, etc.).
4. **Usuário de sistema** que vai rodar a aplicação (criado
   automaticamente, sem privilégios de root).

A partir daí é tudo automático: instala os pacotes necessários, cria o
ambiente virtual e instala as dependências Python, gera uma
`SECRET_KEY` aleatória e o arquivo `.env` de produção, roda as
migrations, oferece para criar o Super Admin (`flask create-admin`,
interativo), sobe o Gunicorn como serviço systemd (reinicia sozinho se
cair ou se o servidor reiniciar) e configura o Nginx como proxy
reverso — com certificado SSL já instalado, se você escolheu essa
opção.

Ao final, o script mostra a URL de acesso e os comandos mais usados
(ver logs, reiniciar, atualizar).

### Atualizando para uma versão mais nova

```bash
sudo bash deploy/update.sh
```

Esse script busca a versão mais recente do repositório e aplica a
atualização com segurança:

1. Faz backup do banco de dados (dump do PostgreSQL ou cópia do arquivo
   SQLite) antes de mudar qualquer coisa.
2. Atualiza o código só por *fast-forward* — se houver alterações
   locais divergentes no servidor, ele para sem tocar em nada, em vez
   de arriscar um merge malfeito.
3. Atualiza as dependências Python e roda as migrations pendentes.
4. Reinicia o serviço e confere se a aplicação voltou a responder
   (`GET /healthz`).
5. **Se qualquer passo falhar**, reverte automaticamente o código para
   a versão anterior, restaura o backup do banco e reinicia o serviço
   — a aplicação não fica fora do ar por causa de uma atualização
   quebrada.

Programe-o para rodar sozinho (ex: `cron` semanal) se quiser manter o
servidor sempre atualizado sem intervenção manual:

```bash
# roda toda segunda-feira às 4h da manhã
0 4 * * 1 root bash /opt/comanda/deploy/update.sh >> /var/log/comanda-update.log 2>&1
```

### Detalhes técnicos do que o instalador configura

- **Serviço systemd** (`/etc/systemd/system/comanda.service`): roda o
  Gunicorn com o usuário de sistema dedicado, reinicia automaticamente
  em caso de falha. Comandos úteis: `systemctl status comanda`,
  `systemctl restart comanda`, `journalctl -u comanda -f`.
- **Nginx** (`/etc/nginx/sites-available/comanda`): proxy reverso para
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

## Comandos úteis

```bash
flask db migrate -m "descrição"   # gera nova migration a partir de mudanças nos models
flask db upgrade                   # aplica migrations pendentes
flask db downgrade                 # reverte a última migration
flask seed-db                      # popula dados mínimos (idempotente)
flask create-admin                 # cria/atualiza um super admin específico
pytest tests/ -v                    # roda a suíte de testes
```

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
