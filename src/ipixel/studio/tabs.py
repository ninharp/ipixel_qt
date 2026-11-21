"""Text, control and screen-mirror tabs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import protocol as p
from .. import render
from .device import DeviceController
from .editor import color_icon, pil_to_qimage
from .model import Color

FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
]

EFFECT_LABELS = {
    p.TextEffect.FIXED: "Stehend",
    p.TextEffect.SCROLL_LEFT: "Nach links laufen",
    p.TextEffect.SCROLL_RIGHT: "Nach rechts laufen",
    p.TextEffect.SCROLL_UP: "Nach oben laufen",
    p.TextEffect.SCROLL_DOWN: "Nach unten laufen",
    p.TextEffect.BLINK: "Blinken",
    p.TextEffect.BREATH: "Atmen",
    p.TextEffect.SNOWFLAKES: "Schneeflocken",
    p.TextEffect.LASER: "Laser",
}


def system_fonts() -> list[Path]:
    found: dict[str, Path] = {}
    for d in FONT_DIRS:
        if d.is_dir():
            for f in d.rglob("*"):
                if f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    found.setdefault(f.stem, f)
    return [found[k] for k in sorted(found, key=str.lower)]


class ColorButton(QPushButton):
    changed = Signal(tuple)

    def __init__(self, color: Color, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.clicked.connect(self._choose)
        self.set(color)

    def set(self, color: Color) -> None:
        self.color = color
        self.setIcon(color_icon(color))
        self.setText("#%02x%02x%02x" % color)
        self.changed.emit(color)

    def _choose(self) -> None:
        c = QColorDialog.getColor(QColor(*self.color), self, self.title)
        if c.isValid():
            self.set((c.red(), c.green(), c.blue()))


class TextTab(QWidget):
    def __init__(self, device: DeviceController, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self.panel_height = 16

        self.text = QLineEdit("Hallo Welt")
        self.color = ColorButton((255, 128, 0), "Textfarbe")
        self.bg_on = QCheckBox("Hintergrund")
        self.bg = ColorButton((0, 0, 64), "Hintergrundfarbe")
        self.effect = QComboBox()
        for eff, label in EFFECT_LABELS.items():
            self.effect.addItem(label, int(eff))
        self.effect.setCurrentIndex(1)
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 100)
        self.speed.setValue(50)
        self.font = QComboBox()
        self.font.addItem("Standard (Pillow)", None)
        for f in system_fonts():
            self.font.addItem(f.stem, str(f))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(6, 64)
        self.height_spin.setValue(self.panel_height)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(20, 230)
        self.threshold.setValue(96)
        self.threshold.setToolTip("Ab welcher Helligkeit ein Pixel der Schrift leuchtet")
        self.slot = QSpinBox()
        self.slot.setRange(0, 99)
        self.slot.setSpecialValueText("nur anzeigen")
        self.slot.setPrefix("Platz ")
        self.preview = QLabel()
        self.preview.setMinimumHeight(80)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.preview.setStyleSheet("background:#111; padding:6px")
        send = QPushButton("Text senden")
        send.clicked.connect(self.send)
        self.send_btn = send

        bg_row = QHBoxLayout()
        bg_row.addWidget(self.bg_on)
        bg_row.addWidget(self.bg)
        form = QFormLayout()
        form.addRow("Text", self.text)
        form.addRow("Farbe", self.color)
        form.addRow("", bg_row)
        form.addRow("Effekt", self.effect)
        form.addRow("Tempo", self.speed)
        form.addRow("Schrift", self.font)
        form.addRow("Höhe (Pixel)", self.height_spin)
        form.addRow("Schwelle", self.threshold)
        form.addRow("", self.slot)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Vorschau (so werden die Zeichen gerastert):"))
        lay.addWidget(self.preview)
        lay.addWidget(send)
        lay.addStretch()

        for sig in (self.text.textChanged, self.font.currentIndexChanged, self.height_spin.valueChanged,
                    self.threshold.valueChanged, self.color.changed, self.bg.changed, self.bg_on.toggled):
            sig.connect(self.update_preview)
        device.stateChanged.connect(lambda _s: send.setEnabled(device.connected))
        send.setEnabled(device.connected)
        self.update_preview()

    def set_panel_size(self, _width: int, height: int) -> None:
        self.panel_height = height
        self.height_spin.setValue(min(height, 64))

    def _glyphs(self):
        return render.glyphs(
            self.text.text() or " ",
            self.height_spin.value(),
            self.color.color,
            self.font.currentData(),
            self.threshold.value(),
        )

    def update_preview(self) -> None:
        try:
            gl = self._glyphs()
        except OSError as e:
            self.preview.setText(f"Schrift nicht ladbar: {e}")
            return
        h = self.height_spin.value()
        width = sum(g[0] for g in gl) or 1
        bg = self.bg.color if self.bg_on.isChecked() else (0, 0, 0)
        img = Image.new("RGB", (width, h), bg)
        x = 0
        for w, gh, bits, rgb in gl:
            row_bytes = (w + 7) // 8
            for y in range(gh):
                for gx in range(w):
                    if bits[y * row_bytes + gx // 8] >> (gx % 8) & 1:
                        img.putpixel((x + gx, y), rgb)
            x += w
        scale = max(1, min(6, 600 // width, 72 // h))
        pm = QPixmap.fromImage(pil_to_qimage(img)).scaled(width * scale, h * scale)
        self.preview.setPixmap(pm)

    def send(self) -> None:
        text = self.text.text()
        if not text:
            return
        try:
            gl = self._glyphs()
        except OSError as e:
            self.device.error.emit(f"Schrift: {e}")
            return
        info = self.device.info
        border = (0, 0, 0) if info and info.has_border_fields else None
        body = p.text_payload(
            gl,
            self.color.color,
            self.bg.color if self.bg_on.isChecked() else None,
            effect=self.effect.currentData(),
            speed=self.speed.value(),
            border=border,
        )
        slot = self.slot.value() or p.SLOT_TEMPORARY
        self.device.run(f"Text „{text}“ gesendet", lambda px: px.upload(p.Upload.TEXT, body, slot))


class ControlTab(QWidget):
    def __init__(self, device: DeviceController, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        grid = QGridLayout(self)
        grid.addWidget(self._panel_box(), 0, 0)
        grid.addWidget(self._clock_box(), 0, 1)
        grid.addWidget(self._timer_box(), 1, 0)
        grid.addWidget(self._score_box(), 1, 1)
        grid.addWidget(self._storage_box(), 2, 0)
        grid.addWidget(self._raw_box(), 2, 1)
        grid.setRowStretch(3, 1)
        device.stateChanged.connect(lambda _s: self.setEnabled(device.connected))
        self.setEnabled(device.connected)

    def _run(self, label, job) -> None:
        self.device.run(label, job)

    def _panel_box(self) -> QGroupBox:
        box = QGroupBox("Panel")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        on = QPushButton("Einschalten")
        off = QPushButton("Ausschalten")
        on.clicked.connect(lambda: self._run("Eingeschaltet", lambda px: px.power(True)))
        off.clicked.connect(lambda: self._run("Ausgeschaltet", lambda px: px.power(False)))
        row.addWidget(on)
        row.addWidget(off)
        lay.addLayout(row)
        flip = QCheckBox("Auf dem Kopf (180°)")
        flip.toggled.connect(lambda v: self._run("Ausrichtung", lambda px: px.flip(v)))
        lay.addWidget(flip)
        speed_row = QHBoxLayout()
        speed = QSpinBox()
        speed.setRange(1, 100)
        speed.setValue(50)
        speed_btn = QPushButton("Lauftext-Tempo setzen")
        speed_btn.clicked.connect(lambda: self._run("Tempo", lambda px: px.text_speed(speed.value())))
        speed_row.addWidget(speed)
        speed_row.addWidget(speed_btn)
        lay.addLayout(speed_row)
        return box

    def _clock_box(self) -> QGroupBox:
        box = QGroupBox("Uhr")
        lay = QFormLayout(box)
        style = QSpinBox()
        style.setRange(1, 8)
        h24 = QCheckBox("24 Stunden")
        h24.setChecked(True)
        date = QCheckBox("Datum zeigen")
        date.setChecked(True)
        show = QPushButton("Uhr anzeigen")
        show.clicked.connect(lambda: self._run(
            "Uhr", lambda px: px.clock(style.value(), h24.isChecked(), date.isChecked())))
        sync = QPushButton("Uhrzeit vom Rechner stellen")
        sync.clicked.connect(lambda: self._run("Uhrzeit gestellt", lambda px: px.query_info()))
        lay.addRow("Stil", style)
        lay.addRow(h24)
        lay.addRow(date)
        lay.addRow(show)
        lay.addRow(sync)
        return box

    def _timer_box(self) -> QGroupBox:
        box = QGroupBox("Countdown und Stoppuhr")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        mins = QSpinBox()
        mins.setRange(0, 99)
        mins.setValue(5)
        mins.setSuffix(" min")
        secs = QSpinBox()
        secs.setRange(0, 59)
        secs.setSuffix(" s")
        row.addWidget(QLabel("Countdown"))
        row.addWidget(mins)
        row.addWidget(secs)
        lay.addLayout(row)
        cd = QHBoxLayout()
        for text, action in [("Start", p.TimerAction.START), ("Pause", p.TimerAction.PAUSE),
                             ("Weiter", p.TimerAction.RESUME), ("Zurücksetzen", p.TimerAction.RESET)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=action: self._run(
                "Countdown", lambda px: px.countdown(mins.value(), secs.value(), a)))
            cd.addWidget(b)
        lay.addLayout(cd)
        lay.addWidget(QLabel("Stoppuhr"))
        sw = QHBoxLayout()
        for text, action in [("Start", p.TimerAction.START), ("Pause", p.TimerAction.PAUSE),
                             ("Weiter", p.TimerAction.RESUME), ("Zurücksetzen", p.TimerAction.RESET)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=action: self._run("Stoppuhr", lambda px: px.stopwatch(a)))
            sw.addWidget(b)
        lay.addLayout(sw)
        return box

    def _score_box(self) -> QGroupBox:
        box = QGroupBox("Anzeigetafel")
        lay = QGridLayout(box)
        self.left = QSpinBox()
        self.right = QSpinBox()
        for col, spin in ((0, self.left), (2, self.right)):
            spin.setRange(0, 999)
            plus = QPushButton("+1")
            plus.clicked.connect(lambda _=False, s=spin: (s.setValue(s.value() + 1), self._send_score()))
            minus = QPushButton("−1")
            minus.clicked.connect(lambda _=False, s=spin: (s.setValue(s.value() - 1), self._send_score()))
            lay.addWidget(spin, 0, col)
            lay.addWidget(plus, 1, col)
            lay.addWidget(minus, 2, col)
        lay.addWidget(QLabel(":"), 0, 1, Qt.AlignmentFlag.AlignCenter)
        send = QPushButton("Anzeigen")
        send.clicked.connect(self._send_score)
        reset = QPushButton("0 : 0")
        reset.clicked.connect(lambda: (self.left.setValue(0), self.right.setValue(0), self._send_score()))
        lay.addWidget(send, 3, 0)
        lay.addWidget(reset, 3, 2)
        return box

    def _send_score(self) -> None:
        l, r = self.left.value(), self.right.value()
        self._run(f"Stand {l}:{r}", lambda px: px.scoreboard(l, r))

    def _storage_box(self) -> QGroupBox:
        box = QGroupBox("Gespeicherte Programme")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        slots = QLineEdit()
        slots.setPlaceholderText("z. B. 1, 3, 4")
        delete = QPushButton("Plätze löschen")

        def delete_slots() -> None:
            try:
                nums = [int(s) for s in slots.text().replace(";", ",").split(",") if s.strip()]
            except ValueError:
                self.device.error.emit("Plätze als Zahlen mit Komma angeben")
                return
            if nums:
                self._run(f"Plätze {nums} gelöscht", lambda px: px.delete_slots(nums))

        delete.clicked.connect(delete_slots)
        row.addWidget(slots)
        row.addWidget(delete)
        lay.addLayout(row)
        clear = QPushButton("Alle Programme löschen …")
        clear.clicked.connect(self._clear_all)
        lay.addWidget(clear)
        return box

    def _clear_all(self) -> None:
        answer = QMessageBox.question(
            self, "Alles löschen", "Alle auf dem Panel gespeicherten Programme löschen?")
        if answer == QMessageBox.StandardButton.Yes:
            self._run("Alles gelöscht", lambda px: px.clear())

    def _raw_box(self) -> QGroupBox:
        box = QGroupBox("Rohbefehl (für Experimente)")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        raw = QLineEdit()
        raw.setPlaceholderText("Hex, z. B. 05 00 07 01 01")
        result = QLabel()
        result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        send = QPushButton("Senden")

        def go() -> None:
            try:
                data = bytes.fromhex(raw.text())
            except ValueError:
                self.device.error.emit("Ungültiges Hex")
                return

            async def job(px):
                try:
                    reply = await px.request(data, timeout=2.0)
                    result.setText("Antwort: " + reply.hex(" "))
                except TimeoutError:
                    result.setText("keine Antwort")

            self._run("Rohbefehl", job)

        send.clicked.connect(go)
        raw.returnPressed.connect(go)
        row.addWidget(raw)
        row.addWidget(send)
        lay.addLayout(row)
        lay.addWidget(result)
        return box


class MirrorTab(QWidget):
    """Stream a screen region to the panel in live mode."""

    def __init__(self, device: DeviceController, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self.size = (64, 16)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._grab)

        self.screen = QComboBox()
        for i, s in enumerate(QGuiApplication.screens()):
            g = s.geometry()
            self.screen.addItem(f"{s.name()} ({g.width()}×{g.height()})", i)
        self.follow = QCheckBox("Bereich folgt der Maus")
        self.x, self.y, self.w = QSpinBox(), QSpinBox(), QSpinBox()
        for s in (self.x, self.y, self.w):
            s.setRange(0, 10000)
        self.w.setRange(8, 10000)
        self.w.setValue(256)
        self.fit = QComboBox()
        for key, label in (("cover", "Ausfüllen"), ("contain", "Einpassen"), ("stretch", "Strecken")):
            self.fit.addItem(label, key)
        self.fps = QSpinBox()
        self.fps.setRange(1, 20)
        self.fps.setValue(8)
        self.fps.setSuffix(" Bilder/s")
        self.start = QPushButton("Spiegeln starten")
        self.start.setCheckable(True)
        self.start.toggled.connect(self._toggle)
        self.preview = QLabel()
        self.preview.setMinimumHeight(80)
        self.preview.setStyleSheet("background:#111")
        self.note = QLabel(
            "Höhe ergibt sich aus dem Seitenverhältnis des Panels. Beim ersten Start fragt macOS "
            "nach der Freigabe „Bildschirmaufnahme“ für dein Terminal.")
        self.note.setWordWrap(True)

        pos = QHBoxLayout()
        for label, spin in (("x", self.x), ("y", self.y), ("Breite", self.w)):
            pos.addWidget(QLabel(label))
            pos.addWidget(spin)
        form = QFormLayout()
        form.addRow("Bildschirm", self.screen)
        form.addRow(self.follow)
        form.addRow("Bereich", pos)
        form.addRow("Skalierung", self.fit)
        form.addRow("Tempo", self.fps)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.start)
        lay.addWidget(self.preview)
        lay.addWidget(self.note)
        lay.addStretch()
        device.liveChanged.connect(self._live_changed)

    def set_panel_size(self, width: int, height: int) -> None:
        self.size = (width, height)

    def _toggle(self, on: bool) -> None:
        self.start.setText("Spiegeln stoppen" if on else "Spiegeln starten")
        if on:
            self.device.set_live(True)
            if not self.device.live:
                self.start.setChecked(False)
                return
            self.timer.start(int(1000 / self.fps.value()))
        else:
            self.timer.stop()
            self.device.set_live(False)

    def _live_changed(self, on: bool) -> None:
        if not on and self.start.isChecked():
            self.start.setChecked(False)

    def _grab(self) -> None:
        from PySide6.QtGui import QCursor

        screens = QGuiApplication.screens()
        screen = screens[min(self.screen.currentData() or 0, len(screens) - 1)]
        w = self.w.value()
        h = max(1, round(w * self.size[1] / self.size[0]))
        if self.follow.isChecked():
            c = QCursor.pos() - screen.geometry().topLeft()
            x, y = c.x() - w // 2, c.y() - h // 2
        else:
            x, y = self.x.value(), self.y.value()
        pm = screen.grabWindow(0, x, y, w, h)
        qimg = pm.toImage().convertToFormat(pm.toImage().Format.Format_RGB888)
        ptr = bytes(qimg.constBits())[: qimg.bytesPerLine() * qimg.height()]
        img = Image.frombuffer("RGB", (qimg.width(), qimg.height()), ptr, "raw", "RGB", qimg.bytesPerLine(), 1)
        fitted = render.fit(img, *self.size, self.fit.currentData())
        self.preview.setPixmap(QPixmap.fromImage(pil_to_qimage(fitted)).scaled(self.size[0] * 4, self.size[1] * 4))
        self.device.push_live(fitted.tobytes())
