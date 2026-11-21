"""Pillow helpers that turn images and text into protocol payloads."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from .protocol import pack_bits

ImageSource = str | Path | bytes | Image.Image


def open_image(src: ImageSource) -> Image.Image:
    if isinstance(src, Image.Image):
        return src
    if isinstance(src, bytes):
        return Image.open(io.BytesIO(src))
    return Image.open(src)


def fit(img: Image.Image, width: int, height: int, mode: str = "contain") -> Image.Image:
    """Scale to the panel; ``contain`` letterboxes, ``cover`` crops, ``stretch`` distorts."""
    img = img.convert("RGB")
    if mode == "stretch":
        return img.resize((width, height), Image.Resampling.LANCZOS)
    scale = (max if mode == "cover" else min)(width / img.width, height / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    scaled = img.resize((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height))
    canvas.paste(scaled, ((width - w) // 2, (height - h) // 2))
    return canvas


def to_png(src: ImageSource, width: int, height: int, mode: str = "contain") -> bytes:
    buf = io.BytesIO()
    fit(open_image(src), width, height, mode).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_rgb(src: ImageSource, width: int, height: int, mode: str = "contain") -> bytes:
    """Row-major R,G,B bytes as the app's live/camera mode sends them."""
    return fit(open_image(src), width, height, mode).tobytes()


def to_gif(src: ImageSource, width: int, height: int, mode: str = "contain") -> bytes:
    img = open_image(src)
    frames, durations = [], []
    for f in ImageSequence.Iterator(img):
        frames.append(fit(f, width, height, mode))
        durations.append(f.info.get("duration", img.info.get("duration", 100)))
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
    )
    return buf.getvalue()


def load_font(height: int, font: str | Path | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font is not None:
        return ImageFont.truetype(str(font), height)
    return ImageFont.load_default(size=height)


def glyphs(
    text: str,
    height: int,
    color: tuple[int, int, int],
    font: str | Path | None = None,
    threshold: int = 96,
) -> list[tuple[int, int, bytes, tuple[int, int, int]]]:
    """Render each character to a 1-bit glyph of ``height`` rows."""
    face = load_font(height, font)
    ascent, descent = face.getmetrics() if hasattr(face, "getmetrics") else (height, 0)
    y = (height - (ascent + descent)) // 2
    out = []
    for ch in text.replace("\n", " "):
        width = max(1, min(255, round(face.getlength(ch))))
        img = Image.new("L", (width, height), 0)
        ImageDraw.Draw(img).text((0, y), ch, fill=255, font=face)
        px = img.load()
        rows = [[px[x, r] >= threshold for x in range(width)] for r in range(height)]
        out.append((width, height, pack_bits(rows), color))
    return out
