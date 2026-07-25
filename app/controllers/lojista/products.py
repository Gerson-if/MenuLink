from markupsafe import escape

from flask import flash, redirect, render_template, request, url_for

from app.forms.product_forms import ProductForm, ImageUploadForm
from app.forms.complement_forms import ComplementGroupForm, ComplementOptionForm
from app.services.category_service import CategoryService
from app.services.product_service import ProductService, ProductLimitReachedError
from app.utils.decorators import lojista_required
from app.utils.htmx import hx_fragment_response
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp


def _populate_category_choices(form, tenant):
    categories = CategoryService(tenant).list_all()
    form.category_id.choices = [(0, "Sem categoria")] + [(c.id, c.name) for c in categories]


def _rerender_product_fragment(tenant, product, *, trigger=None, toast=None):
    """Usado depois de criar/atualizar um produto vindo do drawer (HTMX):
    devolve só o fragmento do form (não a página inteira), com um
    HX-Trigger opcional pra avisar a listagem de fundo (ver
    products/list.html, hx-trigger="productSaved from:body") que precisa
    se atualizar, e um toast de confirmação (ver app/utils/htmx.py) —
    sem ele, salvar pelo drawer não dava nenhum sinal visível de que a
    gravação deu certo."""
    form = ProductForm(obj=product)
    form.price.data = product.price_cents / 100
    form.cost_price.data = (product.cost_price_cents / 100) if product.cost_price_cents else None
    form.category_id.data = product.category_id or 0
    _populate_category_choices(form, tenant)

    html = render_template(
        "lojista/products/_form_fragment.html",
        form=form, product=product,
        image_form=ImageUploadForm(), group_form=ComplementGroupForm(), option_form=ComplementOptionForm(),
        form_action=url_for("lojista.products_edit", product_id=product.id), hide_back_link=True,
    )
    return hx_fragment_response(html, trigger=trigger, toast=toast)


@lojista_bp.route("/produtos")
@lojista_required
def products_list():
    tenant = get_current_tenant()
    category_id = request.args.get("categoria", type=int)
    page = request.args.get("page", 1, type=int)

    service = ProductService(tenant)
    pagination = service.repo.paginated(page=page, per_page=12, category_id=category_id)
    categories = CategoryService(tenant).list_all()

    return render_template(
        "lojista/products/list.html",
        products=pagination.items,
        pagination=pagination,
        categories=categories,
        selected_category_id=category_id,
    )


@lojista_bp.route("/produtos/rascunho", methods=["POST"])
@lojista_required
def products_create_draft():
    """Cria um produto "rascunho" (sem preço, inativo) e já abre a edição
    completa (galeria de fotos e complementos disponíveis desde o primeiro
    instante) — sem precisar "salvar antes". Chamado via HTMX pelo botão
    "+ Novo produto" (abre direto na barra lateral direita); o nome
    padrão "Novo produto" fica pré-preenchido no campo, pronto pra
    renomear. Rascunhos (price_cents == 0) não contam contra o limite do
    plano, ver ProductRepository.count_completed()."""
    tenant = get_current_tenant()
    name = (request.form.get("name") or "").strip() or "Novo produto"
    is_hx = bool(request.headers.get("HX-Request"))

    service = ProductService(tenant)
    try:
        product = service.create(
            name=name, description="", price_reais=0, cost_price_reais=None,
            tag=None, category_id=None, is_active=False,
        )
    except ProductLimitReachedError as exc:
        if is_hx:
            return f'<div class="panel"><p class="upload-error mb-0">{escape(str(exc))}</p></div>'
        flash(str(exc), "warning")
        return redirect(url_for("lojista.products_list"))

    if is_hx:
        return _rerender_product_fragment(tenant, product, trigger="productSaved")

    return redirect(url_for("lojista.products_edit", product_id=product.id))


