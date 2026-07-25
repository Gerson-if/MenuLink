"""
Catálogo de produtos.

Estrutura pensada para refletir o que já existia no cardapio.html original:
- Product tem N imagens (ProductImage), uma marcada como principal.
- Product pode ter grupos de variação (ex: "Tamanho": P / M / G) --
  ComplementGroup com is_variation=True, single_choice=True.
- Product pode ter grupos de complemento (ex: "Molhos", múltipla escolha) --
  ComplementGroup com is_variation=False.

Um único conceito (ComplementGroup + ComplementOption) cobre os dois casos
do frontend antigo (variações e complementos), diferenciando por flags,
o que evita duplicar tabelas quase idênticas.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin


class Product(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)

    name = Column(String(150), nullable=False)
    slug = Column(String(180), nullable=False)
    description = Column(Text, nullable=True)
    price_cents = Column(Integer, nullable=False)
    cost_price_cents = Column(Integer, nullable=True)  # preço de custo, opcional — usado para calcular margem real

    # Selo curto opcional mostrado no card do produto no cardápio público
    # (ex: "Mais pedido", "Novidade", "Promoção"). Puramente decorativo.
    tag = Column(String(40), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    display_order = Column(Integer, nullable=False, default=0)

    tenant = relationship("Tenant", back_populates="products")
    category = relationship("Category", back_populates="products")
    images = relationship(
        "ProductImage", back_populates="product",
        cascade="all, delete-orphan", order_by="ProductImage.display_order",
    )
    complement_groups = relationship(
        "ComplementGroup", back_populates="product",
        cascade="all, delete-orphan", order_by="ComplementGroup.display_order",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_product_tenant_slug"),
        CheckConstraint("price_cents >= 0", name="ck_product_price_non_negative"),
    )

    @property
    def primary_image(self):
        for img in self.images:
            if img.is_primary:
                return img
        return self.images[0] if self.images else None

    @property
    def is_untouched_draft(self) -> bool:
        """True só para um rascunho recém-criado por "+ Novo produto" que
        o lojista nunca chegou a editar (nome/preço/categoria ainda nos
        valores padrão, sem fotos nem complementos). Usado para apagar
        o rascunho automaticamente se o drawer for fechado sem uso, em
        vez de deixar um card "Novo produto" vazio parado no cardápio."""
        return (
            self.name == "Novo produto"
            and self.price_cents == 0
            and not self.description
            and not self.tag
            and self.category_id is None
            and not self.is_active
            and not self.images
            and not self.complement_groups
        )

    @property
    def margin_percent(self) -> float | None:
        """Margem real = (preço venda - preço custo) / preço venda, em %.
        Retorna None se não houver preço de custo cadastrado."""
        if not self.cost_price_cents or self.price_cents <= 0:
            return None
        return round((self.price_cents - self.cost_price_cents) / self.price_cents * 100, 1)

    def __repr__(self):
        return f"<Product {self.name} (tenant={self.tenant_id})>"


class ProductImage(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    file_path = Column(String(255), nullable=False)  # caminho relativo em static/uploads/<tenant>/...
    # Cor média das bordas da foto (ver `_dominant_edge_color` em
    # app/utils/uploads.py) — fundo sólido atrás da imagem no carrossel
    # do modal de produto quando a caixa não bate com a proporção da foto.
    dominant_color = Column(String(7), nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)

    product = relationship("Product", back_populates="images")

    def __repr__(self):
        return f"<ProductImage {self.file_path}>"


class ComplementGroup(db.Model, TenantScopedMixin, TimestampMixin):
    """
    Grupo de opções ligado a um produto.

    is_variation=True  -> representa uma VARIAÇÃO do produto em si
                           (ex: "Tamanho"), normalmente single_choice=True
                           e obrigatória (is_required=True).
    is_variation=False -> representa um COMPLEMENTO/adicional
                           (ex: "Molhos"), pode ser múltipla escolha.
    """

    __tablename__ = "complement_groups"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(100), nullable=False)  # ex: "Tamanho", "Molhos", "Ponto da carne"
    is_variation = Column(Boolean, nullable=False, default=False)
    is_required = Column(Boolean, nullable=False, default=False)
    single_choice = Column(Boolean, nullable=False, default=False)
    # Cliente escolhe quantas unidades de cada opção quer (ex: "2x Coca-
    # Cola 200ml"), em vez de só marcar/desmarcar. Só faz sentido em
    # grupos de múltipla escolha que não sejam variação — forçado pelo
    # service na criação (ver ComplementService.create_group).
    allow_quantity = Column(Boolean, nullable=False, default=False)
    max_choices = Column(Integer, nullable=True)  # limite de escolhas (null = sem limite)
    display_order = Column(Integer, nullable=False, default=0)

    product = relationship("Product", back_populates="complement_groups")
    options = relationship(
        "ComplementOption", back_populates="group",
        cascade="all, delete-orphan", order_by="ComplementOption.display_order",
    )

    def __repr__(self):
        return f"<ComplementGroup {self.name}>"


class ComplementOption(db.Model, TenantScopedMixin, TimestampMixin):
    __tablename__ = "complement_options"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("complement_groups.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(100), nullable=False)  # ex: "Grande", "Barbecue", "Ao ponto"
    extra_price_cents = Column(Integer, nullable=False, default=0)  # pode ser negativo? não -- CheckConstraint abaixo
    is_active = Column(Boolean, nullable=False, default=True)
    display_order = Column(Integer, nullable=False, default=0)

    group = relationship("ComplementGroup", back_populates="options")

    __table_args__ = (
        CheckConstraint("extra_price_cents >= 0", name="ck_option_price_non_negative"),
    )

    def __repr__(self):
        return f"<ComplementOption {self.name}>"
