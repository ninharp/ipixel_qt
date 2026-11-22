"""The studio's own record of programs stored on a panel (the panel cannot report its slots)."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import asdict, dataclass

from PIL import Image
from PySide6.QtCore import QObject, QSettings, Signal

MAX_SLOT = 99


@dataclass
class Program:
    slot: int
    kind: str  # "image", "animation" or "text"
    name: str
    thumb: str = ""  # base64 PNG of the first frame

    def thumbnail(self) -> Image.Image | None:
        if not self.thumb:
            return None
        return Image.open(io.BytesIO(base64.b64decode(self.thumb))).convert("RGB")


def encode_thumb(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class ProgramStore(QObject):
    changed = Signal()

    def __init__(self, settings: QSettings, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self.device = ""
        self.programs: dict[int, Program] = {}

    def _key(self) -> str:
        return "programs/" + self.device.replace("/", "_").replace(":", "")

    def set_device(self, device: str) -> None:
        self.device = device
        raw = self.settings.value(self._key(), "")
        try:
            items = json.loads(raw) if raw else []
        except ValueError:
            items = []
        self.programs = {int(d["slot"]): Program(**d) for d in items}
        self.changed.emit()

    def _save(self) -> None:
        if self.device:
            self.settings.setValue(self._key(), json.dumps([asdict(p) for p in self.sorted()]))
        self.changed.emit()

    def sorted(self) -> list[Program]:
        return [self.programs[s] for s in sorted(self.programs)]

    def record(self, program: Program) -> None:
        self.programs[program.slot] = program
        self._save()

    def remove(self, slots: list[int]) -> None:
        for s in slots:
            self.programs.pop(s, None)
        self._save()

    def clear(self) -> None:
        self.programs.clear()
        self._save()

    def next_free(self) -> int:
        for slot in range(1, MAX_SLOT + 1):
            if slot not in self.programs:
                return slot
        return MAX_SLOT
