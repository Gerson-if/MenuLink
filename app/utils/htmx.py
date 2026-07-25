"""
Helper para respostas HTMX que devolvem só um fragmento (drawers de
criar/editar plano, produto, categoria, banner, lojista etc.).

Sem isso, salvar pelo drawer (fluxo padrão do painel, via HTMX) trocava
só o HTML do formulário e a listagem de fundo — sem nenhum aviso visível
de que a gravação deu certo, diferente do fluxo de página inteira (que
usa flash() normalmente). O toast do `flash()` só é desenhado no
`base.html` de uma página completa, então uma resposta parcial nunca o
mostra: por isso o "toastMessage" viaja no próprio header HX-Trigger e é
renderizado no cliente (ver app/static/js/comanda.js, listener de
'toastMessage' em document.body).
"""

import json

from flask import make_response


def hx_fragment_response(html: str, *, trigger: str | None = None, toast: str | None = None, toast_category: str = "success"):
    """Monta a resposta HTMX de um fragmento (form re-renderizado após
    salvar), combinando o evento de "listagem precisa atualizar"
    (`trigger`, ex.: "planSaved") com um toast de confirmação
    (`toast`) no mesmo header HX-Trigger, como um único JSON."""
    response = make_response(html)

    events = {}
    if trigger:
        events[trigger] = True
    if toast:
        events["toastMessage"] = {"message": toast, "category": toast_category}

    if events:
        response.headers["HX-Trigger"] = json.dumps(events)

    return response