@lojista_bp.route("/produtos/<int:product_id>/descartar-rascunho", methods=["POST"])
@lojista_required
def products_discard_draft(product_id):
    """Chamado via JS quando a barra lateral de edição é fechada (ver
    comanda.js, listener de 'hidden.bs.offcanvas'). Só apaga se o
    produto ainda for um rascunho intocado (Product.is_untouched_draft)
    — se o lojista salvou qualquer dado, enviou foto ou criou um
    complemento, o produto fica como está."""
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    if product.is_untouched_draft:
        service.delete(product)

    return ("", 204)


@lojista_bp.route("/produtos/novo", methods=["GET", "POST"])
@lojista_required
def products_create():
    tenant = get_current_tenant()
    form = ProductForm()
    _populate_category_choices(form, tenant)

    if form.validate_on_submit():
        service = ProductService(tenant)
        try:
            product = service.create(
                name=form.name.data,
                description=form.description.data,
                price_reais=float(form.price.data),
                cost_price_reais=float(form.cost_price.data) if form.cost_price.data is not None else None,
                tag=form.tag.data,
                category_id=form.category_id.data or None,
                is_active=form.is_active.data,
            )
        except ProductLimitReachedError as exc:
            flash(str(exc), "warning")
        else:
            flash("Produto criado com sucesso. Agora envie as fotos dele.", "success")
            return redirect(url_for("lojista.products_edit", product_id=product.id))

    return render_template("lojista/products/form.html", form=form, product=None)


@lojista_bp.route("/produtos/<int:product_id>/editar", methods=["GET", "POST"])
@lojista_required
def products_edit(product_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)
    is_hx = bool(request.headers.get("HX-Request"))

    form = ProductForm(obj=product)
    if request.method == "GET":
        form.price.data = product.price_cents / 100
        form.cost_price.data = (product.cost_price_cents / 100) if product.cost_price_cents else None
        form.category_id.data = product.category_id or 0
    _populate_category_choices(form, tenant)

    if form.validate_on_submit():
        service.update(
            product,
            name=form.name.data,
            description=form.description.data,
            price_reais=float(form.price.data),
            cost_price_reais=float(form.cost_price.data) if form.cost_price.data is not None else None,
            tag=form.tag.data,
            category_id=form.category_id.data or None,
            is_active=form.is_active.data,
        )
        if is_hx:
            # Continua no drawer (fecha só quando o lojista clicar no X) —
            # a listagem de fundo se atualiza sozinha via HX-Trigger.
            return _rerender_product_fragment(
                tenant, product, trigger="productSaved", toast="Produto atualizado com sucesso.",
            )

        flash("Produto atualizado com sucesso.", "success")
        return redirect(url_for("lojista.products_edit", product_id=product.id))

    image_form = ImageUploadForm()
    context = dict(
        form=form, product=product, image_form=image_form,
        group_form=ComplementGroupForm(), option_form=ComplementOptionForm(),
    )

    if is_hx:
        # Chamado pelo botão "Editar" da listagem — abre na barra lateral
        # direita (offcanvas), carregando só o fragmento do form.
        return render_template(
            "lojista/products/_form_fragment.html",
            form_action=url_for("lojista.products_edit", product_id=product.id), hide_back_link=True,
            **context,
        )

    return render_template("lojista/products/form.html", **context)


@lojista_bp.route("/produtos/<int:product_id>/alternar", methods=["POST"])
@lojista_required
def products_toggle(product_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)
    service.toggle_active(product)
    return render_template("lojista/products/_status_badge.html", product=product)


@lojista_bp.route("/produtos/<int:product_id>/excluir", methods=["POST"])
@lojista_required
def products_delete(product_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    service.delete(product)

    if request.headers.get("HX-Request"):
        # Chamado a partir do card na listagem — o próprio card é removido
        # no navegador (hx-target="#product-card-<id>"), sem navegação.
        return ""

    flash("Produto excluído.", "info")
    return redirect(url_for("lojista.products_list"))
