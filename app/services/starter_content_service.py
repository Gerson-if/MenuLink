"""
Conteúdo inicial ("starter kit") criado automaticamente para TODA loja
nova — tanto quando o Super Admin cria um lojista (admin/lojistas/novo)
quanto no auto-cadastro (/cadastro), já que os dois passam por
`AdminTenantService.create`, que chama `seed_starter_content` logo
depois de criar o tenant e o dono.

Objetivo: o lojista nunca cai num painel 100% vazio, sem noção de como
o cardápio público vai ficar — em vez disso, já vê a ESTRUTURA pronta
(categorias com produtos de exemplo, um deles já com variação e
adicionais cadastrados, um carrossel com vários banners e um logo),
claramente marcada como exemplo ("Exemplo" no selo, descrição explicando
que é só um ponto de partida), e edita ou apaga à vontade a partir daí.
Além de preencher o painel, isso também DEMONSTRA recursos que, de outra
forma, um lojista recém-chegado nem saberia que existem (o carrossel de
banners só faz sentido com mais de uma imagem; variações/adicionais só
se um produto já tiver algum cadastrado).

Diferente de app/utils/demo_media.py usado por `flask seed-db` (que
monta um cardápio TEMÁTICO completo — churrascaria "Braseiro & Cia" —
só pra demonstração), aqui o conteúdo é deliberadamente genérico: não
faz sentido inventar um tipo de cozinha para um lojista de verdade que
ainda nem configurou nada. As categorias escolhidas (Entradas, Pratos
principais, Bebidas, Sobremesas) são genéricas o bastante pra servir de
esqueleto pra praticamente qualquer tipo de comida.
"""

from app.extensions import db
from app.models import Banner, Category, ComplementGroup, ComplementOption, Product, ProductImage
from app.utils.demo_media import (
    generate_banner_from_icon,
    generate_logo,
    generate_product_card_from_icon,
    save_generated_image,
)

_ICONS_DIR = "starter_content/icons"

_EXAMPLE_TAG = "Exemplo"
_EXAMPLE_DESCRIPTION = (
    "Isso é só um exemplo para você ver como fica no cardápio — edite as "
    "informações, troque a foto ou exclua este produto quando quiser."
)
_EXAMPLE_DESCRIPTION_WITH_COMPLEMENTS = (
    "Isso é só um exemplo — já veio com uma variação (Tamanho) e "
    "adicionais cadastrados, pra você ver como fica pro cliente escolher "
    "no cardápio. Edite, apague ou adicione os seus em Cardápio → "
    "Variações & Complementos."
)

# Cada entrada: nome da categoria, ícone do sistema, glifo da ilustração,
# nome do produto de exemplo, preço (em centavos) condizente com o tipo
# de item, e se esse produto ganha uma variação + adicionais de exemplo
# (só um, pra não exagerar na quantidade de conteúdo fictício — o
# suficiente pra demonstrar o recurso).
_STARTER_CATEGORIES = [
    {
        "category": "Entradas", "icon": "other", "glyph": "bowl",
        "static_icon": "entrada-bowl.png",
        "product": "Entrada de exemplo", "price_cents": 1490,
    },
    {
        "category": "Pratos principais", "icon": "other", "glyph": "prato",
        "static_icon": "prato-combo.png",
        "product": "Prato de exemplo", "price_cents": 3490,
        "with_complements": True,
    },
    {
        "category": "Bebidas", "icon": "drink", "glyph": "cup",
        "static_icon": "bebida-copo.png",
        "product": "Bebida de exemplo", "price_cents": 890,
    },
    {
        "category": "Sobremesas", "icon": "dessert", "glyph": "cupcake",
        "static_icon": "sobremesa-sorvete.png",
        "product": "Sobremesa de exemplo", "price_cents": 1290,
    },
]

# (glifo, ícone estático, título, subtítulo) — mais de um banner de
# propósito: com só um, o carrossel do cardápio público não tem o que
# "carrossel-ar" (não dá pra notar o recurso de arrastar/trocar de slide
# com uma imagem só).
_STARTER_BANNERS = [
    ("combo", "banner-burger.png", "Bem-vindo(a) ao seu cardápio digital!", "Personalize este banner e suas fotos quando quiser."),
    ("prato", "banner-pizza.png", "Arraste para o lado", "Cadastre quantos banners quiser nesse carrossel."),
    ("cup", "banner-loja.png", "Compartilhe seu link e QR Code", "Seus clientes pedem direto pelo celular, sem baixar nada."),
]

