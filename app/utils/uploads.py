"""
Upload de imagens e vídeo (produto, banner, logo da loja, landing page).

Regras:
- Extensão permitida validada tanto pelo nome do arquivo quanto abrindo o
  arquivo de fato com Pillow (evita que um arquivo malicioso renomeado
  para .jpg passe pela validação só de extensão).
- Redimensiona para no máximo 1600px no maior lado (evita uploads
  gigantes consumindo disco/banda) e recomprime como WEBP (mais leve
  que JPEG na mesma qualidade visual).
- Também calcula a cor média das bordas da imagem (`dominant_edge_color`)
  pra servir de fundo sólido nos carrosséis públicos quando a proporção
  da caixa não bate com a da foto — evita esticar/cortar a imagem ou
  usar um fundo desfocado.
- Cada imagem recebe um nome aleatório (uuid4) — nunca confiamos no nome
  original do arquivo enviado pelo navegador.
- Imagens de um tenant ficam em `static/uploads/tenant_<tenant_id>/...` —
  isolamento também a nível de sistema de arquivos, não só de banco.
  Imagens da plataforma (sem tenant, ex: landing page) ficam em
  `static/uploads/platform/...`.
- Vídeo (hoje só o hero da landing page, `save_platform_video`) é
  recomprimido de verdade via o binário `ffmpeg` do sistema (não é uma
  dependência pip) — ver `_compress_video`. Isso é opcional em tempo de
  execução: sem `ffmpeg` no PATH do servidor, ou se a compressão falhar,
  o upload continua funcionando com o arquivo original sem recomprimir.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

MAX_DIMENSION = 1600
ICON_DIMENSION = 128
IMAGE_QUALITY = 82
# Imagens da landing page nunca são exibidas grandes (ver marketing/landing.html):
# o hero fica em uma coluna de até ~560px de largura / 420px de altura, e as
# fotos de "feature" ficam dentro de uma caixinha de 48x48px. Gerar/guardar
# elas no MAX_DIMENSION padrão (1600px) seria puro desperdício de banda —
# ninguém nunca vê mais que isso. Os valores abaixo já dão folga pra tela
# retina (~2x) do maior tamanho de exibição real.
LANDING_HERO_IMAGE_DIMENSION = 900
LANDING_FEATURE_IMAGE_DIMENSION = 160
# Logo da plataforma (marca-mark da landing, telas de auth e favicon) —
# nunca é exibida grande (ver marketing/landing.html e layouts/auth.html),
# então um teto pequeno já cobre até telas retina.
PLATFORM_LOGO_DIMENSION = 240
MAX_VIDEO_BYTES = 25 * 1024 * 1024  # 25MB
# O vídeo do hero toca mudo, em loop, atrás de um degradê escuro
# (.hero-video-overlay em marketing/landing.html vai até 85% de opacidade) —
# é decoração de fundo, não conteúdo em si, então não precisa de nitidez alta:
# 720p + CRF mais agressivo fica leve sem diferença perceptível por trás do
# overlay. `preset=slow` comprime melhor que o `fast` anterior pro mesmo CRF
# (custa mais tempo de encode, mas isso só acontece uma vez, no upload).
MAX_VIDEO_HEIGHT = 720  # recompressão via ffmpeg nunca faz upscale, só limita o teto
VIDEO_CRF = 30
VIDEO_PRESET = "slow"
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm"}


class InvalidImageError(Exception):
    pass


class InvalidVideoError(Exception):
    pass


def _allowed_extension(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]


def save_product_image(tenant_id: int, product_id: int, file_storage) -> tuple[str, str]:
    """
    Recebe um FileStorage (do request.files), valida e salva.
    Retorna (caminho_relativo, cor_dominante_hex) — ver
    `dominant_edge_color`. O caminho vai em ProductImage.file_path,
    ex: "uploads/tenant_3/products/17/8f1c...a2.webp".
    """
    return _save_validated_image(os.path.join(f"tenant_{tenant_id}", "products", str(product_id)), file_storage)


def save_banner_image(tenant_id: int, file_storage) -> tuple[str, str]:
    """
    Comprime e limita o tamanho máximo (ver `_save_validated_image`),
    SEM recortar — o carrossel público (`.banner-slide img` em
    store_menu.html) usa `object-fit: contain` justamente pra mostrar a
    imagem inteira dentro da caixa, não cortar pedaço nenhum dela.
    Retorna (caminho_relativo, cor_dominante_hex).
    """
    return _save_validated_image(os.path.join(f"tenant_{tenant_id}", "banners"), file_storage)


def save_tenant_logo(tenant_id: int, file_storage) -> str:
    path, _color = _save_validated_image(os.path.join(f"tenant_{tenant_id}", "logo"), file_storage)
    return path


def save_category_icon(tenant_id: int, category_id: int, file_storage) -> str:
    """
    Ícone próprio de uma categoria (cardápio público). Diferente de
    `_save_validated_image`: NÃO converte para JPEG (perderia
    transparência) — redimensiona para um quadrado pequeno fixo
    (ICON_DIMENSION) preservando o canal alpha e salva como PNG.
    """
    filename = secure_filename(file_storage.filename or "")
    if not filename or not _allowed_extension(filename):
        raise InvalidImageError(
            "Formato de imagem não suportado. Use PNG, JPG, JPEG ou WEBP."
        )

    try:
        image = Image.open(file_storage.stream)
        image.verify()
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError):
        raise InvalidImageError("Arquivo enviado não é uma imagem válida.")

    image = ImageOps.fit(image, (ICON_DIMENSION, ICON_DIMENSION), Image.LANCZOS)

    relative_subdir = os.path.join(f"tenant_{tenant_id}", "categories", str(category_id))
    relative_dir = os.path.join("uploads", relative_subdir)
    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_subdir)
    os.makedirs(absolute_dir, exist_ok=True)

    new_filename = f"{uuid.uuid4().hex}.png"
    image.save(os.path.join(absolute_dir, new_filename), format="PNG", optimize=True)

    return os.path.join(relative_dir, new_filename).replace(os.sep, "/")


def save_platform_image(subdir: str, file_storage, max_dimension: int = MAX_DIMENSION) -> str:
    """Imagens da própria plataforma, sem tenant (ex: landing page,
    editada pelo Super Admin) — ver PlatformSettingsService.update_landing_content.
    `max_dimension`: quem chama conhece o tamanho de exibição real (ver
    LANDING_HERO_IMAGE_DIMENSION / LANDING_FEATURE_IMAGE_DIMENSION) e pode
    apertar o teto padrão pra gerar um arquivo bem menor."""
    path, _color = _save_validated_image(os.path.join("platform", subdir), file_storage, max_dimension=max_dimension)
    return path


def save_platform_video(subdir: str, file_storage) -> tuple[str, bool]:
    """Vídeo de fundo do hero da landing page. Recomprime de verdade via
    `ffmpeg` (binário do sistema, chamado por subprocess — ver
    _compress_video), removendo o áudio (o <video autoplay muted loop>
    nunca toca som), limitando a altura a MAX_VIDEO_HEIGHT sem upscale, e
    (importante para não travar ao iniciar) escrevendo o índice do MP4 no
    começo do arquivo (`+faststart`) — sem isso, o navegador só consegue
    começar a tocar depois de baixar o arquivo inteiro, o que parece
    "travado" em conexões mais lentas.

    `ffmpeg` é uma dependência de SISTEMA (não é `pip install` — ver
    `deploy/install.sh`). Se o binário não estiver disponível no PATH do
    servidor, ou a compressão falhar/travar por qualquer motivo:
    - um `.webm` é RECUSADO (não convertido) — sem o ffmpeg não dá pra
      gerar o `.mp4` que Safari/iOS exige (WebM não tem suporte nativo
      nenhum no iOS, o vídeo simplesmente não toca);
    - um `.mp4` é aceito sem recomprimir (mantém o comportamento antigo),
      mas retorna `compressed=False` para quem chamou avisar o Super
      Admin que o arquivo pode estar pesado/sem faststart.

    Retorna (caminho_relativo, compressed)."""
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise InvalidVideoError("Formato de vídeo não suportado. Use MP4 ou WEBM.")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_VIDEO_BYTES:
        raise InvalidVideoError(f"Vídeo muito grande (máximo {MAX_VIDEO_BYTES // (1024 * 1024)}MB). Envie um arquivo já otimizado para web.")

    ffmpeg_available = shutil.which("ffmpeg") is not None
    if ext == "webm" and not ffmpeg_available:
        raise InvalidVideoError(
            "Vídeos .webm exigem o ffmpeg instalado no servidor para serem convertidos para "
            ".mp4 — sem isso, o vídeo não toca no Safari/iPhone/iPad (sem suporte nativo a "
            "WebM). Envie um arquivo .mp4, ou peça para instalar o ffmpeg no servidor."
        )

    relative_dir = os.path.join("uploads", "platform", subdir)
    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "platform", subdir)
    os.makedirs(absolute_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_input = os.path.join(tmp_dir, f"input.{ext}")
        file_storage.save(tmp_input)

        compressed_path = _compress_video(tmp_input, tmp_dir) if ffmpeg_available else None
        if compressed_path:
            new_filename = f"{uuid.uuid4().hex}.mp4"
            shutil.move(compressed_path, os.path.join(absolute_dir, new_filename))
        else:
            new_filename = f"{uuid.uuid4().hex}.{ext}"
            shutil.move(tmp_input, os.path.join(absolute_dir, new_filename))

    relative_path = os.path.join(relative_dir, new_filename).replace(os.sep, "/")
    return relative_path, compressed_path is not None


def _compress_video(input_path: str, tmp_dir: str) -> str | None:
    """Recomprime `input_path` via ffmpeg, sempre normalizando pra
    `.mp4` (libx264) independente do formato de entrada (mp4 ou webm).
    Retorna o caminho do arquivo de saída, ou None se o ffmpeg não
    estiver disponível ou a compressão falhar/travar — quem chamou usa
    o arquivo original nesse caso."""
    if not shutil.which("ffmpeg"):
        return None

    output_path = os.path.join(tmp_dir, "output.mp4")
    command = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale=-2:min({MAX_VIDEO_HEIGHT}\\,ih)",
        "-c:v", "libx264", "-crf", str(VIDEO_CRF), "-preset", VIDEO_PRESET,
        "-an", "-movflags", "+faststart",
        output_path,
    ]
    try:
        # timeout maior que antes: "preset slow" comprime melhor mas demora
        # mais que "fast" — é um custo único no upload, não em toda request.
        result = subprocess.run(command, capture_output=True, timeout=240)
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        return None

    return output_path


def dominant_edge_color(image: Image.Image) -> str:
    """Cor média das bordas (topo/base/laterais) da imagem, em hex.

    Usada como fundo sólido atrás da foto nas telas públicas (carrossel
    de banners, carrossel do modal de produto) quando a proporção da
    caixa de exibição não bate com a da foto — no lugar de um fundo
    desfocado (feio) ou de recortar a imagem (perde informação). Usa a
    média só da borda, não da imagem inteira, porque o miolo de uma
    foto de comida costuma ter cores muito mais vivas que o fundo, e
    isso "sujaria" a média geral.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    border = max(2, min(width, height) // 10)
    regions = [
        rgb.crop((0, 0, width, border)),  # topo
        rgb.crop((0, height - border, width, height)),  # base
        rgb.crop((0, 0, border, height)),  # esquerda
        rgb.crop((width - border, 0, width, height)),  # direita
    ]
    r_total = g_total = b_total = weight_total = 0
    for region in regions:
        r, g, b = region.resize((1, 1), Image.BOX).getpixel((0, 0))
        weight = region.size[0] * region.size[1]
        r_total += r * weight
        g_total += g * weight
        b_total += b * weight
        weight_total += weight
    return "#{:02x}{:02x}{:02x}".format(r_total // weight_total, g_total // weight_total, b_total // weight_total)


def _save_validated_image(relative_subdir: str, file_storage, max_dimension: int = MAX_DIMENSION) -> tuple[str, str]:
    """`relative_subdir`: caminho relativo dentro de UPLOAD_FOLDER onde a
    imagem deve ser salva, ex: "tenant_3/products/17" ou "platform/landing".
    Retorna (caminho_relativo, cor_dominante_hex) — ver `dominant_edge_color`.
    Salva como WEBP: mesma qualidade visual que JPEG só que bem mais leve
    (arquivo final tipicamente 25-35% menor), sem precisar de nenhuma
    dependência nova além do Pillow que o projeto já usa."""
    filename = secure_filename(file_storage.filename or "")
    if not filename or not _allowed_extension(filename):
        raise InvalidImageError(
            "Formato de imagem não suportado. Use PNG, JPG, JPEG ou WEBP."
        )

    try:
        image = Image.open(file_storage.stream)
        image.verify()  # valida que é realmente uma imagem válida
        # verify() invalida o objeto para uso posterior — reabrir
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        # WEBP de saída não tem canal alpha (ver Image.save abaixo), então
        # um PNG/WEBP de entrada com transparência (comum em logo — ver
        # save_tenant_logo/save_platform_image, ambos passam por aqui)
        # precisa de um fundo antes de achatar pra RGB. `.convert("RGB")`
        # direto numa imagem RGBA faz o Pillow compor sobre PRETO por
        # padrão — o fundo transparente da logo virava uma caixa preta
        # sólida em vez de ficar transparente, quebrando qualquer logo
        # colocada sobre um fundo escuro/colorido (navbar, favicon,
        # ícone do PWA — ver render_manifest_icon). Compor sobre BRANCO
        # explicitamente é o padrão seguro pra imagem "achatada" em geral.
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "#FFFFFF")
            flattened.paste(rgba, mask=rgba.split()[3])
            image = flattened
        else:
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise InvalidImageError("Arquivo enviado não é uma imagem válida.")

    image.thumbnail((max_dimension, max_dimension))
    dominant_color = dominant_edge_color(image)

    relative_dir = os.path.join("uploads", relative_subdir)
    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_subdir)
    os.makedirs(absolute_dir, exist_ok=True)

    new_filename = f"{uuid.uuid4().hex}.webp"
    absolute_path = os.path.join(absolute_dir, new_filename)
    image.save(absolute_path, format="WEBP", quality=IMAGE_QUALITY, method=6)

    relative_path = os.path.join(relative_dir, new_filename).replace(os.sep, "/")
    return relative_path, dominant_color


