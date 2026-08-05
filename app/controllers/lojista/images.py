"""
Upload/edição/remoção de imagens de produto.

Estas rotas são consumidas exclusivamente via HTMX: cada uma retorna o
fragmento HTML da galeria (`_image_gallery.html`), que substitui a
galeria inteira no navegador (hx-target="#image-gallery",
hx-swap="outerHTML") — sem recarregar a página e sem navegação. A
mensagem de sucesso/erro vai embutida no próprio fragmento (não usamos
flash() aqui, já que a resposta não é uma renderização de página cheia).
"""

from flask import render_template

from app.forms.product_forms import ImageUploadForm
from app.services.product_service import ProductService
from app.repositories.product_image_repository import ProductImageRepository
from app.utils.decorators import lojista_required
from app.utils.tenant_context import get_current_tenant
from app.utils.uploads import InvalidImageError

from app.controllers.lojista import lojista_bp


def _render_gallery(product, message=None, message_type="success"):
    return render_template(
        "lojista/products/_image_gallery.html",
        product=product,
        image_form=ImageUploadForm(),
        message=message,
        message_type=message_type,
    )


@lojista_bp.route("/produtos/<int:product_id>/imagens", methods=["POST"])
@lojista_required
def images_upload(product_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    form = ImageUploadForm()
    if form.validate_on_submit():
        try:
            service.add_image(product, form.image.data)
        except InvalidImageError as exc:
            return _render_gallery(product, message=str(exc), message_type="danger")
        return _render_gallery(product, message="Imagem enviada com sucesso.", message_type="success")

    error_message = " ".join(form.image.errors) or "Não foi possível enviar a imagem."
    return _render_gallery(product, message=error_message, message_type="danger")


@lojista_bp.route("/produtos/<int:product_id>/imagens/<int:image_id>/substituir", methods=["POST"])
@lojista_required
def images_replace(product_id, image_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    image_repo = ProductImageRepository(tenant.id)
    image = image_repo.get_by_id(image_id)
    if image is None or image.product_id != product.id:
        from flask import abort

        abort(404)

    form = ImageUploadForm()
    if form.validate_on_submit():
        try:
            service.replace_image(image, form.image.data)
        except InvalidImageError as exc:
            return _render_gallery(product, message=str(exc), message_type="danger")
        return _render_gallery(product, message="Foto atualizada.", message_type="success")

    error_message = " ".join(form.image.errors) or "Não foi possível atualizar a foto."
    return _render_gallery(product, message=error_message, message_type="danger")


@lojista_bp.route("/produtos/<int:product_id>/imagens/<int:image_id>/excluir", methods=["POST"])
@lojista_required
def images_delete(product_id, image_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    image_repo = ProductImageRepository(tenant.id)
    image = image_repo.get_by_id(image_id)
    if image is None or image.product_id != product.id:
        from flask import abort

        abort(404)

    service.delete_image(image)
    return _render_gallery(product, message="Imagem removida.", message_type="success")


@lojista_bp.route("/produtos/<int:product_id>/imagens/<int:image_id>/principal", methods=["POST"])
@lojista_required
def images_set_primary(product_id, image_id):
    tenant = get_current_tenant()
    service = ProductService(tenant)
    product = service.get_or_404(product_id)

    image_repo = ProductImageRepository(tenant.id)
    image = image_repo.get_by_id(image_id)
    if image is None or image.product_id != product.id:
        from flask import abort

        abort(404)

    service.set_primary_image(image)
    return _render_gallery(product, message="Imagem principal atualizada.", message_type="success")
