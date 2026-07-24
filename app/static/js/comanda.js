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
 * `defer` garante que o DOM (incluindo #confirmModal) já existe quando
 * este script roda.
 */
(function () {
  const modalEl = document.getElementById('confirmModal');
  if (!modalEl) return;

  const messageEl = document.getElementById('confirmModalMessage');
  const confirmBtn = document.getElementById('confirmModalConfirmBtn');
  const modal = new bootstrap.Modal(modalEl);
  let pendingAction = null;

  function askConfirmation(message, onConfirm) {
    messageEl.textContent = message;
    pendingAction = onConfirm;
    modal.show();
  }

  confirmBtn.addEventListener('click', () => {
    const action = pendingAction;
    pendingAction = null;
    modal.hide();
    if (action) action();
  });

  document.body.addEventListener('htmx:confirm', (evt) => {
    if (!evt.detail.question) return;
    evt.preventDefault();
    askConfirmation(evt.detail.question, () => evt.detail.issueRequest(true));
  });

  document.addEventListener('submit', (evt) => {
    const form = evt.target;
    if (!(form instanceof HTMLFormElement)) return;
    const message = form.getAttribute('data-confirm-message');
    if (!message || form.dataset.confirmBypass === '1') return;
    evt.preventDefault();
    askConfirmation(message, () => {
      form.dataset.confirmBypass = '1';
      form.requestSubmit();
    });
  });
})();

/*
 * Tooltip (Bootstrap) nos ícones do menu lateral — mostra o nome da
 * seção ao passar o mouse, útil sobretudo com a sidebar recolhida (só
 * ícone, sem o texto do nav-label). O botão de recolher/expandir fica
 * de fora de propósito: o texto dele muda de acordo com o estado
 * (collapsed) via Alpine (:title, atualiza a cada clique), e o
 * Bootstrap Tooltip cacheia o título só na inicialização — usar os
 * dois juntos deixaria a dica dessincronizada depois do primeiro clique.
 */
(function () {
  document.querySelectorAll('.sidebar [data-bs-toggle="tooltip"]').forEach((el) => new bootstrap.Tooltip(el));
})();

/*
 * Mantém o item ativo visível no menu lateral (.nav-food é uma lista
 * rolável — `overflow-y: auto`, ver comanda.css). Esta app é
 * multi-página (sem hx-boost no menu): cada clique em uma opção mais
 * abaixo (Clientes, Cardápio, Vendas, Configurações...) causa um reload
 * completo, e o navegador sempre recria o scroll do `.nav-food` do zero
 * (topo), mesmo que o item ativo esteja mais para baixo na lista. Sem
 * isso, a sidebar "esquecia" a seleção visualmente — o item continuava
 * ativo (classe `.active` no link certo), só que fora da área visível,
 * dando a impressão de que a navegação tinha voltado pro topo e perdido
 * a seleção. `block: 'nearest'` garante que só o `.nav-food` (ancestral
 * rolável mais próximo) é ajustado, sem mexer no scroll da página.
 */
(function () {
  const activeLink = document.querySelector('.nav-food .nav-link.active');
  if (activeLink) activeLink.scrollIntoView({ block: 'nearest' });
})();

/*
 * Botão de mostrar/ocultar senha (login e outros formulários com
 * .password-toggle-btn) — troca type="password" por type="text" e o
 * ícone de olho. Progressive enhancement: sem JS o campo continua um
 * <input type="password"> normal, só sem o botão.
 */
(function () {
  document.querySelectorAll('.password-toggle-btn').forEach((btn) => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    btn.addEventListener('click', () => {
      const showingNow = input.type === 'text';
      input.type = showingNow ? 'password' : 'text';
      btn.querySelector('i').className = showingNow ? 'bi bi-eye' : 'bi bi-eye-slash';
      btn.setAttribute('aria-label', showingNow ? 'Mostrar senha' : 'Ocultar senha');
    });
  });
})();

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
 * "+ Novo produto" cria o rascunho assim que o lojista clica, pra já
 * abrir a edição completa (fotos/complementos disponíveis desde o
 * início — ver lojista.products_create_draft). Se ele fechar a barra
 * lateral sem preencher nada, isso deixaria um card "Novo produto"
 * vazio parado no cardápio. Ao fechar o drawer, avisa o servidor pra
 * apagar o rascunho — mas só apaga se ele ainda estiver intocado
 * (Product.is_untouched_draft); qualquer dado salvo, foto ou
 * complemento cancela a limpeza.
 */
(function () {
  const drawer = document.getElementById('editDrawer');
  if (!drawer) return;

  drawer.addEventListener('hidden.bs.offcanvas', () => {
    const marker = document.getElementById('drawer-product-id');
    if (!marker) return;
    htmx.ajax('POST', marker.dataset.discardUrl, { swap: 'none' }).then(() => {
      document.body.dispatchEvent(new Event('productSaved'));
    });
  });
})();

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
(function () {
  const els = document.querySelectorAll('[data-reveal]');
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
})();
