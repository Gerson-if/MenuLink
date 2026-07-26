"""
Geração de imagens de demonstração (sem depender de fotos reais nem de
nenhuma fonte/emoji instalada no sistema operacional do servidor).

Usado por `flask seed-db` (ver app/cli.py) para deixar a loja demo
"Braseiro & Cia" com uma aparência completa no cardápio público — fotos
de produto, banners promocionais e logo — para fins de apresentação. E
também por app/services/starter_content_service.py, que dá esse mesmo
tipo de conteúdo de exemplo (genérico, não temático) para TODA loja
nova criada na plataforma, não só a de demonstração.

Só depende do Pillow (já é dependência obrigatória do projeto, ver
requirements.txt) e da própria fonte "Inter" já vendorizada no projeto
(`app/static/vendor/fonts/inter/...woff2`, a mesma usada no CSS — ver
fonts.css) — o FreeType embutido no Pillow abre `.woff2` diretamente
desde a v2.11, então isso não exige nenhuma fonte instalada no sistema
operacional do servidor (o `ImageFont.load_default()` do Pillow não
tem os acentos do português — "í", por exemplo — nessa fonte bundled).

Os "ícones" são desenhados como ilustrações vetoriais (ImageDraw +
blur), não emojis nem fotos reais — não há como baixar fotos de
internet nem gerar imagem via IA neste ambiente. Pra não ficarem
"achatados"/genéricos demais, cada ilustração tem: um disco
semi-transparente atrás (efeito "bolacha"/badge), sombra suave
projetada, círculos decorativos desfocados no fundo (efeito "bokeh") e
alguns detalhes finos (fumaça, gelo, granulado...) na cor de contraste.
"""

from __future__ import annotations

import math
import os
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    "espeto": ((255, 158, 74), (100, 26, 18)),
    "prato": ((214, 100, 58), (48, 20, 13)),
    "bowl": ((178, 150, 66), (42, 38, 18)),
    "cup": ((68, 196, 182), (10, 42, 48)),
    "cupcake": ((238, 128, 158), (62, 22, 42)),
    "combo": ((252, 196, 84), (86, 52, 14)),
}
_GLYPH_COLOR = (255, 246, 235)  # creme claro, contraste sobre o degradê escuro


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


# --------------------------------------------------------------------
# Fundo: degradê radial + "bokeh" decorativo
# --------------------------------------------------------------------

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


