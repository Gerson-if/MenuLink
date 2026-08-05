/*
 * JS compartilhado do painel (lojista + Super Admin). Registrado com
 * `defer` em base.html, antes do Alpine, para que estas funções já
 * existam no momento em que o Alpine processa os `x-data` que as usam.
 */

/**
 * Estado do "app shell" (sidebar) usado em layouts/lojista_panel.html e
 * layouts/admin_panel.html. `collapsed` persiste em localStorage — sem
 * isso, cada navegação (app multi-página, sem hx-boost) recriava o
 * x-data do zero e o recolhimento da sidebar nunca "durava".
 */
function sidebarShell() {
  return {
    sidebarOpen: false,
    collapsed: localStorage.getItem('comandaSidebarCollapsed') === '1',
    toggleCollapsed() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('comandaSidebarCollapsed', this.collapsed ? '1' : '0');
    },
    // O valor inicial já foi aplicado no <html> pelo script anti-flash em
    // base.html (roda antes do Alpine) — aqui só refletimos o mesmo
    // estado pra `:class`/`x-text` do botão sol/lua ficarem sincronizados.
    colorMode: document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark',
    toggleColorMode() {
      this.colorMode = this.colorMode === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', this.colorMode);
      localStorage.setItem('comandaColorMode', this.colorMode);
      // Mantém a cor da barra de status do celular (meta theme-color, ver
      // base.html) em sincronia com o tema — sem isso, ela só era
      // corrigida no carregamento da página, não ao clicar no sol/lua.
      var meta = document.getElementById('meta-theme-color');
      if (meta) meta.setAttribute('content', this.colorMode === 'light' ? '#F7F1E4' : '#221C16');
    },
  };
}

/*
 * Modal de confirmação genérico (markup em base.html, #confirmModal) —
 * substitui o confirm() nativo do navegador em toda a aplicação:
 *
 * - hx-confirm="..." (HTMX): interceptado via o evento htmx:confirm, o
 *   hook oficial do HTMX pra customizar a confirmação. Nenhum template
 *   que já usa hx-confirm precisa mudar.
 * - onsubmit="return confirm(...)" nativo: trocado por
 *   data-confirm-message="..." no <form>, interceptado aqui via o evento
 *   submit.
 *
 * Reinicializado a cada navegação "boosted" da sidebar (ver
 * initPageChrome mais abaixo) — #confirmModal vive em base.html, FORA
 * do conteúdo trocado a cada seção, mas com hx-boost o <body> inteiro é
 * substituído a cada navegação, recriando o nó do zero; sem reamarrar a
 * instância do bootstrap.Modal e o listener do botão "Confirmar", os
 * diálogos de confirmação (ex: excluir produto) parariam de funcionar
 * depois da primeira navegação pelo menu. Os listeners delegados em
 * `document`/`document.body` (htmx:confirm, submit) ficam de fora desta
 * função — são registrados uma única vez, mais abaixo, e sempre chamam
 * a versão mais recente de `askConfirmation` guardada em `__confirmAsk`.
 * `__confirmPendingAction` também vive fora da função (mesmo motivo):
 * se ficasse como variável local, um `initConfirmModal()` chamado de
 * novo sem recriar o botão (ver guarda `dataset.bound` abaixo) deixaria
 * o listener de clique já amarrado apontando pra uma variável "morta"
 * de uma chamada anterior, desconectada da que `askConfirmation`
 * atualiza — o modal pararia de disparar a ação combinada.
 */
let __confirmAsk = null;
let __confirmPendingAction = null;

function initConfirmModal() {
  const modalEl = document.getElementById('confirmModal');
  if (!modalEl) {
    __confirmAsk = null;
    return;
  }

  const messageEl = document.getElementById('confirmModalMessage');
  const confirmBtn = document.getElementById('confirmModalConfirmBtn');
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

  __confirmAsk = function askConfirmation(message, onConfirm) {
    messageEl.textContent = message;
    __confirmPendingAction = onConfirm;
    modal.show();
  };

  // initPageChrome (onde initConfirmModal é chamada) roda a cada settle
  // do HTMX na página inteira, não só depois de uma navegação boosted —
  // sem esta guarda, cada pequena troca via HTMX em outro canto da
  // página (ex: recarregar a grade de produtos) reamarraria outro
  // listener de clique no MESMO botão "Confirmar" (o elemento não foi
  // recriado, só o resto da página mudou), acumulando handlers.
  if (!confirmBtn.dataset.bound) {
    confirmBtn.dataset.bound = '1';
    confirmBtn.addEventListener('click', () => {
      const action = __confirmPendingAction;
      __confirmPendingAction = null;
      modal.hide();
      if (action) action();
    });
  }
}
initConfirmModal();

