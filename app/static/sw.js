/*
 * Service worker do Menu Link — escopo DELIBERADAMENTE limitado a
 * arquivos estáticos (CSS, JS, ícones, fontes locais). NUNCA cacheia
 * HTML, navegação de página ou chamadas de API/htmx: isso é
 * multi-tenant e cheio de dado sensível por sessão (pedidos, clientes,
 * configurações de cada loja) — cachear a navegação correria o risco
 * de mostrar tela de um lojista pra outro num aparelho compartilhado,
 * ou dado desatualizado (pedido/preço antigo) depois de uma alteração
 * no painel. Isso é o que dá a "sensação de app instalado" mais fluida
 * (CSS/JS carregam na hora, sem esperar rede) sem nenhum dos riscos de
 * cachear conteúdo dinâmico.
 *
 * As URLs de estático já têm cache-busting por ?v=<mtime> (ver
 * static_versioned() em app/__init__.py) — plugin novo depois de um
 * deploy sempre baixa a versão nova, nunca fica "preso" numa versão
 * antiga do CSS/JS.
 */

const CACHE_NAME = "menulink-static-v1";
const APP_SHELL = [
  "/static/css/comanda.css",
  "/static/js/comanda.js",
  "/static/img/favicon.svg",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
  "/static/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .catch(() => {
        // Falha ao pré-cachear (ex: offline na primeira instalação) não
        // deve impedir o service worker de ativar — ele ainda funciona
        // em modo "cache conforme uso" via fetch abaixo.
      })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

function isCacheableStaticAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (!url.pathname.startsWith("/static/")) return false;
  // uploads (logos, banners, fotos de produto) mudam sem trocar de
  // nome de arquivo — nunca cachear, pra edição de imagem no painel
  // aparecer na hora, sem exigir um "hard refresh".
  if (url.pathname.startsWith("/static/uploads/")) return false;
  return true;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (!isCacheableStaticAsset(url)) return; // deixa passar pro comportamento normal do navegador

  // stale-while-revalidate: responde do cache na hora (fluido, sem
  // esperar rede) e atualiza o cache em segundo plano pra próxima vez —
  // seguro aqui porque a URL já tem ?v=<mtime>, então uma versão nova
  // do arquivo vem com URL diferente, nunca conflita com a antiga.
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response && response.ok) {
              cache.put(request, response.clone());
            }
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    )
  );
});
