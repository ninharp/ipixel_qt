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
    spacing: int = 1,
    size: int | None = None,
) -> list[tuple[int, int, bytes, tuple[int, int, int]]]:
    """Render each character to a 1-bit glyph of ``height`` rows, using font ``size``.

    Glyphs are cropped to their lit columns plus ``spacing`` empty columns, because the panel
    pages static text by glyph width and the font's side bearings would waste panel space.
    """
    face = load_font(size or default_font_size(height), font)
    ascent, descent = face.getmetrics() if hasattr(face, "getmetrics") else (height, 0)
    y = (height - (ascent + descent)) // 2
    space = max(2, round(face.getlength(" ") * 0.75))
    out = []
    for ch in text.replace("\n", " "):
        pad = height
        canvas_w = round(face.getlength(ch)) + 2 * pad
        img = Image.new("L", (canvas_w, height), 0)
        ImageDraw.Draw(img).text((pad, y), ch, fill=255, font=face)
        px = img.load()
        lit = [x for x in range(canvas_w) if any(px[x, r] >= threshold for r in range(height))]
        if lit:
            left, width = lit[0], min(255, lit[-1] - lit[0] + 1 + spacing)
            rows = [[left + x < canvas_w and px[left + x, r] >= threshold for x in range(width)]
                    for r in range(height)]
        else:
            width = space
            rows = [[False] * width for _ in range(height)]
        out.append((width, height, pack_bits(rows), color))
    return out


def default_font_size(height: int) -> int:
    # A font sized to the full row height is too wide for LED panels (2-px strokes, ~9 px per letter).
    return max(6, round(height * 0.75))


def text_width(glyph_list: list[tuple[int, int, bytes, tuple[int, int, int]]]) -> int:
    return sum(g[0] for g in glyph_list)


def pages(glyph_list: list[tuple[int, int, bytes, tuple[int, int, int]]], panel_width: int) -> int:
    """How many screens a static effect needs; the panel breaks pages between glyphs."""
    count, used = 1, 0
    for width, *_ in glyph_list:
        if used and used + width > panel_width:
            count, used = count + 1, 0
        used += width
    return count