document.body.addEventListener('htmx:confirm', (evt) => {
  if (!evt.detail.question || !__confirmAsk) return;
  evt.preventDefault();
  __confirmAsk(evt.detail.question, () => evt.detail.issueRequest(true));
});

document.addEventListener('submit', (evt) => {
  const form = evt.target;
  if (!(form instanceof HTMLFormElement) || !__confirmAsk) return;
  const message = form.getAttribute('data-confirm-message');
  if (!message || form.dataset.confirmBypass === '1') return;
  evt.preventDefault();
  __confirmAsk(message, () => {
    form.dataset.confirmBypass = '1';
    form.requestSubmit();
  });
});

/*
 * Tooltip (Bootstrap) nos ícones do menu lateral — mostra o nome da
 * seção ao passar o mouse, útil sobretudo com a sidebar recolhida (só
 * ícone, sem o texto do nav-label). O botão de recolher/expandir fica
 * de fora de propósito: o texto dele muda de acordo com o estado
 * (collapsed) via Alpine (:title, atualiza a cada clique), e o
 * Bootstrap Tooltip cacheia o título só na inicialização — usar os
 * dois juntos deixaria a dica dessincronizada depois do primeiro clique.
 *
 * `getInstance`+`dispose` evita acumular instâncias órfãs quando esta
 * função roda de novo após uma navegação boosted (ver initPageChrome).
 */
function initSidebarTooltips() {
  document.querySelectorAll('.sidebar [data-bs-toggle="tooltip"]').forEach((el) => {
    const existing = bootstrap.Tooltip.getInstance(el);
    if (existing) existing.dispose();
    new bootstrap.Tooltip(el);
  });
}

/*
 * Mantém o item ativo visível no menu lateral (.nav-food é uma lista
 * rolável — `overflow-y: auto`, ver comanda.css). A navegação principal
 * (sidebar/barra inferior) usa hx-boost — troca só o conteúdo da
 * página, sem recarregar CSS/JS/fontes do zero —, mas o `.nav-food`
 * ainda assim nasce rolado pro topo a cada swap (é um elemento novo).
 * Sem isso, a sidebar "esquecia" a seleção visualmente — o item
 * continuava ativo (classe `.active` no link certo), só que fora da
 * área visível, dando a impressão de que a navegação tinha voltado pro
 * topo e perdido a seleção. `block: 'nearest'` garante que só o
 * `.nav-food` (ancestral rolável mais próximo) é ajustado, sem mexer no
 * scroll da página.
 */
function initActiveNavScroll() {
  const activeLink = document.querySelector('.nav-food .nav-link.active');
  if (activeLink) activeLink.scrollIntoView({ block: 'nearest' });
}

/*
 * Botão de mostrar/ocultar senha (login e outros formulários com
 * .password-toggle-btn) — troca type="password" por type="text" e o
 * ícone de olho. Progressive enhancement: sem JS o campo continua um
 * <input type="password"> normal, só sem o botão.
 */
function initPasswordToggles() {
  document.querySelectorAll('.password-toggle-btn').forEach((btn) => {
    if (btn.dataset.toggleBound) return;
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    btn.dataset.toggleBound = '1';
    btn.addEventListener('click', () => {
      const showingNow = input.type === 'text';
      input.type = showingNow ? 'password' : 'text';
      btn.querySelector('i').className = showingNow ? 'bi bi-eye' : 'bi bi-eye-slash';
      btn.setAttribute('aria-label', showingNow ? 'Mostrar senha' : 'Ocultar senha');
    });
  });
}