# Grupo de variação (obrigatório, escolha única) + grupo de adicionais
# (opcional, múltipla escolha) — usados só no produto marcado com
# "with_complements": True acima, pra mostrar as duas modalidades do
# recurso sem inflar o starter kit inteiro com isso.
_EXAMPLE_VARIATION_OPTIONS = [("Pequena", 0), ("Média", 500), ("Grande", 900)]
_EXAMPLE_ADDON_OPTIONS = [("Molho especial", 300), ("Porção extra", 600), ("Acompanhamento", 400)]


def _icon_path(filename: str) -> str:
    import os

    from flask import current_app

    return os.path.join(current_app.static_folder, _ICONS_DIR, filename)


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
        for order, entry in enumerate(_STARTER_CATEGORIES):
            category = Category(
                tenant_id=tenant.id,
                name=entry["category"],
                slug=_unique_slug(entry["category"], Category, tenant.id),
                icon=entry["icon"],
                display_order=order,
                is_active=True,
            )
            db.session.add(category)
            db.session.flush()

            product_name = entry["product"]
            with_complements = entry.get("with_complements", False)
            product = Product(
                tenant_id=tenant.id,
                category_id=category.id,
                name=product_name,
                slug=_unique_slug(product_name, Product, tenant.id),
                description=_EXAMPLE_DESCRIPTION_WITH_COMPLEMENTS if with_complements else _EXAMPLE_DESCRIPTION,
                price_cents=entry["price_cents"],
                tag=_EXAMPLE_TAG,
                is_active=True,
                display_order=0,
            )
            db.session.add(product)
            db.session.flush()

            icon_path = _icon_path(entry["static_icon"])
            card = generate_product_card_from_icon(icon_path, product_name, glyph=entry["glyph"])
            relative_path, color = save_generated_image(card, f"tenant_{tenant.id}/products/{product.id}")
            db.session.add(ProductImage(
                tenant_id=tenant.id,
                product_id=product.id,
                file_path=relative_path,
                dominant_color=color,
                is_primary=True,
                display_order=0,
            ))

            if with_complements:
                size_group = ComplementGroup(
                    tenant_id=tenant.id, product_id=product.id, name="Tamanho",
                    is_variation=True, is_required=True, single_choice=True, display_order=0,
                )
                db.session.add(size_group)
                db.session.flush()
                for option_order, (option_name, extra_cents) in enumerate(_EXAMPLE_VARIATION_OPTIONS):
                    db.session.add(ComplementOption(
                        tenant_id=tenant.id, group_id=size_group.id, name=option_name,
                        extra_price_cents=extra_cents, is_active=True, display_order=option_order,
                    ))

                addons_group = ComplementGroup(
                    tenant_id=tenant.id, product_id=product.id, name="Adicionais",
                    is_variation=False, is_required=False, single_choice=False,
                    max_choices=3, display_order=1,
                )
                db.session.add(addons_group)
                db.session.flush()
                for option_order, (option_name, extra_cents) in enumerate(_EXAMPLE_ADDON_OPTIONS):
                    db.session.add(ComplementOption(
                        tenant_id=tenant.id, group_id=addons_group.id, name=option_name,
                        extra_price_cents=extra_cents, is_active=True, display_order=option_order,
                    ))

        for order, (glyph, icon_file, title, subtitle) in enumerate(_STARTER_BANNERS):
            banner_icon_path = _icon_path(icon_file)
            banner_image = generate_banner_from_icon(banner_icon_path, glyph, title, subtitle)
            banner_path, banner_color = save_generated_image(banner_image, f"tenant_{tenant.id}/banners")
            db.session.add(Banner(
                tenant_id=tenant.id,
                title=title,
                subtitle=subtitle,
                image_path=banner_path,
                dominant_color=banner_color,
                display_order=order,
                is_active=True,
            ))

        if not tenant.logo_path:
            logo = generate_logo(tenant.name[:1].upper(), glyph="combo")
            logo_path, _color = save_generated_image(logo, f"tenant_{tenant.id}/logo")
            tenant.logo_path = logo_path

        db.session.commit()
    except Exception:  # noqa: BLE001 - conteúdo de exemplo nunca deve impedir a criação da loja
        db.session.rollback()
