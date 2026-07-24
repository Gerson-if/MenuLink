"""
Upload de imagens e vídeo (produto, banner, logo da loja, landing page).

Regras:
- Extensão permitida validada tanto pelo nome do arquivo quanto abrindo o
  arquivo de fato com Pillow (evita que um arquivo malicioso renomeado
  para .jpg passe pela validação só de extensão).
- Redimensiona para no máximo 1600px no maior lado (evita uploads
  gigantes consumindo disco/banda) e recomprime como JPEG otimizado.
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
MAX_VIDEO_BYTES = 25 * 1024 * 1024  # 25MB
MAX_VIDEO_HEIGHT = 1080  # recompressão via ffmpeg nunca faz upscale, só limita o teto
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm"}


class InvalidImageError(Exception):
    pass


class InvalidVideoError(Exception):
    pass


def _allowed_extension(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]


def save_product_image(tenant_id: int, product_id: int, file_storage) -> str:
    """
    Recebe um FileStorage (do request.files), valida e salva.
    Retorna o caminho relativo (para gravar em ProductImage.file_path),
    ex: "uploads/tenant_3/products/17/8f1c...a2.jpg".
    """
    return _save_validated_image(os.path.join(f"tenant_{tenant_id}", "products", str(product_id)), file_storage)


def save_banner_image(tenant_id: int, file_storage) -> str:
    return _save_validated_image(os.path.join(f"tenant_{tenant_id}", "banners"), file_storage)


def save_tenant_logo(tenant_id: int, file_storage) -> str:
    return _save_validated_image(os.path.join(f"tenant_{tenant_id}", "logo"), file_storage)


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


def save_platform_image(subdir: str, file_storage) -> str:
    """Imagens da própria plataforma, sem tenant (ex: landing page,
    editada pelo Super Admin) — ver PlatformSettingsService.update_landing_content."""
    return _save_validated_image(os.path.join("platform", subdir), file_storage)


def save_platform_video(subdir: str, file_storage) -> str:
    """Vídeo de fundo do hero da landing page. Recomprime de verdade via
    `ffmpeg` (binário do sistema, chamado por subprocess — ver
    _compress_video), removendo o áudio (o <video autoplay muted loop>
    nunca toca som) e limitando a altura a MAX_VIDEO_HEIGHT sem upscale.
    `ffmpeg` é uma dependência de SISTEMA (não é `pip install`) — se o
    binário não estiver disponível no PATH do servidor, ou a compressão
    falhar/travar por qualquer motivo, cai de volta pro arquivo original
    sem recomprimir. Nunca bloqueamos um upload por causa disso."""
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise InvalidVideoError("Formato de vídeo não suportado. Use MP4 ou WEBM.")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_VIDEO_BYTES:
        raise InvalidVideoError(f"Vídeo muito grande (máximo {MAX_VIDEO_BYTES // (1024 * 1024)}MB). Envie um arquivo já otimizado para web.")

    relative_dir = os.path.join("uploads", "platform", subdir)
    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "platform", subdir)
    os.makedirs(absolute_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_input = os.path.join(tmp_dir, f"input.{ext}")
        file_storage.save(tmp_input)

        compressed_path = _compress_video(tmp_input, tmp_dir)
        if compressed_path:
            new_filename = f"{uuid.uuid4().hex}.mp4"
            shutil.move(compressed_path, os.path.join(absolute_dir, new_filename))
        else:
            new_filename = f"{uuid.uuid4().hex}.{ext}"
            shutil.move(tmp_input, os.path.join(absolute_dir, new_filename))

    return os.path.join(relative_dir, new_filename).replace(os.sep, "/")


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
        "-c:v", "libx264", "-crf", "28", "-preset", "fast",
        "-an", "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        return None

    return output_path


def _save_validated_image(relative_subdir: str, file_storage) -> str:
    """`relative_subdir`: caminho relativo dentro de UPLOAD_FOLDER onde a
    imagem deve ser salva, ex: "tenant_3/products/17" ou "platform/landing"."""
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
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise InvalidImageError("Arquivo enviado não é uma imagem válida.")

    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    relative_dir = os.path.join("uploads", relative_subdir)
    absolute_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_subdir)
    os.makedirs(absolute_dir, exist_ok=True)

    new_filename = f"{uuid.uuid4().hex}.jpg"
    absolute_path = os.path.join(absolute_dir, new_filename)
    image.save(absolute_path, format="JPEG", quality=85, optimize=True)

    return os.path.join(relative_dir, new_filename).replace(os.sep, "/")


def delete_product_image_file(relative_path: str) -> None:
    absolute_path = os.path.join(current_app.static_folder, relative_path)
    if os.path.isfile(absolute_path):
        os.remove(absolute_path)
