from flask import abort

from app.extensions import db
from app.models import Product, ProductImage
from app.repositories.product_repository import ProductRepository
from app.repositories.product_image_repository import ProductImageRepository
from app.utils.slugs import generate_unique_slug
from app.utils.uploads import save_product_image, delete_product_image_file, InvalidImageError


class ProductLimitReachedError(Exception):
    pass


class ProductService:
    def __init__(self, tenant):
        self.tenant = tenant
        self.repo = ProductRepository(tenant.id)
        self.image_repo = ProductImageRepository(tenant.id)

    # --- Consulta ---
    def list_all(self, category_id: int | None = None):
        return self.repo.list_ordered(category_id=category_id)

    def get_or_404(self, product_id: int) -> Product:
        product = self.repo.get_by_id(product_id)
        if product is None:
            abort(404)
        return product

    # --- CRUD ---
    def create(self, *, name: str, description: str, price_reais: float, category_id: int | None, is_active: bool, cost_price_reais: float | None = None, tag: str | None = None) -> Product:
        plan = self.tenant.plan
        if plan and plan.max_products is not None and self.repo.count_completed() >= plan.max_products:
            raise ProductLimitReachedError(
                f"Seu plano permite no máximo {plan.max_products} produtos. "
                "Fale com o suporte para ampliar seu plano."
            )

        slug = generate_unique_slug(name, self.repo.get_by_slug)
        max_order = db.session.query(db.func.max(Product.display_order)).filter(
            Product.tenant_id == self.tenant.id
        ).scalar() or 0

        product = Product(
            tenant_id=self.tenant.id,
            category_id=category_id,
            name=name.strip(),
            slug=slug,
            description=(description or "").strip() or None,
            price_cents=round(price_reais * 100),
            cost_price_cents=round(cost_price_reais * 100) if cost_price_reais is not None else None,
            tag=(tag or "").strip() or None,
            is_active=is_active,
            display_order=max_order + 1,
        )
        db.session.add(product)
        db.session.commit()
        return product

    def update(self, product: Product, *, name: str, description: str, price_reais: float, category_id: int | None, is_active: bool, cost_price_reais: float | None = None, tag: str | None = None) -> Product:
        if name.strip() != product.name:
            product.slug = generate_unique_slug(name, self.repo.get_by_slug, current_id=product.id)
        product.name = name.strip()
        product.tag = (tag or "").strip() or None
        product.description = (description or "").strip() or None
        product.price_cents = round(price_reais * 100)
        product.cost_price_cents = round(cost_price_reais * 100) if cost_price_reais is not None else None
        product.category_id = category_id
        product.is_active = is_active
        db.session.commit()
        return product

    def toggle_active(self, product: Product) -> Product:
        product.is_active = not product.is_active
        db.session.commit()
        return product

    def delete(self, product: Product) -> None:
        # Apaga também os arquivos físicos das imagens (o cascade do
        # SQLAlchemy só remove os registros do banco).
        for image in list(product.images):
            delete_product_image_file(image.file_path)
        db.session.delete(product)
        db.session.commit()

    # --- Imagens ---
    def add_image(self, product: Product, file_storage) -> ProductImage:
        try:
            relative_path, dominant_color = save_product_image(self.tenant.id, product.id, file_storage)
        except InvalidImageError as exc:
            raise

        is_first_image = len(product.images) == 0
        max_order = max([img.display_order for img in product.images], default=-1)

        image = ProductImage(
            tenant_id=self.tenant.id,
            product_id=product.id,
            file_path=relative_path,
            dominant_color=dominant_color,
            is_primary=is_first_image,  # a primeira imagem vira principal automaticamente
            display_order=max_order + 1,
        )
        db.session.add(image)
        db.session.commit()
        return image

    def replace_image(self, image: ProductImage, file_storage) -> ProductImage:
        """Troca o ARQUIVO de uma foto já existente, mantendo o mesmo
        registro (posição na galeria, se é a principal etc.) — usada
        quando o lojista clica direto numa foto já enviada pra trocá-la,
        em vez de precisar remover e enviar de novo do zero (ver
        _image_gallery.html). Só apaga o arquivo antigo DEPOIS do commit
        do novo caminho dar certo — se `save_product_image` falhar (ex:
        arquivo inválido), a foto antiga continua no ar normalmente."""
        old_path = image.file_path
        relative_path, dominant_color = save_product_image(self.tenant.id, image.product_id, file_storage)
        image.file_path = relative_path
        image.dominant_color = dominant_color
        db.session.commit()
        delete_product_image_file(old_path)
        return image

    def delete_image(self, image: ProductImage) -> None:
        product = image.product
        was_primary = image.is_primary

        delete_product_image_file(image.file_path)
        db.session.delete(image)
        db.session.flush()

        if was_primary:
            next_image = (
                ProductImage.query.filter_by(product_id=product.id, tenant_id=self.tenant.id)
                .order_by(ProductImage.display_order)
                .first()
            )
            if next_image:
                next_image.is_primary = True

        db.session.commit()

    def set_primary_image(self, image: ProductImage) -> None:
        self.image_repo.clear_primary_flag(image.product_id)
        image.is_primary = True
        db.session.commit()