/*
 * Preview local (antes do envio) de imagens/vídeos escolhidos em
 * qualquer ".dropzone" (banner, logo da loja, ícone de categoria,
 * fotos de produto, mídia da landing) — usa URL.createObjectURL (mais
 * rápido e mais leve que FileReader/data:, importante pros vídeos de
 * até 25MB) e mostra no lugar do ícone/texto instrutivo. Reaplica
 * depois de qualquer swap HTMX, porque tanto o dropzone de fotos de
 * produto quanto os fragmentos de remoção de mídia recriam o DOM
 * (hx-target="#image-gallery" / "closest .dropzone-wrap").
 *
 * Cada dropzone que permite remover um arquivo já salvo é envolvida
 * num ".dropzone-wrap" com dois botões-ícone irmãos do <label>:
 * ".dropzone-remove-existing" (chama o servidor via hx-post, some
 * enquanto um arquivo novo está só selecionado localmente) e
 * ".dropzone-clear-local" (só client-side, cancela a seleção e
 * restaura o preview pro estado original salvo em data-original-src /
 * data-has-original do wrap).
 */
function initDropzonePreviews(root) {
  (root || document).querySelectorAll('.dropzone input[type=file]').forEach((input) => {
    if (input.dataset.previewBound) return;
    input.dataset.previewBound = '1';
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      const label = input.closest('.dropzone');
      const wrap = input.closest('.dropzone-wrap');
      const preview = label && label.querySelector('.dropzone-preview');
      if (!file || !preview) return;

      if (wrap && wrap.dataset.objectUrl) URL.revokeObjectURL(wrap.dataset.objectUrl);
      const objectUrl = URL.createObjectURL(file);
      if (wrap) wrap.dataset.objectUrl = objectUrl;

      preview.src = objectUrl;
      preview.classList.remove('d-none');
      label.querySelectorAll(':scope > i, :scope > span.small').forEach((el) => el.classList.add('d-none'));

      if (wrap) {
        const removeBtn = wrap.querySelector('.dropzone-remove-existing');
        const clearBtn = wrap.querySelector('.dropzone-clear-local');
        if (removeBtn) removeBtn.classList.add('d-none');
        if (clearBtn) clearBtn.classList.remove('d-none');
      }
    });
  });

  (root || document).querySelectorAll('.dropzone-clear-local').forEach((btn) => {
    if (btn.dataset.clearBound) return;
    btn.dataset.clearBound = '1';
    btn.addEventListener('click', () => {
      const wrap = btn.closest('.dropzone-wrap');
      if (!wrap) return;
      const input = wrap.querySelector('input[type=file]');
      const label = wrap.querySelector('.dropzone');
      const preview = wrap.querySelector('.dropzone-preview');
      const removeBtn = wrap.querySelector('.dropzone-remove-existing');
      if (input) input.value = '';
      if (wrap.dataset.objectUrl) {
        URL.revokeObjectURL(wrap.dataset.objectUrl);
        delete wrap.dataset.objectUrl;
      }

      const hasOriginal = wrap.dataset.hasOriginal === 'true';
      if (preview) {
        preview.src = hasOriginal ? wrap.dataset.originalSrc : '';
        preview.classList.toggle('d-none', !hasOriginal);
      }
      if (label) {
        label.querySelectorAll(':scope > i, :scope > span.small').forEach((el) => el.classList.toggle('d-none', hasOriginal));
      }
      btn.classList.add('d-none');
      if (removeBtn) removeBtn.classList.toggle('d-none', !hasOriginal);
    });
  });
}
initDropzonePreviews();
document.body.addEventListener('htmx:afterSwap', (evt) => initDropzonePreviews(evt.detail.target));

/*
 * Toast de confirmação para salvamentos feitos pelo drawer (HTMX) —
 * criar/editar plano, produto, categoria, banner e dados do lojista
 * trocam só o fragmento do formulário, então o toast desenhado a partir
 * de flash() em base.html (só existe numa página inteira) nunca
 * aparecia: o lojista/Super Admin salvava e não tinha nenhum sinal de
 * que a gravação deu certo. O backend manda a mensagem no header
 * HX-Trigger (ver app/utils/htmx.py, hx_fragment_response) como o evento
 * "toastMessage"; este listener desenha o mesmo toast usado no
 * carregamento normal da página (mesmo HTML/CSS/transição do Alpine que
 * o bloco em base.html usa, só que criado dinamicamente).
 */
