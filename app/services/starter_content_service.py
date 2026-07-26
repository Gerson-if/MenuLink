"""
Conteúdo inicial ("starter kit") criado automaticamente para TODA loja
nova — tanto quando o Super Admin cria um lojista (admin/lojistas/novo)
quanto no auto-cadastro (/cadastro), já que os dois passam por
`AdminTenantService.create`, que chama `seed_starter_content` logo
depois de criar o tenant e o dono.

Objetivo: o lojista nunca cai num painel 100% vazio, sem noção de como
o cardápio público vai ficar — em vez disso, já vê a ESTRUTURA pronta
(categorias, um produto de exemplo em cada, um banner e um logo),
claramente marcada como exemplo ("Exemplo" no selo, descrição explicando
que é só um ponto de partida), e edita ou apaga à vontade a partir daí.

Diferente de app/utils/demo_media.py usado por `flask seed-db` (que
monta um cardápio TEMÁTICO completo — churrascaria "Braseiro & Cia" —
só pra demonstração), aqui o conteúdo é deliberadamente genérico: não
faz sentido inventar um tipo de cozinha para um lojista de verdade que
ainda nem configurou nada. As categorias escolhidas (Entradas, Pratos
principais, Bebidas, Sobremesas) são genéricas o bastante pra servir de
esqueleto pra praticamente qualquer tipo de comida.
"""

from app.extensions import db
from app.models import Banner, Category, Product, ProductImage
from app.utils.demo_media import generate_banner, generate_logo, generate_product_card, save_generated_image

_EXAMPLE_TAG = "Exemplo"
_EXAMPLE_DESCRIPTION = (
    "Isso é só um exemplo para você ver como fica no cardápio — edite as "
    "informações, troque a foto ou exclua este produto quando quiser."
)

# (nome da categoria, ícone do sistema, glifo da ilustração, nome do produto de exemplo)
_STARTER_CATEGORIES = [
    ("Entradas", "other", "bowl", "Entrada de exemplo"),
    ("Pratos principais", "other", "prato", "Prato de exemplo"),
    ("Bebidas", "drink", "cup", "Bebida de exemplo"),
    ("Sobremesas", "dessert", "cupcake", "Sobremesa de exemplo"),
]


def _unique_slug(base_text: str, model, tenant_id: int) -> str:
    from app.utils.slugs import generate_unique_slug

    def slug_exists(slug):
        return model.query.filter_by(tenant_id=tenant_id, slug=slug).first()

    return generate_unique_slug(base_text, slug_exists)


def seed_starter_content(tenant) -> None:
    """Chamado pelo `AdminTenantService.create` DEPOIS que o tenant e o
    dono já foram commitados (ver lá) — assim, qualquer problema aqui
    (ex: uma falha inesperada do Pillow gerando uma imagem) nunca
    impede a loja de ser criada: o rollback do `except` abaixo desfaz
    só o conteúdo de exemplo em si, nunca a loja/dono que já estão
    seguros no banco."""
    try:
        for order, (category_name, icon, glyph, product_name) in enumerate(_STARTER_CATEGORIES):
            category = Category(
                tenant_id=tenant.id,
                name=category_name,
                slug=_unique_slug(category_name, Category, tenant.id),
                icon=icon,
                display_order=order,
                is_active=True,
            )
            db.session.add(category)
            db.session.flush()

            product = Product(
                tenant_id=tenant.id,
                category_id=category.id,
                name=product_name,
                slug=_unique_slug(product_name, Product, tenant.id),
                description=_EXAMPLE_DESCRIPTION,
                price_cents=1990,
                tag=_EXAMPLE_TAG,
                is_active=True,
                display_order=0,
            )
            db.session.add(product)
            db.session.flush()

            card = generate_product_card(glyph, product_name)
            relative_path, color = save_generated_image(card, f"tenant_{tenant.id}/products/{product.id}")
            db.session.add(ProductImage(
                tenant_id=tenant.id,
                product_id=product.id,
                file_path=relative_path,
                dominant_color=color,
                is_primary=True,
                display_order=0,
            ))

        banner_image = generate_banner(
            "combo",
            "Bem-vindo(a) ao seu cardápio digital!",
            "Personalize este banner e suas fotos quando quiser.",
        )
        banner_path, banner_color = save_generated_image(banner_image, f"tenant_{tenant.id}/banners")
        db.session.add(Banner(
            tenant_id=tenant.id,
            title="Bem-vindo(a) ao seu cardápio digital!",
            subtitle="Personalize este banner e suas fotos quando quiser.",
            image_path=banner_path,
            dominant_color=banner_color,
            display_order=0,
            is_active=True,
        ))

        if not tenant.logo_path:
            logo = generate_logo(tenant.name[:1].upper(), glyph="combo")
            logo_path, _color = save_generated_image(logo, f"tenant_{tenant.id}/logo")
            tenant.logo_path = logo_path

        db.session.commit()
    except Exception:  # noqa: BLE001 - conteúdo de exemplo nunca deve impedir a criação da loja
        db.session.rollback()
