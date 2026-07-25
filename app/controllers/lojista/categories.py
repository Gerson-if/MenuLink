from flask import flash, make_response, redirect, render_template, request, url_for

from app.forms.category_forms import CategoryForm
from app.models.category import CATEGORY_ICON_CHOICES
from app.services.category_service import CategoryService, CategoryLimitReachedError
from app.utils.decorators import lojista_required
from app.utils.htmx import hx_fragment_response
from app.utils.tenant_context import get_current_tenant
from app.utils.uploads import InvalidImageError

from app.controllers.lojista import lojista_bp


def _rerender_category_fragment(category, *, trigger=None, toast=None):
    """Ver _rerender_banner_fragment em banners.py — mesmo padrão pro
    drawer de categorias: devolve só o fragmento, com HX-Trigger opcional
    pra avisar a listagem de fundo, e um toast de confirmação (ver
    app/utils/htmx.py) — sem ele, salvar pelo drawer não dava nenhum
    sinal visível de que a gravação deu certo."""
    form = CategoryForm(obj=category)
    html = render_template(
        "lojista/categories/_form_fragment.html", form=form, category=category,
        form_action=url_for("lojista.categories_edit", category_id=category.id), hide_back_link=True,
    )
    return hx_fragment_response(html, trigger=trigger, toast=toast)


@lojista_bp.route("/categorias")
@lojista_required
def categories_list():
    tenant = get_current_tenant()
    service = CategoryService(tenant)
    categories = service.list_all()
    return render_template(
        "lojista/categories/list.html", categories=categories, icon_labels=dict(CATEGORY_ICON_CHOICES),
    )


@lojista_bp.route("/categorias/nova", methods=["GET", "POST"])
@lojista_required
def categories_create():
    tenant = get_current_tenant()
    form = CategoryForm()
    is_hx = bool(request.headers.get("HX-Request"))

    if form.validate_on_submit():
        service = CategoryService(tenant)
        try:
            category = service.create(
                name=form.name.data, is_active=form.is_active.data, icon=form.icon.data,
                icon_image_file=form.icon_image.data,
            )
        except CategoryLimitReachedError as exc:
            flash(str(exc), "warning")
        except InvalidImageError as exc:
            flash(str(exc), "danger")
        else:
            if is_hx:
                return _rerender_category_fragment(category, trigger="categorySaved", toast="Categoria criada com sucesso.")
            flash("Categoria criada com sucesso.", "success")
            return redirect(url_for("lojista.categories_list"))

    if is_hx:
        return render_template(
            "lojista/categories/_form_fragment.html", form=form, category=None,
            form_action=url_for("lojista.categories_create"), hide_back_link=True,
        )
    return render_template("lojista/categories/form.html", form=form, category=None)


@lojista_bp.route("/categorias/<int:category_id>/editar", methods=["GET", "POST"])
@lojista_required
def categories_edit(category_id):
    tenant = get_current_tenant()
    service = CategoryService(tenant)
    category = service.get_or_404(category_id)
    is_hx = bool(request.headers.get("HX-Request"))

    form = CategoryForm(obj=category)

    if form.validate_on_submit():
        try:
            service.update(
                category, name=form.name.data, is_active=form.is_active.data, icon=form.icon.data,
                icon_image_file=form.icon_image.data,
            )
        except InvalidImageError as exc:
            flash(str(exc), "danger")
        else:
            if is_hx:
                return _rerender_category_fragment(category, trigger="categorySaved", toast="Categoria atualizada com sucesso.")
            flash("Categoria atualizada com sucesso.", "success")
            return redirect(url_for("lojista.categories_list"))

    if is_hx:
        return render_template(
            "lojista/categories/_form_fragment.html", form=form, category=category,
            form_action=url_for("lojista.categories_edit", category_id=category.id), hide_back_link=True,
        )
    return render_template("lojista/categories/form.html", form=form, category=category)


@lojista_bp.route("/categorias/<int:category_id>/icone/remover", methods=["POST"])
@lojista_required
def categories_remove_icon(category_id):
    """Ação isolada do ícone de remover no dropzone — some com o ícone
    próprio na hora, sem depender do resto do formulário da categoria
    (ver CategoryForm/categories_edit)."""
    if not request.headers.get("HX-Request"):
        return "", 400

    tenant = get_current_tenant()
    service = CategoryService(tenant)
    category = service.get_or_404(category_id)
    service.remove_icon_image(category)

    form = CategoryForm(obj=category)
    html = render_template("lojista/categories/_icon_dropzone.html", form=form, category=category)
    return make_response(html)


@lojista_bp.route("/categorias/<int:category_id>/alternar", methods=["POST"])
@lojista_required
def categories_toggle(category_id):
    tenant = get_current_tenant()
    service = CategoryService(tenant)
    category = service.get_or_404(category_id)
    service.toggle_active(category)
    return render_template("lojista/categories/_status_badge.html", category=category)


@lojista_bp.route("/categorias/<int:category_id>/excluir", methods=["POST"])
@lojista_required
def categories_delete(category_id):
    tenant = get_current_tenant()
    service = CategoryService(tenant)
    category = service.get_or_404(category_id)

    service.delete(category)

    if request.headers.get("HX-Request"):
        # A linha da tabela é removida no próprio navegador via hx-swap
        # (hx-target="closest tr"); uma resposta vazia é suficiente.
        return ""

    flash("Categoria excluída. Os produtos dela ficaram sem categoria.", "info")
    return redirect(url_for("lojista.categories_list"))