function showToast(message, category) {
  if (!message) return;
  let stack = document.querySelector('.toast-flash-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'toast-flash-stack';
    document.body.appendChild(stack);
  }
  const cat = ['success', 'danger', 'warning', 'info'].includes(category) ? category : 'secondary';
  const icons = {
    success: 'bi-check-circle-fill', danger: 'bi-x-circle-fill',
    warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill',
  };
  const icon = icons[cat] || 'bi-info-circle-fill';

  const wrap = document.createElement('div');
  wrap.innerHTML = `<div class="toast-flash toast-flash-${cat}"
      x-data="{ show: false }" x-init="requestAnimationFrame(() => show = true); setTimeout(() => show = false, 6000)"
      x-show="show"
      x-transition:enter="toast-flash-enter" x-transition:enter-start="toast-flash-enter-start" x-transition:enter-end="toast-flash-enter-end"
      x-transition:leave="toast-flash-leave" x-transition:leave-start="toast-flash-leave-start" x-transition:leave-end="toast-flash-leave-end">
    <i class="bi ${icon} toast-flash-icon" aria-hidden="true"></i>
    <span class="toast-flash-message"></span>
    <button type="button" class="toast-flash-close" @click="show = false" aria-label="Fechar">&times;</button>
  </div>`;
  const el = wrap.firstElementChild;
  el.querySelector('.toast-flash-message').textContent = message;
  stack.appendChild(el);
  // Alpine observa o DOM e inicializa o x-data sozinho assim que o nó
  // entra na árvore — mesmo mecanismo que já faz o drawer funcionar com
  // conteúdo trazido via HTMX em outras telas do painel.
}

document.body.addEventListener('toastMessage', (evt) => {
  const detail = evt.detail || {};
  showToast(detail.message, detail.category);
});

/*
 * Faz o botão/gesto "voltar" do celular fechar a gaveta lateral
 * (offcanvas) ou modal aberto, em vez de sair da tela inteira por
 * trás. Sem isso, abrir a gaveta de editar/cadastrar produto,
 * categoria, banner etc. e apertar "voltar" no Android tirava o
 * lojista da seção inteira (perdendo o que estava fazendo), porque o
 * navegador não associa nenhuma entrada de histórico ao overlay — ele
 * simplesmente navega para a página anterior de verdade.
 *
 * Estratégia: ao abrir qualquer offcanvas/modal, empilha uma entrada
 * "fantasma" no histórico. O botão voltar então consome essa entrada
 * primeiro (dispara `popstate`), e o listener abaixo fecha o overlay
 * em vez de deixar a navegação acontecer. Se o usuário fechar pelo X/
 * clique fora (sem usar o botão voltar), a mesma entrada fantasma é
 * consumida programaticamente, pra não acumular lixo no histórico.
 */
(function () {
  let overlayOpen = false;

  function onShow() {
    if (overlayOpen) return;
    overlayOpen = true;
    history.pushState({ comandaOverlay: true }, '');
  }

  function onHidden() {
    if (!overlayOpen) return;
    overlayOpen = false;
    // Só "volta" programaticamente se ainda estivermos na entrada
    // fantasma (fechou pelo X/fora) — se o popstate abaixo é quem
    // disparou o fechamento (botão voltar), o navegador já saiu dessa
    // entrada sozinho, e chamar back() de novo iria pular mais um
    // passo no histórico do que deveria.
    if (history.state && history.state.comandaOverlay) {
      history.back();
    }
  }

  document.addEventListener('show.bs.offcanvas', onShow);
  document.addEventListener('show.bs.modal', onShow);
  document.addEventListener('hidden.bs.offcanvas', onHidden);
  document.addEventListener('hidden.bs.modal', onHidden);

  window.addEventListener('popstate', () => {
    if (!overlayOpen) return;
    overlayOpen = false;
    document.querySelectorAll('.offcanvas.show').forEach((el) => {
      const instance = bootstrap.Offcanvas.getInstance(el);
      if (instance) instance.hide();
    });
    document.querySelectorAll('.modal.show').forEach((el) => {
      const instance = bootstrap.Modal.getInstance(el);
      if (instance) instance.hide();
    });
  });
})();

/*
 * "+ Novo produto" cria o rascunho assim que o lojista clica, pra já
 * abrir a edição completa (fotos/complementos disponíveis desde o
 * início — ver lojista.products_create_draft). Se ele fechar a barra
 * lateral sem preencher nada, isso deixaria um card "Novo produto"
 * vazio parado no cardápio. Ao fechar o drawer, avisa o servidor pra
 * apagar o rascunho — mas só apaga se ele ainda estiver intocado
 * (Product.is_untouched_draft); qualquer dado salvo, foto ou
 * complemento cancela a limpeza.
 *
 * Reamarrado a cada navegação boosted (initPageChrome) — o marcador
 * `data-drawer-bound` evita duplicar o listener se a página de Produtos
 * for revisitada sem um swap completo de #editDrawer no meio.
 */
