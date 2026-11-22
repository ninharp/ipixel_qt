"""Animation document: frames of RGB pixels with undo, no Qt dependency."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageSequence, ImageStat

from .. import render

Color = tuple[int, int, int]
Point = tuple[int, int]
Box = tuple[int, int, int, int]

DEFAULT_DURATION = 100
UNDO_LIMIT = 200


@dataclass
class Frame:
    image: Image.Image
    duration: int = DEFAULT_DURATION

    def copy(self) -> Frame:
        return Frame(self.image.copy(), self.duration)


class Animation:
    def __init__(self, width: int = 64, height: int = 16):
        self.width = width
        self.height = height
        self.frames: list[Frame] = [Frame(self._blank())]
        self.index = 0
        self._undo: list[tuple[list[Frame], int]] = []
        self._redo: list[tuple[list[Frame], int]] = []
        self._listeners: list[Callable[[], None]] = []

    # Change tracking

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def changed(self) -> None:
        for fn in self._listeners:
            fn()

    def checkpoint(self) -> None:
        """Remember the current state so the next edits can be undone as one step."""
        self._undo.append(([f.copy() for f in self.frames], self.index))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def undo(self) -> bool:
        return self._swap(self._undo, self._redo)

    def redo(self) -> bool:
        return self._swap(self._redo, self._undo)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _swap(self, src: list, dst: list) -> bool:
        if not src:
            return False
        dst.append(([f.copy() for f in self.frames], self.index))
        self.frames, self.index = src.pop()
        self.width, self.height = self.frames[0].image.size
        self.changed()
        return True

    # Frames

    @property
    def frame(self) -> Frame:
        return self.frames[self.index]

    @property
    def image(self) -> Image.Image:
        return self.frame.image

    @property
    def is_animated(self) -> bool:
        return len(self.frames) > 1

    def select(self, index: int) -> None:
        self.index = max(0, min(len(self.frames) - 1, index))
        self.changed()

    def add_frame(self, duplicate: bool = True) -> None:
        self.checkpoint()
        new = self.frame.copy() if duplicate else Frame(self._blank(), self.frame.duration)
        self.frames.insert(self.index + 1, new)
        self.index += 1
        self.changed()

    def delete_frame(self) -> None:
        if len(self.frames) == 1:
            self.clear()
            return
        self.checkpoint()
        del self.frames[self.index]
        self.index = min(self.index, len(self.frames) - 1)
        self.changed()

    def move_frame(self, delta: int) -> None:
        target = self.index + delta
        if not 0 <= target < len(self.frames):
            return
        self.checkpoint()
        self.frames[self.index], self.frames[target] = self.frames[target], self.frames[self.index]
        self.index = target
        self.changed()

    def reverse(self) -> None:
        self.checkpoint()
        self.frames.reverse()
        self.index = len(self.frames) - 1 - self.index
        self.changed()

    def pingpong(self) -> None:
        """Play forward then backward; first and last frame are not repeated, so the turn is smooth."""
        if len(self.frames) < 3:
            return
        self.checkpoint()
        self.frames += [f.copy() for f in self.frames[-2:0:-1]]
        self.changed()

    def crossfade_loop(self, overlap: int) -> None:
        """Blend the last ``overlap`` frames into the first ones so the end flows into the start.

        The result is ``overlap`` frames shorter: it starts at frame ``overlap`` and its blended
        tail leads straight back into that frame.
        """
        n = len(self.frames)
        overlap = max(1, min(overlap, n // 2))
        if n < 3:
            return
        self.checkpoint()
        head, middle, tail = self.frames[:overlap], self.frames[overlap:n - overlap], self.frames[n - overlap:]
        blended = [
            Frame(Image.blend(t.image, h.image, (k + 1) / (overlap + 1)), t.duration)
            for k, (t, h) in enumerate(zip(tail, head))
        ]
        self.frames = middle + blended
        self.index = min(self.index, len(self.frames) - 1)
        self.changed()

    def find_loop(self, min_frames: int) -> tuple[int, int, float]:
        """Best stretch to loop: frames ``start .. end-1`` where frame ``end`` looks most like ``start``.

        Returns (start, end, difference) with difference = mean absolute pixel difference 0..255.
        A hard cut there needs no blending, so nothing gets blurry.
        """
        n = len(self.frames)
        if n < min_frames + 1:
            raise ValueError("animation too short for that loop length")
        imgs = [f.image.convert("RGB") for f in self.frames]
        best = (0, n - 1, float("inf"))
        for start in range(n):
            for end in range(start + min_frames, n):
                diff = sum(ImageStat.Stat(ImageChops.difference(imgs[start], imgs[end])).mean) / 3
                if diff < best[2]:
                    best = (start, end, diff)
        return best

    def trim_to_loop(self, start: int, end: int) -> None:
        self.checkpoint()
        self.frames = self.frames[start:end]
        self.index = min(max(0, self.index - start), len(self.frames) - 1)
        self.changed()

    def set_duration(self, ms: int, all_frames: bool = False) -> None:
        self.checkpoint()
        for f in self.frames if all_frames else [self.frame]:
            f.duration = max(10, int(ms))
        self.changed()

    # Drawing primitives (call checkpoint() once before a stroke)

    def in_bounds(self, p: Point) -> bool:
        return 0 <= p[0] < self.width and 0 <= p[1] < self.height

    def pixel(self, p: Point) -> Color:
        return self.image.getpixel(p)  # type: ignore[return-value]

    def plot(self, points: Iterable[Point], color: Color, size: int = 1) -> None:
        img = self.image
        for x, y in points:
            for dx in range(size):
                for dy in range(size):
                    q = (x + dx - size // 2, y + dy - size // 2)
                    if self.in_bounds(q):
                        img.putpixel(q, color)
        self.changed()

    def line(self, a: Point, b: Point, color: Color, size: int = 1) -> None:
        self.plot(line_points(a, b), color, size)

    def rect(self, a: Point, b: Point, color: Color, fill: bool = False) -> None:
        box = _box(a, b)
        ImageDraw.Draw(self.image).rectangle(box, fill=color if fill else None, outline=color)
        self.changed()

    def ellipse(self, a: Point, b: Point, color: Color, fill: bool = False) -> None:
        box = _box(a, b)
        ImageDraw.Draw(self.image).ellipse(box, fill=color if fill else None, outline=color)
        self.changed()

    def flood_fill(self, p: Point, color: Color) -> None:
        if not self.in_bounds(p):
            return
        img = self.image
        target = img.getpixel(p)
        if target == color:
            return
        stack = [p]
        while stack:
            x, y = stack.pop()
            if not self.in_bounds((x, y)) or img.getpixel((x, y)) != target:
                continue
            img.putpixel((x, y), color)
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
        self.changed()

    # Rectangular regions (boxes are inclusive: x0, y0, x1, y1)

    def clip_box(self, box: Box) -> Box | None:
        x0, y0, x1, y1 = _box((box[0], box[1]), (box[2], box[3]))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.width - 1, x1), min(self.height - 1, y1)
        return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None

    def crop(self, box: Box | None = None) -> Image.Image:
        if box is None:
            return self.image.copy()
        x0, y0, x1, y1 = box
        return self.image.crop((x0, y0, x1 + 1, y1 + 1))

    def fill_box(self, box: Box, color: Color) -> None:
        x0, y0, x1, y1 = box
        self.image.paste(color, (x0, y0, x1 + 1, y1 + 1))
        self.changed()

    def stamp(self, img: Image.Image, x: int, y: int) -> None:
        """Paste ``img`` with its top-left at (x, y); parts outside the frame are dropped."""
        self.image.paste(img.convert("RGB"), (x, y))
        self.changed()

    def move_box(self, box: Box, dx: int, dy: int, background: Color, all_frames: bool = False) -> Box:
        """Move a region's pixels, leaving the background colour behind. Returns the moved box."""
        self.checkpoint()
        x0, y0, x1, y1 = box
        for f in self.frames if all_frames else [self.frame]:
            piece = f.image.crop((x0, y0, x1 + 1, y1 + 1))
            f.image.paste(background, (x0, y0, x1 + 1, y1 + 1))
            f.image.paste(piece, (x0 + dx, y0 + dy))
        self.changed()
        return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)

    # Whole-frame operations (record their own undo step)

    def clear(self, color: Color = (0, 0, 0), all_frames: bool = False) -> None:
        self.checkpoint()
        for f in self.frames if all_frames else [self.frame]:
            f.image.paste(color, (0, 0, self.width, self.height))
        self.changed()

    def shift(self, dx: int, dy: int, all_frames: bool = False) -> None:
        self.checkpoint()
        for f in self.frames if all_frames else [self.frame]:
            src = f.image.copy()
            for y in range(self.height):
                for x in range(self.width):
                    f.image.putpixel(((x + dx) % self.width, (y + dy) % self.height), src.getpixel((x, y)))
        self.changed()

    def flip(self, horizontal: bool, all_frames: bool = False) -> None:
        self.checkpoint()
        method = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
        for f in self.frames if all_frames else [self.frame]:
            f.image = f.image.transpose(method)
        self.changed()

    def resize_canvas(self, width: int, height: int) -> None:
        """Change the panel size, keeping existing pixels anchored top-left."""
        if (width, height) == (self.width, self.height):
            return
        self.checkpoint()
        for f in self.frames:
            img = Image.new("RGB", (width, height))
            img.paste(f.image, (0, 0))
            f.image = img
        self.width, self.height = width, height
        self.changed()

    def paste_image(self, img: Image.Image, fit: str = "contain") -> None:
        """Replace the current frame with a scaled picture."""
        self.checkpoint()
        self.frame.image = render.fit(img, self.width, self.height, fit)
        self.changed()

    # Import / export

    def load(self, path: str | Path, fit: str = "contain") -> None:
        """Open a picture or animation (PNG, APNG, GIF, JPEG, ...) as the whole document."""
        self.checkpoint()
        src = Image.open(path)
        frames = []
        for f in ImageSequence.Iterator(src):
            duration = f.info.get("duration", src.info.get("duration", DEFAULT_DURATION)) or DEFAULT_DURATION
            frames.append(Frame(render.fit(f.convert("RGBA"), self.width, self.height, fit), int(duration)))
        self.frames = frames or [Frame(self._blank())]
        self.index = 0
        self.changed()

    def save(self, path: str | Path) -> None:
        """PNG/APNG keeps exact colours and durations; .gif is also accepted."""
        path = Path(path)
        if path.suffix.lower() == ".gif":
            path.write_bytes(self.gif_bytes())
            return
        first, rest = self.frames[0], self.frames[1:]
        first.image.save(
            path,
            format="PNG",
            save_all=bool(rest),
            append_images=[f.image for f in rest],
            duration=[f.duration for f in self.frames],
            loop=0,
        )

    def png_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def gif_bytes(self) -> bytes:
        return render.encode_gif([f.image for f in self.frames], [f.duration for f in self.frames])

    def rgb_bytes(self, index: int | None = None) -> bytes:
        return self.frames[self.index if index is None else index].image.tobytes()

    def _blank(self) -> Image.Image:
        return Image.new("RGB", (self.width, self.height))


def line_points(a: Point, b: Point) -> list[Point]:
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _box(a: Point, b: Point) -> tuple[int, int, int, int]:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])
