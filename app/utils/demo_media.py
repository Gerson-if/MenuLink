"""
Geração de imagens de demonstração (sem depender de fotos reais nem de
nenhuma fonte/emoji instalada no sistema operacional do servidor).

Usado só por `flask seed-db` (ver app/cli.py) para deixar a loja demo
"Braseiro & Cia" com uma aparência completa no cardápio público — fotos
de produto, banners promocionais e logo — para fins de apresentação.

Só depende do Pillow (já é dependência obrigatória do projeto, ver
requirements.txt) e da própria fonte "Inter" já vendorizada no projeto
(`app/static/vendor/fonts/inter/...woff2`, a mesma usada no CSS — ver
fonts.css) — o FreeType embutido no Pillow abre `.woff2` diretamente
desde a v2.11, então isso não exige nenhuma fonte instalada no sistema
operacional do servidor (o `ImageFont.load_default()` do Pillow não
tem os acentos do português — "í", por exemplo — nessa fonte bundled).
Os "ícones" são desenhados como formas vetoriais simples (ImageDraw),
não emojis — não há garantia nenhuma de que uma fonte de emoji (ex:
fonts-noto-color-emoji) esteja instalada num servidor novo.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

_FONT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "static", "vendor", "fonts", "inter", "inter-latin-700-normal.woff2"
)
_font_cache: dict[int, ImageFont.FreeTypeFont] = {}

# Paleta por "glifo" (tipo de item) — usada tanto no fundo em degradê
# quanto para dar variedade visual entre categorias, já que os ícones de
# categoria do sistema (CATEGORY_ICON_KEYS) são um conjunto fechado e
# genérico demais para representar bem um cardápio de churrascaria.
_PALETTE: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    # glifo: (cor central, cor da borda) do degradê radial
    "espeto": ((255, 150, 70), (110, 30, 20)),
    "prato": ((205, 95, 55), (55, 24, 15)),
    "bowl": ((165, 140, 65), (48, 42, 20)),
    "cup": ((70, 190, 178), (12, 48, 54)),
    "cupcake": ((235, 125, 155), (68, 26, 46)),
    "combo": ((250, 192, 84), (92, 56, 16)),
}
_GLYPH_COLOR = (255, 244, 232)  # creme claro, contraste sobre o degradê escuro


def _radial_gradient(size: tuple[int, int], center_color, edge_color) -> Image.Image:
    """Degradê radial (centro -> borda), sem numpy: usa a máscara
    embutida do Pillow (`Image.radial_gradient`) para compor duas
    camadas sólidas."""
    mask = Image.radial_gradient("L").resize(size)
    mask = ImageOps.invert(mask)  # centro fica 255 (opaco), borda 0
    bg = Image.new("RGB", size, edge_color)
    center_layer = Image.new("RGB", size, center_color)
    bg.paste(center_layer, mask=mask)
    return bg


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Fonte "Inter" (bold) já vendorizada no projeto, com fallback para
    a fonte bundled do Pillow caso o arquivo não seja encontrado por
    algum motivo (ex: instalação incompleta) — nesse caso os acentos do
    português não aparecem, mas a geração não quebra."""
    if size not in _font_cache:
        try:
            _font_cache[size] = ImageFont.truetype(_FONT_PATH, size)
        except OSError:
            _font_cache[size] = ImageFont.load_default(size=size)
    return _font_cache[size]