def _soft_circle(image: Image.Image, cx: float, cy: float, r: float, color, alpha: int, blur: float) -> None:
    """Cola um círculo desfocado e semi-transparente sobre `image` — usado
    tanto pro efeito "bokeh" do fundo quanto pro disco atrás do ícone."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    if blur:
        overlay = overlay.filter(ImageFilter.GaussianBlur(blur))
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _decorated_background(size: tuple[int, int], glyph: str, badge_center: tuple[float, float], badge_r: float) -> Image.Image:
    """Fundo completo: degradê + 2-3 círculos "bokeh" desfocados (mais
    claros e mais escuros que o centro, pra dar profundidade) + o disco
    ("badge") atrás de onde o ícone principal vai ficar."""
    center_color, edge_color = _PALETTE.get(glyph, _PALETTE["combo"])
    width, height = size
    image = _radial_gradient(size, center_color, edge_color)

    lighter = tuple(min(255, c + 45) for c in center_color)
    darker = tuple(max(0, c - 30) for c in edge_color)
    _soft_circle(image, width * 0.86, height * 0.10, max(width, height) * 0.30, lighter, 60, blur=max(width, height) * 0.05)
    _soft_circle(image, width * 0.06, height * 0.92, max(width, height) * 0.24, darker, 90, blur=max(width, height) * 0.04)

    _soft_circle(image, badge_center[0], badge_center[1] + badge_r * 0.10, badge_r * 1.02, (0, 0, 0), 60, blur=badge_r * 0.18)
    _soft_circle(image, badge_center[0], badge_center[1], badge_r, (255, 255, 255), 40, blur=badge_r * 0.06)
    return image


# --------------------------------------------------------------------
# Ícones (ilustrações vetoriais por tipo de item)
# --------------------------------------------------------------------

# Silhueta de labareda normalizada (x em [-0.4, 0.4], y em [-1 (ponta) a
# 0 (base)]) — com uma "cintura" assimétrica perto da base, que é o que
# faz uma chama parecer uma chama de verdade e não uma gota/diamante.
_FLAME_SHAPE = [
    (0.05, -1.00), (0.30, -0.70), (0.40, -0.38), (0.28, -0.05),
    (0.38, 0.20), (0.14, 0.32), (0.00, 0.22), (-0.16, 0.34),
    (-0.32, 0.18), (-0.22, -0.10), (-0.34, -0.40), (-0.18, -0.72),
]


def _flame_points(cx: float, base_y: float, w: float, h: float) -> list[tuple[float, float]]:
    """Silhueta de chama (baseada em `_FLAME_SHAPE`), escalada e
    posicionada com a base em `base_y`."""
    return [(cx + x * w, base_y + y * h) for x, y in _FLAME_SHAPE]


def _draw_espeto(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Espeto na diagonal com "cubos" de carne enfileirados + labaredas."""
    color = _GLYPH_COLOR
    shade = _PALETTE["espeto"][1]
    x1, y1 = cx - s * 0.34, cy + s * 0.28
    x2, y2 = cx + s * 0.30, cy - s * 0.34
    draw.line([(x1 - s * 0.10, y1 + s * 0.10), (x2 + s * 0.06, y2 - s * 0.06)], fill=shade, width=max(3, int(s * 0.035)))
    for t in (0.12, 0.36, 0.60, 0.84):
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        r = s * 0.105
        draw.rounded_rectangle([px - r, py - r * 0.85, px + r, py + r * 0.85], radius=r * 0.4, fill=color)
        # linha fina de "gordura" pra dar textura, sem ficar um bloco liso
        draw.line([(px - r * 0.5, py), (px + r * 0.5, py)], fill=shade, width=max(1, int(s * 0.008)))
    # labaredas na base, sugerindo a brasa
    flame_y = cy + s * 0.44
    for fx, scale in [(-0.20, 0.85), (-0.02, 1.15), (0.17, 0.72)]:
        base_x = cx + s * fx
        draw.polygon(_flame_points(base_x, flame_y, s * 0.15 * scale, s * 0.24 * scale), fill=color)


def _wavy_line(draw: ImageDraw.ImageDraw, x: float, y: float, height: float, width: float, color, line_width: int) -> None:
    """Linha ondulada vertical (fumaça subindo) — uma polilinha
    aproximando uma senoide, bem mais reconhecível como "fumacinha" do
    que arcos combinados."""
    steps = 14
    points = []
    for i in range(steps + 1):
        t = i / steps
        points.append((x + math.sin(t * math.pi * 2.4) * width * (1 - t * 0.35), y - t * height))
    draw.line(points, fill=color, width=line_width, joint="curve")


