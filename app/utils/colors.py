"""
Pequenos helpers para permitir que lojistas (e o Super Admin) escolham
UMA cor de destaque (accent) e o sistema derive automaticamente as
variantes que o design system já espera (--accent-dark, --accent-soft)
— sem precisar pedir 3 cores separadas numa tela de configuração.
"""

import re

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalize_hex(value: str | None) -> str | None:
    """Valida e normaliza uma cor hex (#RRGGBB). Retorna None se inválida."""
    if not value:
        return None
    match = _HEX_RE.match(value.strip())
    if not match:
        return None
    return "#" + match.group(1).upper()


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def darken_hex(hex_color: str, amount: float = 0.16) -> str:
    """Escurece uma cor hex em `amount` (0-1) — usado para gerar a
    variante "-dark" (hover/estado ativo) a partir da cor escolhida."""
    r, g, b = hex_to_rgb(hex_color)
    r = max(0, round(r * (1 - amount)))
    g = max(0, round(g * (1 - amount)))
    b = max(0, round(b * (1 - amount)))
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_rgba_css(hex_color: str, alpha: float) -> str:
    """Usado para gerar a variante "-soft" (fundo suave) a partir da cor escolhida."""
    r, g, b = hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def readable_text_color(hex_color: str) -> str:
    """Preto ou branco — o que tiver mais contraste sobre `hex_color`
    (luminância relativa, fórmula do WCAG). Usado no cartaz de impressão
    do QR Code (ver settings_menu_qrcode_card): antes o texto tinha uma
    cor fixa, então uma cor de fundo escura escolhida pelo lojista virava
    texto ilegível — aqui o texto sempre se adapta, qualquer que seja o
    fundo escolhido."""
    r, g, b = hex_to_rgb(hex_color)

    def _channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)
    return "#000000" if luminance > 0.5 else "#FFFFFF"