def delete_product_image_file(relative_path: str) -> None:
    absolute_path = os.path.join(current_app.static_folder, relative_path)
    if os.path.isfile(absolute_path):
        os.remove(absolute_path)


def _rounded_corner_mask(size: int, radius_ratio: float) -> "Image.Image":
    """Máscara alpha (branco = visível) com cantos arredondados — usada
    só no ícone `purpose=any` (ver `_build_icon_canvas` abaixo)."""
    from PIL import ImageDraw

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius_ratio), fill=255)
    return mask


def _build_icon_canvas(size: int, background_hex: str, rounded: bool) -> "tuple[Image.Image, int]":
    """Canvas quadrado de fundo (cor de destaque em vigor) + margem de
    segurança em pixels pro conteúdo (logo/ícone-padrão) ser colado
    depois.

    `rounded`: controla o formato do PRÓPRIO quadrado de fundo — NÃO o
    conteúdo:
    - `purpose=maskable` (rounded=False): fundo vai até a borda, sem
      arredondar nada aqui — é o Android/iOS/launcher quem aplica a
      própria máscara (círculo, "squircle", gota d'água — cada
      fabricante tem a sua) por cima. Arredondar o quadrado aqui
      resultaria num ícone "arredondado dentro do arredondado do
      sistema", cortando pedaço da logo de um jeito imprevisível.
    - `purpose=any` (rounded=True): cantos arredondados desenhados por
      nós mesmos, pro ícone ainda ficar com uma cara de app "de
      verdade" nos poucos lugares que não aplicam máscara adaptativa
      nenhuma (ex: atalho de área de trabalho em desktop)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fill = Image.new("RGBA", (size, size), background_hex)
    if rounded:
        mask = _rounded_corner_mask(size, radius_ratio=0.22)
        canvas.paste(fill, (0, 0), mask)
    else:
        canvas.paste(fill, (0, 0))
    margin = int(size * 0.18)  # ~80% de "zona segura" pro conteúdo real (exigência do Android p/ maskable)
    return canvas, margin


def _finalize_icon_png(canvas: "Image.Image", rounded: bool) -> bytes:
    """`rounded=True`: mantém RGBA (cantos transparentes de propósito).
    `rounded=False`: força RGB (100% opaco, sem nenhum canal alpha) —
    além de ser o que a spec de ícone "maskable" pede, é OBRIGATÓRIO
    pro apple-touch-icon do iOS (ver base.html): Safari não compõe
    transparência nesse ícone específico direito, mostrando preto atrás
    de qualquer pixel não-100%-opaco — inclusive pixels quase-opacos de
    anti-serrilhado nas bordas do desenho, não só transparência total."""
    from io import BytesIO

    buffer = BytesIO()
    output = canvas if rounded else canvas.convert("RGB")
    output.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_manifest_icon(relative_logo_path: str, size: int, background_hex: str, rounded: bool = False) -> bytes:
    """Gera, na hora, um ícone quadrado PNG (do tamanho pedido) a partir
    da logo customizada do Super Admin (`PlatformSettings.platform_logo`)
    — usado pelo manifest.json/PWA (ver app/__init__.py:manifest_json e
    app/static/sw.js) e pelo apple-touch-icon.

    Por que gerar em vez de usar o arquivo salvo direto: a logo salva por
    `save_platform_image` (PLATFORM_LOGO_DIMENSION) é WEBP, sem
    transparência e SEM garantia de ser quadrada — usar ela direto como
    ícone de instalação faria o Android/iOS espremer/cortar uma logo
    retangular numa moldura quadrada, ficando distorcida. Aqui a logo é
    colocada centralizada, mantendo a proporção original, sobre um fundo
    quadrado sólido (a cor de destaque em vigor) com uma margem de
    segurança — dentro da "zona segura" que ícones "maskable" do Android
    exigem (conteúdo real ocupando no máximo ~80% do quadrado), pra
    NENHUM launcher cortar pedaço da logo ao aplicar sua própria máscara
    (círculo, "squircle" etc — ver `rounded` em `_build_icon_canvas`)."""
    absolute_path = os.path.join(current_app.static_folder, relative_logo_path)
    with Image.open(absolute_path) as source:
        logo = source.convert("RGBA")

    canvas, margin = _build_icon_canvas(size, background_hex, rounded)
    max_content = size - 2 * margin
    fitted = logo.copy()
    fitted.thumbnail((max_content, max_content), Image.LANCZOS)
    offset = ((size - fitted.width) // 2, (size - fitted.height) // 2)
    canvas.paste(fitted, offset, fitted)
    return _finalize_icon_png(canvas, rounded)


_EGG_MASK_PATH = os.path.join("img", "egg-mask.png")


def render_default_manifest_icon(size: int, background_hex: str, rounded: bool = False) -> bytes:
    """Mesma ideia de `render_manifest_icon`, mas pro ícone-PADRÃO (sem
    logo customizada do Super Admin) — o ovo frito branco (silhueta
    pré-renderizada em img/egg-mask.png) colado sobre um fundo quadrado
    na cor de destaque REAL em vigor.

    Por que gerar na hora em vez de usar os PNGs estáticos antigos
    (img/icon-192.png etc.): aqueles tinham a cor de destaque GRAVADA no
    arquivo (sempre laranja, #E54A36) — se o Super Admin trocasse o tema
    do painel pra outra cor, a tela de splash do app instalado (que usa
    `background_color` do manifest, gerado dinamicamente com a cor
    atual — ver pwa_manifest em app/__init__.py) deixava de bater com a
    cor do ícone, e o ícone quadrado "flutuava" feio no meio de um fundo
    de cor diferente. Gerando os dois (ícone e fundo da splash) com a
    MESMA cor sempre, o quadrado do ícone fica invisível — parece um
    logotipo desenhado direto na tela cheia, sem caixa nem borda.

    `rounded=False` (usado no ícone `purpose=maskable`) é o que faz o
    ícone se ADAPTAR ao formato de cada aparelho/launcher (círculo,
    "squircle", gota — cada fabricante Android tem o seu) em vez de
    ficar sempre quadrado com cantos retos, que é o que dava a aparência
    "feia e sem profissionalismo" reportada."""
    mask_path = os.path.join(current_app.static_folder, _EGG_MASK_PATH)
    with Image.open(mask_path) as mask_source:
        mask = mask_source.convert("RGBA")

    canvas, margin = _build_icon_canvas(size, background_hex, rounded)
    content_size = size - 2 * margin
    fitted_mask = mask.resize((content_size, content_size), Image.LANCZOS)
    canvas.paste(fitted_mask, (margin, margin), fitted_mask)
    return _finalize_icon_png(canvas, rounded)
