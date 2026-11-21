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
    return encode_gif(frames, durations)


def encode_gif(frames: list[Image.Image], durations: list[int]) -> bytes:
    """Write an animated GIF the panel can decode: full frames, one shared palette.

    Pillow otherwise gives frames their own (local) colour tables; when one is larger than the
    global table the panel only shows the frames it can decode (seen with a 4-colour global and
    8-colour local tables: only the first and last frame appeared).
    """
    rgb = [f.convert("RGB") for f in frames]
    strip = Image.new("RGB", (rgb[0].width, rgb[0].height * len(rgb)))
    for i, f in enumerate(rgb):
        strip.paste(f, (0, i * f.height))
    colours = strip.getcolors(256)
    if colours is None:
        return _encode_gif_local_palettes(rgb, durations)
    # Few enough colours overall: one exact shared palette.
    table = [c for _, c in colours]
    palette = bytes(v for c in table for v in c)
    paletted = [_exact_paletted(f, table) for f in rgb]
    buf = io.BytesIO()
    paletted[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=False,
        palette=palette,  # one global colour table, no local ones
    )
    return buf.getvalue()


def _exact_paletted(frame: Image.Image, table: list[tuple[int, int, int]]) -> Image.Image:
    """Map an RGB frame onto ``table`` exactly (Pillow's own mapping approximates close colours)."""
    lut = {c: i for i, c in enumerate(table)}
    raw = frame.tobytes()
    indices = bytes(lut[(raw[i], raw[i + 1], raw[i + 2])] for i in range(0, len(raw), 3))
    out = Image.frombytes("P", frame.size, indices)
    out.putpalette([v for c in table for v in c])
    return out


def _encode_gif_local_palettes(rgb: list[Image.Image], durations: list[int]) -> bytes:
    """More than 256 colours overall: keep a palette per frame, but a full 256-entry global table
    so that no local table is larger than the global one (what the panel decodes correctly)."""
    paletted = []
    for f in rgb:
        cols = f.getcolors(256)
        if cols is None:  # a single frame with >256 colours (e.g. a photo): reduce it
            f = f.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
            cols = f.getcolors(256)
        q = _exact_paletted(f, [c for _, c in cols])
        pad = q.getpalette()
        q.putpalette(pad + [0] * (768 - len(pad)))
        paletted.append(q)
    buf = io.BytesIO()
    paletted[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=False,
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


Glyph = tuple[int, int, bytes, tuple[int, int, int]]

# Glyph records carry their width in one byte.
MAX_GLYPH_WIDTH = 255


def merge_glyphs(glyph_list: list[Glyph]) -> Glyph:
    """Join glyphs side by side into one wide glyph (colour of the first)."""
    height = glyph_list[0][1]
    width = sum(g[0] for g in glyph_list)
    rows = [[False] * width for _ in range(height)]
    x = 0
    for w, h, bits, _ in glyph_list:
        rb = (w + 7) // 8
        for y in range(h):
            for gx in range(w):
                rows[y][x + gx] = bool(bits[y * rb + gx // 8] >> (gx % 8) & 1)
        x += w
    return (width, height, pack_bits(rows), glyph_list[0][3])


def page_glyphs(text: str, glyph_list: list[Glyph], panel_width: int) -> list[Glyph]:
    """Pack text into one glyph per screen for the static text effects.

    The panel pages static text by glyph count (8 per screen on 64×16, sized for the vendor app's
    fixed half-height glyphs), not by pixel width. One wide glyph per screen fills each screen and
    keeps effects like blink or breathe. Lines break at spaces where possible.
    """
    chars = text.replace("\n", " ")
    if len(chars) != len(glyph_list):
        raise ValueError("one glyph per character expected")
    limit = min(panel_width, MAX_GLYPH_WIDTH)
    pages: list[list[tuple[str, Glyph]]] = []
    cur: list[tuple[str, Glyph]] = []

    def width(items) -> int:
        return sum(g[0] for _, g in items)

    def strip(items):
        while items and items[0][0] == " ":
            items = items[1:]
        while items and items[-1][0] == " ":
            items = items[:-1]
        return items

    for item in zip(chars, glyph_list):
        while cur and width(cur) + item[1][0] > limit:
            spaces = [i for i, (ch, _) in enumerate(cur) if ch == " "]
            cut = spaces[-1] if spaces and item[0] != " " else len(cur)
            pages.append(cur[:cut])
            cur = strip(cur[cut:])
        if cur or item[0] != " ":
            cur.append(item)
    pages.append(cur)
    pages = [strip(page) for page in pages]
    pages = [page for page in pages if page] or [cur or list(zip(chars, glyph_list))[:1]]
    return [merge_glyphs([g for _, g in page]) for page in pages]