def _draw_prato(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Posta de carne grelhada (com marcas de grelha) sobre um prato oval,
    com fiozinhos de fumaça subindo."""
    color = _GLYPH_COLOR
    shade = _PALETTE["prato"][1]
    draw.ellipse([cx - s * 0.42, cy + s * 0.06, cx + s * 0.42, cy + s * 0.32], outline=color, width=max(3, int(s * 0.022)))
    steak_box = [cx - s * 0.32, cy - s * 0.22, cx + s * 0.32, cy + s * 0.14]
    draw.rounded_rectangle(steak_box, radius=s * 0.11, fill=color)
    # marcas de grelha: tracinhos diagonais curtos, todos dentro da posta
    box_w = s * 0.64
    for frac in (-0.62, -0.32, -0.02, 0.28, 0.58):
        lx = cx + frac * box_w * 0.5
        draw.line(
            [(lx - s * 0.045, cy - s * 0.12), (lx + s * 0.045, cy + s * 0.06)],
            fill=shade, width=max(2, int(s * 0.018)),
        )
    # fumaça subindo (duas fumacinhas onduladas acima da carne)
    for fx, h_scale in ((-0.14, 1.0), (0.12, 0.8)):
        _wavy_line(draw, cx + s * fx, cy - s * 0.26, s * 0.34 * h_scale, s * 0.05, color, max(2, int(s * 0.016)))


def _draw_bowl(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Tigela com "pedaços" de acompanhamento e colher apoiada. A borda
    (rim) fica exatamente na "cintura" do corpo da tigela — sem gap
    nem sobreposição — pra não parecer duas formas soltas."""
    color = _GLYPH_COLOR
    shade = _PALETTE["bowl"][1]
    rim_y = cy - s * 0.02
    body_ry = s * 0.32
    draw.pieslice([cx - s * 0.36, rim_y - body_ry, cx + s * 0.36, rim_y + body_ry], start=0, end=180, fill=color)
    draw.ellipse([cx - s * 0.36, rim_y - s * 0.07, cx + s * 0.36, rim_y + s * 0.07], outline=color, width=max(3, int(s * 0.024)))
    for dx, dy in [(-0.15, 0.09), (0.03, 0.14), (0.17, 0.07), (-0.02, 0.22)]:
        r = s * 0.04
        draw.ellipse([cx + s * dx - r, rim_y + s * dy - r, cx + s * dx + r, rim_y + s * dy + r], fill=shade)
    handle_x = cx + s * 0.30
    draw.line([(handle_x, rim_y + s * 0.08), (handle_x + s * 0.14, rim_y - s * 0.30)], fill=color, width=max(3, int(s * 0.028)))
    draw.ellipse([handle_x + s * 0.07, rim_y - s * 0.40, handle_x + s * 0.23, rim_y - s * 0.26], fill=color)


def _draw_cup(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Copo afunilado com canudo, gás/gelo e condensação — bebidas."""
    color = _GLYPH_COLOR
    shade = _PALETTE["cup"][1]
    cup = [
        (cx - s * 0.24, cy - s * 0.32),
        (cx + s * 0.24, cy - s * 0.32),
        (cx + s * 0.18, cy + s * 0.38),
        (cx - s * 0.18, cy + s * 0.38),
    ]
    draw.polygon(cup, outline=color, width=max(3, int(s * 0.022)))
    draw.line([(cx - s * 0.23, cy - s * 0.08), (cx + s * 0.23, cy - s * 0.08)], fill=color, width=max(2, int(s * 0.016)))
    for dx, dy, r in [(-0.06, 0.10, 0.055), (0.09, 0.20, 0.045)]:
        draw.rounded_rectangle([cx + s * dx - s * r, cy + s * dy - s * r, cx + s * dx + s * r, cy + s * dy + s * r], radius=s * r * 0.3, outline=shade, width=max(2, int(s * 0.012)))
    for dy in (-0.20, -0.02, 0.16):
        draw.line([(cx - s * 0.30, cy + s * dy), (cx - s * 0.26, cy + s * dy)], fill=color, width=max(2, int(s * 0.012)))
    draw.line([(cx + s * 0.09, cy - s * 0.46), (cx + s * 0.21, cy - s * 0.06)], fill=color, width=max(3, int(s * 0.032)))


def _draw_cupcake(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Cupcake com cobertura, granulado e cereja — sobremesas."""
    color = _GLYPH_COLOR
    shade = _PALETTE["cupcake"][1]
    wrapper = [
        (cx - s * 0.26, cy + s * 0.38),
        (cx + s * 0.26, cy + s * 0.38),
        (cx + s * 0.16, cy + s * 0.06),
        (cx - s * 0.16, cy + s * 0.06),
    ]
    draw.polygon(wrapper, fill=color)
    for i in range(-2, 3):
        x = cx + i * s * 0.075
        draw.line([(x, cy + s * 0.10), (x + i * s * 0.01, cy + s * 0.36)], fill=shade, width=max(1, int(s * 0.008)))
    draw.ellipse([cx - s * 0.23, cy - s * 0.30, cx + s * 0.23, cy + s * 0.08], fill=color)
    for dx, dy in [(-0.10, -0.14), (0.08, -0.08), (0.0, -0.20), (-0.02, -0.02), (0.14, -0.18)]:
        r = s * 0.018
        draw.ellipse([cx + s * dx - r, cy + s * dy - r, cx + s * dx + r, cy + s * dy + r], fill=shade)
    draw.ellipse([cx - s * 0.06, cy - s * 0.44, cx + s * 0.06, cy - s * 0.32], fill=color)


def _draw_combo(draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
    """Prato com itens variados + talheres ao lado — combos."""
    color = _GLYPH_COLOR
    draw.ellipse([cx - s * 0.44, cy - s * 0.18, cx + s * 0.44, cy + s * 0.44], outline=color, width=max(3, int(s * 0.024)))
    draw.ellipse([cx - s * 0.28, cy - s * 0.02, cx - s * 0.06, cy + s * 0.20], fill=color)
    draw.rounded_rectangle([cx - s * 0.04, cy - s * 0.10, cx + s * 0.20, cy + s * 0.10], radius=s * 0.05, fill=color)
    draw.polygon(
        [(cx, cy + s * 0.32), (cx + s * 0.24, cy + s * 0.32), (cx + s * 0.12, cy + s * 0.10)],
        fill=color,
    )
    # garfo (à esquerda)
    fork_x = cx - s * 0.58
    draw.line([(fork_x, cy - s * 0.10), (fork_x, cy + s * 0.34)], fill=color, width=max(2, int(s * 0.02)))
    for i in range(3):
        tx = fork_x - s * 0.045 + i * s * 0.045
        draw.line([(tx, cy - s * 0.32), (tx, cy - s * 0.10)], fill=color, width=max(2, int(s * 0.016)))
    # faca (à direita)
    knife_x = cx + s * 0.58
    draw.line([(knife_x, cy - s * 0.10), (knife_x, cy + s * 0.34)], fill=color, width=max(2, int(s * 0.02)))
    draw.polygon(
        [(knife_x - s * 0.03, cy - s * 0.32), (knife_x + s * 0.03, cy - s * 0.32), (knife_x, cy - s * 0.08)],
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


# --------------------------------------------------------------------
# Texto
# --------------------------------------------------------------------

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
    """Barra translúcida escura (com leve degradê) na base da imagem, com
    o texto (nome do produto...) em branco por cima."""
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bar_height = int(height * 0.20)
    fade_height = int(bar_height * 0.5)
    for i in range(fade_height):
        alpha = int(160 * (i / fade_height))
        y = height - bar_height - fade_height + i
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    draw.rectangle([0, height - bar_height, width, height], fill=(0, 0, 0, 165))
    font, bbox = _fit_font(draw, text, max_width=int(width * 0.92), start_size=font_size)
    text_w = bbox[2] - bbox[0]
    text_x = max(int(width * 0.05), (width - text_w) // 2)
    text_y = height - bar_height + (bar_height - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


# --------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------

def generate_product_card(glyph: str, product_name: str, size: int = 900) -> Image.Image:
    """Imagem quadrada para um ProductImage: degradê decorado + disco
    ("badge") + ícone vetorial do tipo de prato + nome do produto na
    base."""
    badge_center = (size / 2, size * 0.38)
    badge_r = size * 0.32
    image = _decorated_background((size, size), glyph, badge_center, badge_r)
    draw = ImageDraw.Draw(image)
    _GLYPH_DRAWERS.get(glyph, _draw_combo)(draw, int(badge_center[0]), int(badge_center[1]), int(size * 0.52))
    _caption(image, product_name, font_size=int(size * 0.052))
    return image


def generate_banner(glyph: str, title: str, subtitle: str, size: tuple[int, int] = (1600, 500)) -> Image.Image:
    """Imagem larga para um Banner promocional: degradê decorado + disco
    + ícone vetorial à esquerda + título/subtítulo (com "chip" de
    destaque) à direita."""
    width, height = size
    badge_center = (width * 0.20, height * 0.52)
    badge_r = height * 0.46
    image = _decorated_background(size, glyph, badge_center, badge_r)
    draw = ImageDraw.Draw(image)
    _, edge_color = _PALETTE.get(glyph, _PALETTE["combo"])
    _GLYPH_DRAWERS.get(glyph, _draw_combo)(draw, int(badge_center[0]), int(badge_center[1]), int(height * 0.80))

    text_x = int(width * 0.40)
    max_text_width = width - text_x - int(width * 0.04)
    title_font, title_bbox = _fit_font(draw, title, max_width=max_text_width, start_size=int(height * 0.14))
    subtitle_font, subtitle_bbox = _fit_font(draw, subtitle, max_width=max_text_width, start_size=int(height * 0.075))

    title_y = int(height * 0.28)
    draw.text((text_x, title_y), title, font=title_font, fill=_GLYPH_COLOR, stroke_width=3, stroke_fill=edge_color)

    # "chip" atrás do subtítulo, pra dar aparência de banner promocional
    chip_y = int(height * 0.60)
    chip_pad_x, chip_pad_y = int(height * 0.035), int(height * 0.03)
    chip_box = [
        text_x - chip_pad_x, chip_y - chip_pad_y,
        text_x + (subtitle_bbox[2] - subtitle_bbox[0]) + chip_pad_x, chip_y + (subtitle_bbox[3] - subtitle_bbox[1]) + chip_pad_y * 2,
    ]
    draw.rounded_rectangle(chip_box, radius=chip_pad_y * 1.4, fill=_blend(edge_color, (255, 255, 255), 0.18))
    draw.text((text_x, chip_y - subtitle_bbox[1]), subtitle, font=subtitle_font, fill=_GLYPH_COLOR)
    return image


def _blend(color_a, color_b, t: float) -> tuple[int, int, int]:
    return tuple(int(a + (b - a) * t) for a, b in zip(color_a, color_b))


def generate_logo(initial: str, size: int = 512, glyph: str = "espeto") -> Image.Image:
    """Logo circular simples: iniciais do nome da loja sobre um disco,
    com um ícone estilizado por baixo. `glyph` escolhe tanto o ícone
    quanto a paleta — "espeto" (padrão) é o tema da loja demo
    (churrascaria); conteúdo genérico para lojas novas de verdade deve
    passar outro glyph (ex: "combo", que não sugere nenhum tipo
    específico de comida)."""
    badge_center = (size * 0.5, size * 0.42)
    badge_r = size * 0.40
    image = _decorated_background((size, size), glyph, badge_center, badge_r)
    draw = ImageDraw.Draw(image)
    _GLYPH_DRAWERS.get(glyph, _draw_combo)(draw, size // 2, int(size * 0.70), int(size * 0.26))
    font = _font(int(size * 0.32))
    bbox = draw.textbbox((0, 0), initial, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(
        ((size - text_w) // 2 - bbox[0], int(size * 0.14) - bbox[1]),
        initial, font=font, fill=_GLYPH_COLOR,
    )
    return image


def save_generated_image(image: Image.Image, relative_subdir: str) -> tuple[str, str]:
    """Salva uma imagem gerada (`generate_product_card`/`generate_banner`/
    `generate_logo`) em `UPLOAD_FOLDER/<relative_subdir>/<uuid>.webp` e
    retorna (caminho_relativo, cor_dominante) — mesmo formato que
    `app/utils/uploads.py` usa para fotos de verdade enviadas pelo
    lojista, então tudo (template, carrossel com cor de fundo...)
    funciona igual não importa se a foto é gerada ou enviada."""
    from flask import current_app

    from app.utils.uploads import dominant_edge_color

    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_subdir)
    os.makedirs(absolute_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.webp"
    image.save(os.path.join(absolute_dir, filename), format="WEBP", quality=82, method=6)
    relative_path = os.path.join("uploads", relative_subdir, filename).replace(os.sep, "/")
    return relative_path, dominant_edge_color(image)