function initProductDraftDiscard() {
  const drawer = document.getElementById('editDrawer');
  if (!drawer || drawer.dataset.drawerBound) return;
  drawer.dataset.drawerBound = '1';

  drawer.addEventListener('hidden.bs.offcanvas', () => {
    const marker = document.getElementById('drawer-product-id');
    if (!marker) return;
    htmx.ajax('POST', marker.dataset.discardUrl, { swap: 'none' }).then(() => {
      document.body.dispatchEvent(new Event('productSaved'));
    });
  });
}

/*
 * "Revelar ao rolar" — utilitário genérico, sitewide: qualquer elemento
 * com o atributo `data-reveal` entra com um fade + leve deslocamento
 * quando cruza a viewport pela primeira vez (um `--reveal-delay` no
 * style do próprio elemento dá um efeito de cascata em listas/grids).
 *
 * A classe que deixa o elemento invisível (`reveal-pending`) só é
 * adicionada aqui, pelo JS — nunca existe uma regra CSS baseada direto
 * em `[data-reveal]`. Assim, se este script falhar por qualquer motivo,
 * o conteúdo continua com a visibilidade normal (progressive
 * enhancement: nada de seção sumida por causa de um erro de JS).
 * Respeita `prefers-reduced-motion` pelo mesmo motivo.
 */
function initRevealOnScroll() {
  const els = document.querySelectorAll('[data-reveal]:not(.reveal-pending):not(.is-visible)');
  if (!els.length) return;
  if (!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  els.forEach((el) => el.classList.add('reveal-pending'));

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15, rootMargin: '0px 0px -60px 0px' });

  els.forEach((el) => observer.observe(el));
}

/*
 * Chips em carrossel (.chip-scroll — status de Pedidos/Histórico,
 * período do Histórico, ordenação de Clientes, categorias de Produtos,
 * abas de Configurações) rolam na horizontal em uma linha só. A página/
 * conteúdo recém-trocado sempre nasce rolado pro início — então, se o
 * item selecionado (.btn-chili) for um dos últimos, ele ficava fora da
 * área visível, "escondido" à direita, dando a impressão de que a
 * seleção tinha se perdido. Aqui a gente centraliza o item ativo. `block:
 * 'nearest'` garante que só a rolagem horizontal do carrossel se move —
 * não rola a página inteira.
 */
function initChipScrollCentering() {
  document.querySelectorAll('.chip-scroll').forEach((container) => {
    const active = container.querySelector('.btn-chili');
    if (active) active.scrollIntoView({ block: 'nearest', inline: 'center' });
  });
}

/*
 * Reúne toda inicialização client-side que depende de elementos do
 * <body> (sidebar, drawers, carrosséis...) — roda uma vez no carregamento
 * normal da página E de novo depois de qualquer navegação "boosted" da
 * sidebar/barra inferior (ver hx-boost em layouts/admin_panel.html e
 * layouts/lojista_panel.html): como o <body> inteiro é substituído a
 * cada troca de seção, sem isso essas funcionalidades só funcionariam
 * na primeira página carregada de verdade, "quebrando" silenciosamente
 * depois do primeiro clique no menu.
 */
function initPageChrome() {
  initConfirmModal();
  initSidebarTooltips();
  initActiveNavScroll();
  initPasswordToggles();
  initProductDraftDiscard();
  initRevealOnScroll();
  initChipScrollCentering();
}

document.body.addEventListener('htmx:afterSettle', initPageChrome);

// Chamada direta pro carregamento normal (não boosted) da página — o
// comentário acima já dizia "roda uma vez no carregamento normal E de
// novo depois de navegação boosted", mas só o listener de
// htmx:afterSettle existia; esse evento NUNCA dispara no primeiro
// carregamento real (só depois de uma navegação boosted), então
// toggle de senha, modal de confirmação, tooltips etc. simplesmente
// não funcionavam em NENHUMA primeira visita a uma página (login,
// primeira vez no painel após F5, etc.) — só passavam a funcionar
// depois do primeiro clique num link boosted. Este <script> já é
// `defer`, então o DOM já está pronto quando esta linha roda; não
// precisa esperar DOMContentLoaded.
initPageChrome();