def _draw_espeto(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Espeto na diagonal com "cubos" enfileirados + labaredas na base."""
    color = _GLYPH_COLOR
    x1, y1 = cx - s * 0.32, cy + s * 0.30
    x2, y2 = cx + s * 0.32, cy - s * 0.30
    draw.line([(x1, y1), (x2, y2)], fill=color, width=max(2, int(s * 0.02)))
    for t in (0.18, 0.40, 0.62, 0.84):
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        r = s * 0.09
        draw.rounded_rectangle([px - r, py - r, px + r, py + r], radius=r * 0.35, fill=color)
    # labaredas (ondas triangulares) na base, sugerindo a brasa
    flame_y = cy + s * 0.34
    for i, fx in enumerate([-0.22, -0.05, 0.12, 0.29]):
        base_x = cx + s * fx
        draw.polygon(
            [
                (base_x - s * 0.05, flame_y + s * 0.08),
                (base_x, flame_y - s * 0.10 - (s * 0.02 if i % 2 else 0)),
                (base_x + s * 0.05, flame_y + s * 0.08),
            ],
            fill=color,
        )


def _draw_prato(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Posta de carne grelhada (retângulo arredondado com marcas de
    grelha) sobre um prato oval."""
    color = _GLYPH_COLOR
    draw.ellipse(
        [cx - s * 0.40, cy + s * 0.05, cx + s * 0.40, cy + s * 0.30],
        outline=color, width=max(2, int(s * 0.015)),
    )
    steak_box = [cx - s * 0.30, cy - s * 0.24, cx + s * 0.30, cy + s * 0.12]
    draw.rounded_rectangle(steak_box, radius=s * 0.10, fill=color)
    bg_center, _edge = _PALETTE["prato"]
    for i in range(-2, 3):
        offset = i * s * 0.11
        draw.line(
            [(cx - s * 0.24 + offset, cy - s * 0.20), (cx - s * 0.05 + offset, cy + s * 0.08)],
            fill=bg_center, width=max(2, int(s * 0.015)),
        )


def _draw_bowl(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Tigela com colher apoiada — representa acompanhamentos."""
    color = _GLYPH_COLOR
    draw.pieslice([cx - s * 0.36, cy - s * 0.30, cx + s * 0.36, cy + s * 0.42], start=0, end=180, fill=color)
    draw.ellipse([cx - s * 0.36, cy - s * 0.34, cx + s * 0.36, cy - s * 0.22], outline=color, width=max(2, int(s * 0.02)))
    # colher: cabo + concha
    handle_x = cx + s * 0.30
    draw.line([(handle_x, cy - s * 0.05), (handle_x + s * 0.14, cy - s * 0.38)], fill=color, width=max(2, int(s * 0.025)))
    draw.ellipse(
        [handle_x + s * 0.08, cy - s * 0.46, handle_x + s * 0.22, cy - s * 0.34],
        fill=color,
    )


def _draw_cup(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Copo (levemente afunilado) com canudo — bebidas."""
    color = _GLYPH_COLOR
    cup = [
        (cx - s * 0.22, cy - s * 0.30),
        (cx + s * 0.22, cy - s * 0.30),
        (cx + s * 0.17, cy + s * 0.36),
        (cx - s * 0.17, cy + s * 0.36),
    ]
    draw.polygon(cup, outline=color, width=max(2, int(s * 0.02)))
    draw.line([(cx - s * 0.22, cy - s * 0.10), (cx + s * 0.22, cy - s * 0.10)], fill=color, width=max(2, int(s * 0.015)))
    draw.line([(cx + s * 0.08, cy - s * 0.44), (cx + s * 0.20, cy - s * 0.06)], fill=color, width=max(2, int(s * 0.03)))


def _draw_cupcake(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Cupcake com cobertura e cereja — sobremesas."""
    color = _GLYPH_COLOR
    wrapper = [
        (cx - s * 0.26, cy + s * 0.36),
        (cx + s * 0.26, cy + s * 0.36),
        (cx + s * 0.16, cy + s * 0.04),
        (cx - s * 0.16, cy + s * 0.04),
    ]
    draw.polygon(wrapper, fill=color)
    draw.ellipse([cx - s * 0.22, cy - s * 0.30, cx + s * 0.22, cy + s * 0.06], fill=color)
    draw.ellipse([cx - s * 0.055, cy - s * 0.42, cx + s * 0.055, cy - s * 0.31], fill=color)


def _draw_combo(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Prato com 3 itens variados — combos."""
    color = _GLYPH_COLOR
    draw.ellipse([cx - s * 0.42, cy - s * 0.20, cx + s * 0.42, cy + s * 0.42], outline=color, width=max(2, int(s * 0.02)))
    draw.ellipse([cx - s * 0.24, cy - s * 0.04, cx - s * 0.04, cy + s * 0.16], fill=color)
    draw.rounded_rectangle([cx - s * 0.02, cy - s * 0.12, cx + s * 0.20, cy + s * 0.08], radius=s * 0.05, fill=color)
    draw.polygon(
        [(cx + s * 0.02, cy + s * 0.30), (cx + s * 0.24, cy + s * 0.30), (cx + s * 0.13, cy + s * 0.10)],
        fill=color,
    )


_GLYPH_DRAWERS = {
    "espeto": _draw_espeto,
    "prato": _draw_prato,
    "bowl": _draw_bowl,
    "cup": _draw_cup,
    "cupcake": _draw_cupcake,
    "combo": _draw_combo,
}


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int = 14):
    """Reduz o tamanho da fonte até o texto caber em `max_width` (nomes
    de produto/título mais compridos não vazam pra fora da imagem)."""
    size = start_size
    while size > min_size:
        font = _font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font, bbox
        size -= 2
    font = _font(min_size)
    return font, draw.textbbox((0, 0), text, font=font)


def _caption(image: Image.Image, text: str, font_size: int) -> None:
    """Barra translúcida escura na base da imagem com o texto (nome do
    produto, título do banner...) em branco por cima."""
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bar_height = int(height * 0.22)
    draw.rectangle([0, height - bar_height, width, height], fill=(0, 0, 0, 150))
    font, bbox = _fit_font(draw, text, max_width=int(width * 0.92), start_size=font_size)
    text_w = bbox[2] - bbox[0]
    text_x = max(int(width * 0.04), (width - text_w) // 2)
    text_y = height - bar_height + (bar_height - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def generate_product_card(glyph: str, product_name: str, size: int = 900) -> Image.Image:
    """Imagem quadrada para um ProductImage: degradê + ícone vetorial do
    tipo de prato + nome do produto na base."""
    center_color, edge_color = _PALETTE.get(glyph, _PALETTE["combo"])
    image = _radial_gradient((size, size), center_color, edge_color)
    draw = ImageDraw.Draw(image)
    _GLYPH_DRAWERS.get(glyph, _draw_combo)(draw, size // 2, int(size * 0.44), int(size * 0.62))
    _caption(image, product_name, font_size=int(size * 0.052))
    return image


def generate_banner(glyph: str, title: str, subtitle: str, size: tuple[int, int] = (1600, 500)) -> Image.Image:
    """Imagem larga para um Banner promocional: degradê + ícone
    vetorial à esquerda + título/subtítulo à direita."""
    center_color, edge_color = _PALETTE.get(glyph, _PALETTE["combo"])
    width, height = size
    image = _radial_gradient(size, center_color, edge_color)
    draw = ImageDraw.Draw(image)
    _GLYPH_DRAWERS.get(glyph, _draw_combo)(draw, int(width * 0.20), height // 2, int(height * 0.85))

    text_x = int(width * 0.40)
    max_text_width = width - text_x - int(width * 0.04)
    title_font, _bbox = _fit_font(draw, title, max_width=max_text_width, start_size=int(height * 0.14))
    subtitle_font, _bbox = _fit_font(draw, subtitle, max_width=max_text_width, start_size=int(height * 0.075))
    draw.text((text_x, int(height * 0.30)), title, font=title_font, fill=_GLYPH_COLOR, stroke_width=2, stroke_fill=edge_color)
    draw.text((text_x, int(height * 0.58)), subtitle, font=subtitle_font, fill=_GLYPH_COLOR)
    return image


def generate_logo(initial: str, size: int = 512) -> Image.Image:
    """Logo circular simples: iniciais do nome da loja + labareda
    estilizada, no acento de marca padrão do sistema (chili/laranja)."""
    center_color = (255, 150, 70)
    edge_color = (120, 32, 20)
    image = _radial_gradient((size, size), center_color, edge_color)
    draw = ImageDraw.Draw(image)
    _draw_espeto(draw, size // 2, int(size * 0.66), int(size * 0.30))
    font = _font(int(size * 0.34))
    bbox = draw.textbbox((0, 0), initial, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(
        ((size - text_w) // 2 - bbox[0], int(size * 0.16) - bbox[1]),
        initial, font=font, fill=_GLYPH_COLOR,
    )
    return image
