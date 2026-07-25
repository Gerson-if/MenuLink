"""
Gestão de variações/complementos de um produto (ex: Tamanho, Molhos).

Assim como a galeria de imagens, estas rotas respondem com o fragmento
HTML da seção de complementos (`_complements_panel.html`), atualizado
via HTMX sem recarregar a página do produto.
"""

from flask import render_template

from app.forms.complement_forms import ComplementGroupForm, ComplementOptionForm
from app.services.complement_service import ComplementService
from app.services.product_service import ProductService
from app.utils.decorators import lojista_required
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp


def _render_panel(product):
    return render_template(
        "lojista/products/_complements_panel.html",
        product=product,
        group_form=ComplementGroupForm(),
        option_form=ComplementOptionForm(),
    )


@lojista_bp.route("/produtos/<int:product_id>/complementos", methods=["POST"])
@lojista_required
def complement_groups_create(product_id):
    tenant = get_current_tenant()
    product = ProductService(tenant).get_or_404(product_id)

    form = ComplementGroupForm()
    if form.validate_on_submit():
        ComplementService(tenant).create_group(
            product, name=form.name.data, is_variation=form.is_variation.data,
            is_required=form.is_required.data, single_choice=form.single_choice.data,
            allow_quantity=form.allow_quantity.data,
        )

    return _render_panel(product)


@lojista_bp.route("/produtos/<int:product_id>/complementos/<int:group_id>/excluir", methods=["POST"])
@lojista_required
def complement_groups_delete(product_id, group_id):
    tenant = get_current_tenant()
    product = ProductService(tenant).get_or_404(product_id)
    service = ComplementService(tenant)
    group = service.get_group_or_404(group_id)

    if group.product_id != product.id:
        from flask import abort

        abort(404)

    service.delete_group(group)
    return _render_panel(product)


@lojista_bp.route("/produtos/<int:product_id>/complementos/<int:group_id>/opcoes", methods=["POST"])
@lojista_required
def complement_options_create(product_id, group_id):
    tenant = get_current_tenant()
    product = ProductService(tenant).get_or_404(product_id)
    service = ComplementService(tenant)
    group = service.get_group_or_404(group_id)

    if group.product_id != product.id:
        from flask import abort

        abort(404)

    form = ComplementOptionForm()
    if form.validate_on_submit():
        service.add_option(group, name=form.name.data, extra_price_reais=float(form.extra_price.data or 0))

    return _render_panel(product)


@lojista_bp.route("/produtos/<int:product_id>/complementos/opcoes/<int:option_id>/excluir", methods=["POST"])
@lojista_required
def complement_options_delete(product_id, option_id):
    tenant = get_current_tenant()
    product = ProductService(tenant).get_or_404(product_id)
    service = ComplementService(tenant)
    option = service.get_option_or_404(option_id)

    if option.group.product_id != product.id:
        from flask import abort

        abort(404)

    service.delete_option(option)
    return _render_panel(product)
