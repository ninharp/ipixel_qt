"""Text, control and screen-mirror tabs."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
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
    QListWidget,
    QListWidgetItem,
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
from .i18n import tr
from .model import Color
from .programs import Program, ProgramStore, encode_thumb

FONT_DIRS = [
    # macOS
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    # Linux
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
    # Windows
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Microsoft/Windows/Fonts",
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
        self.programs: ProgramStore | None = None
        self.panel_width = 64
        self.panel_height = 16

        self.text = QLineEdit(tr("Hallo Welt"))
        self.color = ColorButton((255, 128, 0), tr("Textfarbe"))
        self.bg_on = QCheckBox(tr("Hintergrund"))
        self.bg = ColorButton((0, 0, 64), tr("Hintergrundfarbe"))
        self.effect = QComboBox()
        for eff, label in EFFECT_LABELS.items():
            self.effect.addItem(tr(label), int(eff))
        self.effect.setCurrentIndex(1)
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setValue(p.DEFAULT_TEXT_SPEED)
        self.font = QComboBox()
        self.font.addItem(tr("Standard (Pillow)"), None)
        for f in system_fonts():
            self.font.addItem(f.stem, str(f))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(6, 64)
        self.height_spin.setValue(self.panel_height)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(5, 80)
        self.size_spin.setValue(render.default_font_size(self.panel_height))
        self.size_spin.setToolTip(tr("Kleinere Schrift passt mehr Zeichen aufs Panel"))
        self.height_spin.valueChanged.connect(lambda h: self.size_spin.setValue(render.default_font_size(h)))
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 6)
        self.spacing_spin.setValue(1)
        self.spacing_spin.setSuffix(" px")
        self.fit_label = QLabel()
        self.fit_label.setWordWrap(True)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(20, 230)
        self.threshold.setValue(96)
        self.threshold.setToolTip(tr("Ab welcher Helligkeit ein Pixel der Schrift leuchtet"))
        self.slot = QSpinBox()
        self.slot.setRange(0, 99)
        self.slot.setSpecialValueText(tr("nur anzeigen"))
        self.slot.setPrefix(tr("Platz "))
        self.preview = QLabel()
        self.preview.setMinimumHeight(80)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.preview.setStyleSheet("background:#111; padding:6px")
        send = QPushButton(tr("Text senden"))
        send.clicked.connect(self.send)
        self.send_btn = send

        bg_row = QHBoxLayout()
        bg_row.addWidget(self.bg_on)
        bg_row.addWidget(self.bg)
        form = QFormLayout()
        form.addRow(tr("Text"), self.text)
        form.addRow(tr("Farbe"), self.color)
        form.addRow("", bg_row)
        form.addRow(tr("Effekt"), self.effect)
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel(tr("langsam")))
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(QLabel(tr("schnell")))
        self.speed_value = QLabel()
        self.speed_value.setMinimumWidth(28)
        self.speed.valueChanged.connect(lambda v: self.speed_value.setText(str(v)))
        self.speed_value.setText(str(self.speed.value()))
        speed_row.addWidget(self.speed_value)
        form.addRow(tr("Tempo"), speed_row)
        form.addRow(tr("Schrift"), self.font)
        form.addRow(tr("Höhe (Pixel)"), self.height_spin)
        form.addRow(tr("Schriftgröße"), self.size_spin)
        form.addRow(tr("Zeichenabstand"), self.spacing_spin)
        form.addRow(tr("Schwelle"), self.threshold)
        slot_row = QHBoxLayout()
        slot_row.addWidget(self.slot, 1)
        free = QPushButton(tr("frei"))
        free.setToolTip(tr("Nächsten freien Programmplatz eintragen"))
        free.clicked.connect(lambda: self.slot.setValue(self.programs.next_free() if self.programs else 1))
        slot_row.addWidget(free)
        form.addRow("", slot_row)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel(tr("Vorschau (so werden die Zeichen gerastert):")))
        lay.addWidget(self.preview)
        lay.addWidget(self.fit_label)
        lay.addWidget(send)
        lay.addStretch()

        for sig in (self.text.textChanged, self.font.currentIndexChanged, self.height_spin.valueChanged,
                    self.size_spin.valueChanged, self.spacing_spin.valueChanged, self.effect.currentIndexChanged,
                    self.threshold.valueChanged, self.color.changed, self.bg.changed, self.bg_on.toggled):
            sig.connect(self.update_preview)
        device.stateChanged.connect(lambda _s: send.setEnabled(device.connected))
        send.setEnabled(device.connected)
        self.update_preview()

    def set_panel_size(self, width: int, height: int) -> None:
        self.panel_width = width
        self.panel_height = height
        self.height_spin.setValue(min(height, 64))
        self.update_preview()

    def _glyphs(self):
        return render.glyphs(
            self.text.text() or " ",
            self.height_spin.value(),
            self.color.color,
            self.font.currentData(),
            self.threshold.value(),
            spacing=self.spacing_spin.value(),
            size=self.size_spin.value(),
        )

    def _scrolls(self) -> bool:
        return p.TextEffect(self.effect.currentData()).scrolls

    def _send_glyphs(self):
        """Glyphs as they go to the panel: per character for scrolling, one per screen otherwise."""
        gl = self._glyphs()
        if self._scrolls():
            return gl
        return render.page_glyphs(self.text.text() or " ", gl, self.panel_width)

    def _update_fit(self, gl, screens: int) -> None:
        width, panel = render.text_width(gl), self.panel_width
        if width <= panel:
            text = tr("{width} von {panel} Pixel – passt auf eine Seite", width=width, panel=panel)
        elif self._scrolls():
            text = tr("{width} von {panel} Pixel – zu breit für eine Seite, läuft als Lauftext durch",
                      width=width, panel=panel)
        elif screens == 1:
            text = tr("{width} von {panel} Pixel – passt auf eine Seite", width=width, panel=panel)
        else:
            text = tr("Zu breit für eine Seite: dieser Effekt zeigt den Text nacheinander auf {pages} Seiten "
                      "(Umbruch an Leerzeichen, siehe Vorschau). Ein Lauf-Effekt zeigt ihn am Stück.",
                      pages=screens)
        self.fit_label.setText(text)

    def update_preview(self) -> None:
        try:
            gl = self._glyphs()
            shown = gl if self._scrolls() else self._send_glyphs()
        except OSError as e:
            self.preview.setText(tr("Schrift nicht ladbar: {error}", error=e))
            return
        h = self.height_spin.value()
        gap = 0 if self._scrolls() else 3
        width = max(1, sum(g[0] for g in shown) + gap * (len(shown) - 1))
        bg = self.bg.color if self.bg_on.isChecked() else (0, 0, 0)
        img = Image.new("RGB", (width, h), (40, 40, 40))
        x = 0
        for w, gh, bits, rgb in shown:
            page_w = self.panel_width if gap else w
            img.paste(bg, (x, 0, x + min(page_w, w), h))
            row_bytes = (w + 7) // 8
            for y in range(gh):
                for gx in range(w):
                    if bits[y * row_bytes + gx // 8] >> (gx % 8) & 1:
                        img.putpixel((x + gx, y), rgb)
            x += w + gap
        scale = max(1, min(6, 600 // max(width, self.panel_width), 72 // h))
        pm = QPixmap.fromImage(pil_to_qimage(img)).scaled(width * scale, h * scale)
        if self._scrolls() and width > self.panel_width:
            # Mark where the panel ends.
            qp = QPainter(pm)
            qp.setPen(QPen(QColor(255, 60, 60), 1, Qt.PenStyle.DashLine))
            qp.drawLine(self.panel_width * scale, 0, self.panel_width * scale, h * scale)
            qp.end()
        self.preview.setPixmap(pm)
        self._update_fit(gl, len(shown))

    def send(self) -> None:
        text = self.text.text()
        if not text:
            return
        try:
            gl = self._send_glyphs()
        except OSError as e:
            self.device.error.emit(tr("Schrift: {error}", error=e))
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
        program = Program(slot, "text", text, encode_thumb(self._thumbnail(gl)))
        self.device.upload(tr("Text"), tr("Text „{text}“ gesendet", text=text), p.Upload.TEXT, body, slot, program)

    def _thumbnail(self, gl) -> Image.Image:
        """First screen of the text as the panel shows it."""
        h = self.height_spin.value()
        img = Image.new("RGB", (self.panel_width, h))
        x = 0
        for w, gh, bits, rgb in gl:
            rb = (w + 7) // 8
            for y in range(gh):
                for gx in range(w):
                    if x + gx < self.panel_width and bits[y * rb + gx // 8] >> (gx % 8) & 1:
                        img.putpixel((x + gx, y), rgb)
            x += w
            if x >= self.panel_width:
                break
        return img


class ControlTab(QWidget):
    def __init__(self, device: DeviceController, programs: ProgramStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.device = device
        self.programs = programs
        self.feature_boxes: dict[p.Feature, tuple[QGroupBox, str]] = {}
        grid = QGridLayout(self)
        grid.addWidget(self._panel_box(), 0, 0)
        grid.addWidget(self._clock_box(), 0, 1)
        grid.addWidget(self._programs_box(), 1, 0, 1, 2)
        grid.addWidget(self._countdown_box(), 2, 0)
        grid.addWidget(self._stopwatch_box(), 2, 1)
        grid.addWidget(self._score_box(), 3, 0)
        grid.addWidget(self._raw_box(), 3, 1)
        grid.setRowStretch(4, 1)
        device.stateChanged.connect(lambda _s: self.setEnabled(device.connected))
        device.infoChanged.connect(lambda _i: self.apply_features(device.features))
        self.setEnabled(device.connected)

    def apply_features(self, features: dict[p.Feature, bool | None]) -> None:
        """Grey out screens the vendor app does not offer for this panel model."""
        for feature, (box, title) in self.feature_boxes.items():
            unsupported = features.get(feature) is False
            box.setEnabled(not unsupported)
            box.setTitle(tr("{title} (von diesem Panel nicht unterstützt)", title=title) if unsupported else title)
            box.setToolTip(tr("Die Hersteller-App bietet diese Funktion bei diesem Panelmodell nicht an; "
                              "das Panel reagiert nicht auf den Befehl.") if unsupported else "")

    def _feature_box(self, feature: p.Feature, title: str) -> QGroupBox:
        box = QGroupBox(title)
        self.feature_boxes[feature] = (box, title)
        return box

    def _run(self, label, job) -> None:
        self.device.run(label, job)

    def _panel_box(self) -> QGroupBox:
        box = QGroupBox(tr("Panel"))
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        on = QPushButton(tr("Einschalten"))
        off = QPushButton(tr("Ausschalten"))
        on.clicked.connect(lambda: self._run(tr("Eingeschaltet"), lambda px: px.power(True)))
        off.clicked.connect(lambda: self._run(tr("Ausgeschaltet"), lambda px: px.power(False)))
        row.addWidget(on)
        row.addWidget(off)
        lay.addLayout(row)
        flip = QCheckBox(tr("Auf dem Kopf (180°)"))
        flip.toggled.connect(lambda v: self._run(tr("Ausrichtung"), lambda px: px.flip(v)))
        lay.addWidget(flip)
        speed_row = QHBoxLayout()
        speed = QSpinBox()
        speed.setRange(0, 100)
        speed.setToolTip(tr("0 = langsam, 100 = schnell"))
        speed.setValue(p.DEFAULT_TEXT_SPEED)
        speed_btn = QPushButton(tr("Lauftext-Tempo setzen"))
        speed_btn.clicked.connect(lambda: self._run(tr("Tempo"), lambda px: px.text_speed(speed.value())))
        speed_row.addWidget(speed)
        speed_row.addWidget(speed_btn)
        lay.addLayout(speed_row)
        return box

    def _clock_box(self) -> QGroupBox:
        box = QGroupBox(tr("Uhr"))
        lay = QFormLayout(box)
        style = QSpinBox()
        style.setRange(1, 8)
        h24 = QCheckBox(tr("24 Stunden"))
        h24.setChecked(True)
        date = QCheckBox(tr("Datum zeigen"))
        date.setChecked(True)
        show = QPushButton(tr("Uhr anzeigen"))
        show.clicked.connect(lambda: self._run(
            tr("Uhr"), lambda px: px.clock(style.value(), h24.isChecked(), date.isChecked())))
        sync = QPushButton(tr("Uhrzeit vom Rechner stellen"))
        sync.clicked.connect(lambda: self._run(tr("Uhrzeit gestellt"), lambda px: px.query_info()))
        lay.addRow(tr("Stil"), style)
        lay.addRow(h24)
        lay.addRow(date)
        lay.addRow(show)
        lay.addRow(sync)
        return box

    @staticmethod
    def _timer_buttons() -> list[tuple[str, p.TimerAction]]:
        return [(tr("Start"), p.TimerAction.START), (tr("Pause"), p.TimerAction.PAUSE),
                (tr("Weiter"), p.TimerAction.RESUME), (tr("Zurücksetzen"), p.TimerAction.RESET)]

    def _countdown_box(self) -> QGroupBox:
        box = self._feature_box(p.Feature.COUNTDOWN, tr("Countdown"))
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        mins = QSpinBox()
        mins.setRange(0, 99)
        mins.setValue(5)
        mins.setSuffix(" min")
        secs = QSpinBox()
        secs.setRange(0, 59)
        secs.setSuffix(" s")
        row.addWidget(mins)
        row.addWidget(secs)
        lay.addLayout(row)
        buttons = QHBoxLayout()
        for text, action in self._timer_buttons():
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=action: self._run(
                tr("Countdown"), lambda px: px.countdown(mins.value(), secs.value(), a)))
            buttons.addWidget(b)
        lay.addLayout(buttons)
        return box

    def _stopwatch_box(self) -> QGroupBox:
        box = self._feature_box(p.Feature.STOPWATCH, tr("Stoppuhr"))
        buttons = QHBoxLayout(box)
        for text, action in self._timer_buttons():
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=action: self._run(tr("Stoppuhr"), lambda px: px.stopwatch(a)))
            buttons.addWidget(b)
        return box

    def _score_box(self) -> QGroupBox:
        box = self._feature_box(p.Feature.SCOREBOARD, tr("Anzeigetafel"))
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
        send = QPushButton(tr("Anzeigen"))
        send.clicked.connect(self._send_score)
        reset = QPushButton("0 : 0")
        reset.clicked.connect(lambda: (self.left.setValue(0), self.right.setValue(0), self._send_score()))
        lay.addWidget(send, 3, 0)
        lay.addWidget(reset, 3, 2)
        return box

    def _send_score(self) -> None:
        l, r = self.left.value(), self.right.value()
        self._run(tr("Stand {left}:{right}", left=l, right=r), lambda px: px.scoreboard(l, r))

    def _programs_box(self) -> QGroupBox:
        box = QGroupBox(tr("Gespeicherte Programme"))
        lay = QVBoxLayout(box)
        self.program_list = QListWidget()
        self.program_list.setIconSize(QSize(128, 32))
        self.program_list.setMinimumHeight(140)
        self.program_list.itemDoubleClicked.connect(lambda item: self._play([item.data(Qt.ItemDataRole.UserRole)]))
        lay.addWidget(self.program_list)
        row = QHBoxLayout()
        play = QPushButton(tr("Ausgewählte abspielen"))
        play.setToolTip(tr("Das Panel zeigt die angehakten Programme im Wechsel (Doppelklick: nur dieses)"))
        play.clicked.connect(lambda: self._play(self._checked()))
        self.play_one = QPushButton(tr("Nur dieses abspielen"))
        self.play_one.setToolTip(tr("Spielt nur den markierten Eintrag, ohne Wechsel"))
        self.play_one.setEnabled(False)
        self.play_one.clicked.connect(self._play_current)
        self.program_list.currentItemChanged.connect(lambda cur, _prev: self.play_one.setEnabled(cur is not None))
        delete = QPushButton(tr("Ausgewählte löschen"))
        delete.clicked.connect(self._delete_checked)
        clear = QPushButton(tr("Alle löschen …"))
        clear.clicked.connect(self._clear_all)
        for b in (self.play_one, play, delete, clear):
            row.addWidget(b)
        lay.addLayout(row)
        hint = QLabel(tr("Das Panel meldet nicht, welche Plätze belegt sind. Die Liste führt das Studio "
                         "selbst und kennt nur, was du von hier aus auf einen Platz gesendet hast."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px")
        lay.addWidget(hint)
        self.programs.changed.connect(self._refresh_programs)
        self._refresh_programs()
        return box

    def _refresh_programs(self) -> None:
        previous = {self.program_list.item(i).data(Qt.ItemDataRole.UserRole):
                    self.program_list.item(i).checkState() == Qt.CheckState.Checked
                    for i in range(self.program_list.count())}
        self.program_list.clear()
        kinds = {"image": tr("Bild"), "animation": tr("Animation"), "text": tr("Text")}
        for prog in self.programs.sorted():
            item = QListWidgetItem(tr("Platz {slot} · {kind} · {name}", slot=prog.slot,
                                      kind=kinds.get(prog.kind, prog.kind), name=prog.name))
            item.setData(Qt.ItemDataRole.UserRole, prog.slot)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # New programs start ticked; known ones keep their tick.
            item.setCheckState(Qt.CheckState.Checked if previous.get(prog.slot, True) else Qt.CheckState.Unchecked)
            if (thumb := prog.thumbnail()) is not None:
                item.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(thumb)).scaled(128, 32)))
            self.program_list.addItem(item)

    def _play_current(self) -> None:
        item = self.program_list.currentItem()
        if item is not None:
            self._play([item.data(Qt.ItemDataRole.UserRole)])

    def _checked(self) -> list[int]:
        return [self.program_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.program_list.count())
                if self.program_list.item(i).checkState() == Qt.CheckState.Checked]

    def _play(self, slots: list[int]) -> None:
        if slots:
            self._run(tr("Spiele Plätze {slots}", slots=", ".join(map(str, slots))),
                      lambda px: px.play_slots(slots))

    def _delete_checked(self) -> None:
        slots = self._checked()
        if not slots:
            return

        async def job(px) -> None:
            await px.delete_slots(slots)
            self.programs.remove(slots)

        self._run(tr("Plätze {slots} gelöscht", slots=", ".join(map(str, slots))), job)

    def _clear_all(self) -> None:
        answer = QMessageBox.question(
            self, tr("Alles löschen"), tr("Alle auf dem Panel gespeicherten Programme löschen?"))
        if answer != QMessageBox.StandardButton.Yes:
            return

        async def job(px) -> None:
            await px.clear()
            self.programs.clear()

        self._run(tr("Alles gelöscht"), job)

    def _raw_box(self) -> QGroupBox:
        box = QGroupBox(tr("Rohbefehl (für Experimente)"))
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        raw = QLineEdit()
        raw.setPlaceholderText(tr("Hex, z. B. 05 00 07 01 01"))
        result = QLabel()
        result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        send = QPushButton(tr("Senden"))

        def go() -> None:
            try:
                data = bytes.fromhex(raw.text())
            except ValueError:
                self.device.error.emit(tr("Ungültiges Hex"))
                return

            async def job(px):
                try:
                    reply = await px.request(data, timeout=2.0)
                    result.setText(tr("Antwort: {reply}", reply=reply.hex(" ")))
                except TimeoutError:
                    result.setText(tr("keine Antwort"))

            self._run(tr("Rohbefehl"), job)

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
        self.follow = QCheckBox(tr("Bereich folgt der Maus"))
        self.x, self.y, self.w = QSpinBox(), QSpinBox(), QSpinBox()
        for s in (self.x, self.y, self.w):
            s.setRange(0, 10000)
        self.w.setRange(8, 10000)
        self.w.setValue(256)
        self.fit = QComboBox()
        for key, label in (("cover", tr("Ausfüllen")), ("contain", tr("Einpassen")), ("stretch", tr("Strecken"))):
            self.fit.addItem(label, key)
        self.fps = QSpinBox()
        self.fps.setRange(1, 20)
        self.fps.setValue(8)
        self.fps.setSuffix(tr(" Bilder/s"))
        self.start = QPushButton(tr("Spiegeln starten"))
        self.start.setCheckable(True)
        self.start.toggled.connect(self._toggle)
        self.preview = QLabel()
        self.preview.setMinimumHeight(80)
        self.preview.setStyleSheet("background:#111")
        self.note = QLabel(tr(
            "Höhe ergibt sich aus dem Seitenverhältnis des Panels. Beim ersten Start fragt macOS "
            "nach der Freigabe „Bildschirmaufnahme“ für dein Terminal."))
        self.note.setWordWrap(True)

        pos = QHBoxLayout()
        for label, spin in (("x", self.x), ("y", self.y), (tr("Breite"), self.w)):
            pos.addWidget(QLabel(label))
            pos.addWidget(spin)
        form = QFormLayout()
        form.addRow(tr("Bildschirm"), self.screen)
        form.addRow(self.follow)
        form.addRow(tr("Bereich"), pos)
        form.addRow(tr("Skalierung"), self.fit)
        form.addRow(tr("Tempo"), self.fps)

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
        self.start.setText(tr("Spiegeln stoppen") if on else tr("Spiegeln starten"))
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