/*
 * Barra de progresso fina no topo da página — dá feedback visual
 * IMEDIATO ao trocar de seção (sidebar/barra inferior, ver hx-boost),
 * em vez do usuário só ver a tela "parada" enquanto o servidor responde
 * (o que dava a impressão de o clique não ter funcionado, ou do sistema
 * estar lento/travado). Cobre qualquer requisição HTMX da página, não
 * só a navegação boosted — inclusive salvamentos em drawer.
 *
 * O atraso de 150ms antes de mostrar evita "piscar" a barra em
 * respostas rápidas (a grande maioria); a barra sobe suavemente até
 * ~90% enquanto espera (nunca completa sozinha, pra não sugerir que
 * terminou antes da hora) e só vai a 100%+some quando a resposta chega.
 *
 * Anexada no <html> (documentElement), não no <body> — com hx-boost o
 * <body> inteiro é substituído a cada navegação (innerHTML), o que
 * apagaria a barra (um filho dele) já na primeira troca de seção.
 * document.documentElement nunca é tocado pelo swap.
 */
(function () {
  const bar = document.createElement('div');
  bar.className = 'nav-progress-bar';
  document.documentElement.appendChild(bar);

  let showTimer = null;
  let hideTimer = null;
  let width = 0;

  function show() {
    width = 12;
    bar.style.transition = 'none';
    bar.style.width = width + '%';
    bar.style.opacity = '1';
    // força o reflow antes de trocar pra transição suave — sem isso o
    // navegador podia agrupar as duas mudanças de estilo num único
    // frame e a barra "pularia" direto pro valor final sem animar.
    void bar.offsetWidth;
    bar.style.transition = 'width 3s cubic-bezier(.1,.6,.2,1), opacity .2s ease';
    width = 90;
    bar.style.width = width + '%';
  }

  function finish() {
    bar.style.transition = 'width .2s ease, opacity .3s ease .1s';
    bar.style.width = '100%';
    bar.style.opacity = '0';
  }

  document.body.addEventListener('htmx:beforeRequest', () => {
    clearTimeout(hideTimer);
    showTimer = setTimeout(show, 150);
  });

  document.body.addEventListener('htmx:afterRequest', () => {
    clearTimeout(showTimer);
    if (bar.style.opacity === '1') {
      hideTimer = setTimeout(finish, 80);
    }
  });
})();

/*
 * Campo escondido atrás do teclado no celular (relatado primeiro no
 * cadastro em passos, mas o mesmo problema existe em QUALQUER campo de
 * texto/textarea do site em mobile): ao tocar num campo que fica na
 * metade de baixo da tela — comum em telas centralizadas verticalmente,
 * como o painel de login/cadastro —, o teclado abre por cima dele sem o
 * navegador rolar a página sozinho, obrigando a pessoa a arrastar
 * manualmente pra ver o que está digitando.
 *
 * Corrigido de forma global aqui (não campo a campo) usando a API
 * VisualViewport: ela dispara um 'resize' quando o teclado abre/fecha
 * (a altura VISÍVEL da página encolhe, mesmo a página em si não mudando
 * de tamanho) — bem diferente de um resize de verdade (rotação de tela,
 * redimensionar a janela no desktop), que também dispara 'resize' só
 * que sem subtrair tanto da altura visível. Reagimos só quando a altura
 * cai de forma expressiva (> 100px, chute seguro pro tamanho mínimo de
 * qualquer teclado virtual real) E existe um campo de texto focado no
 * momento — nesses casos, e só nesses, rolamos esse campo pro centro da
 * área ainda visível. Sem VisualViewport (navegador muito antigo),
 * simplesmente não faz nada — sem quebrar nada, só sem a correção
 * extra.
 */
(function () {
  if (!window.visualViewport) return;
  var vv = window.visualViewport;
  var lastHeight = vv.height;

  vv.addEventListener('resize', function () {
    var shrank = vv.height < lastHeight - 100;
    lastHeight = vv.height;
    if (!shrank) return;

    var active = document.activeElement;
    if (!active || (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA')) return;

    // Pequeno atraso: a altura do viewport já mudou, mas a animação do
    // teclado subindo ainda pode estar em andamento em alguns
    // navegadores — rolar cedo demais mede a posição errada.
    setTimeout(function () {
      active.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 60);
  });
})();
