"""Pixel editor tab: canvas, tools, palette, frame timeline and send controls."""

from __future__ import annotations

from enum import Enum

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import protocol as p
from .device import DeviceController
from .i18n import tr
from .model import Animation, Box, Color, Point

PALETTE: list[Color] = [
    (0, 0, 0), (255, 255, 255), (128, 128, 128), (64, 64, 64),
    (255, 0, 0), (255, 128, 0), (255, 255, 0), (128, 255, 0),
    (0, 255, 0), (0, 255, 128), (0, 255, 255), (0, 128, 255),
    (0, 0, 255), (128, 0, 255), (255, 0, 255), (255, 0, 128),
]


class Tool(Enum):
    SELECT = ("Auswahl", "M")
    PEN = ("Stift", "P")
    ERASER = ("Radierer", "E")
    LINE = ("Linie", "L")
    RECT = ("Rechteck", "R")
    RECT_FILL = ("Rechteck gefüllt", "Shift+R")
    ELLIPSE = ("Ellipse", "O")
    ELLIPSE_FILL = ("Ellipse gefüllt", "Shift+O")
    FILL = ("Füllen", "F")
    PICKER = ("Pipette", "I")

    @property
    def label(self) -> str:
        return tr(self.value[0])

    @property
    def shortcut(self) -> str:
        return self.value[1]


SHAPES = {Tool.LINE, Tool.RECT, Tool.RECT_FILL, Tool.ELLIPSE, Tool.ELLIPSE_FILL}


def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    return QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()


def color_icon(color: Color, size: int = 18) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(*color))
    return QIcon(pm)


class PixelCanvas(QWidget):
    hovered = Signal(object)
    colorPicked = Signal(tuple)
    nudge = Signal(int, int)
    selectionChanged = Signal()

    def __init__(self, doc: Animation, parent: QWidget | None = None):
        super().__init__(parent)
        self.doc = doc
        self.tool = Tool.PEN
        self.color: Color = (255, 255, 255)
        self.background: Color = (0, 0, 0)
        self.pen_size = 1
        self.grid = True
        self.onion = False
        self.selection: Box | None = None
        self._last: Point | None = None
        self._start: Point | None = None
        self._base: Image.Image | None = None
        self._floating: tuple[Image.Image, Box] | None = None
        self._paint_color: Color = self.color
        self.setMouseTracking(True)
        self.setMinimumSize(320, 120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def cell(self) -> float:
        return max(1.0, min(self.width() / self.doc.width, self.height() / self.doc.height))

    def origin(self) -> tuple[float, float]:
        c = self.cell()
        return (self.width() - c * self.doc.width) / 2, (self.height() - c * self.doc.height) / 2

    def to_pixel(self, pos) -> Point:
        c = self.cell()
        ox, oy = self.origin()
        return int((pos.x() - ox) // c), int((pos.y() - oy) // c)

    def paintEvent(self, _event) -> None:
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(30, 30, 30))
        c = self.cell()
        ox, oy = self.origin()
        w, h = self.doc.width * c, self.doc.height * c
        target = self.rect().adjusted(int(ox), int(oy), 0, 0)
        target.setSize(QSize(int(w), int(h)))
        if self.onion and self.doc.index > 0:
            qp.drawImage(target, pil_to_qimage(self.doc.frames[self.doc.index - 1].image))
            qp.setOpacity(0.65)
        qp.drawImage(target, pil_to_qimage(self.doc.image))
        qp.setOpacity(1.0)
        if self.grid and c >= 6:
            qp.setPen(QPen(QColor(70, 70, 70), 1))
            for x in range(self.doc.width + 1):
                qp.drawLine(int(ox + x * c), int(oy), int(ox + x * c), int(oy + h))
            for y in range(self.doc.height + 1):
                qp.drawLine(int(ox), int(oy + y * c), int(ox + w), int(oy + y * c))
        if self.selection:
            x0, y0, x1, y1 = self.selection
            box = QRectF(ox + x0 * c, oy + y0 * c, (x1 - x0 + 1) * c, (y1 - y0 + 1) * c)
            qp.setPen(QPen(QColor(0, 0, 0), 2))
            qp.drawRect(box)
            qp.setPen(QPen(QColor(255, 255, 255), 2, Qt.PenStyle.DashLine))
            qp.drawRect(box)

    def set_selection(self, box: Box | None) -> None:
        self.selection = self.doc.clip_box(box) if box else None
        self.update()
        self.selectionChanged.emit()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        mods = e.modifiers()
        step = 4 if mods & Qt.KeyboardModifier.ShiftModifier else 1
        other = mods & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        arrows = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                  Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if e.key() in arrows and other == Qt.KeyboardModifier.NoModifier:
            self.nudge.emit(*arrows[e.key()])
        elif e.key() == Qt.Key.Key_Escape and self.selection:
            self.set_selection(None)
        else:
            super().keyPressEvent(e)

    def _select_press(self, pt: Point, right: bool) -> None:
        sel = self.selection
        if right:
            self.set_selection(None)
            return
        if sel and sel[0] <= pt[0] <= sel[2] and sel[1] <= pt[1] <= sel[3]:
            # Lift the selected pixels so they can be dragged.
            self.doc.checkpoint()
            self._floating = (self.doc.crop(sel), sel)
            self.doc.image.paste(self.background, (sel[0], sel[1], sel[2] + 1, sel[3] + 1))
            self._base = self.doc.image.copy()
        self._start = self._last = pt
        if not self._floating:
            self.set_selection((pt[0], pt[1], pt[0], pt[1]))

    def _select_move(self, pt: Point) -> None:
        assert self._start is not None
        if self._floating:
            piece, (x0, y0, x1, y1) = self._floating
            dx, dy = pt[0] - self._start[0], pt[1] - self._start[1]
            self.doc.image.paste(self._base)
            self.doc.stamp(piece, x0 + dx, y0 + dy)
            self.selection = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self.update()
        else:
            self.set_selection((self._start[0], self._start[1], pt[0], pt[1]))

    def _select_release(self, pt: Point | None) -> None:
        if self._floating:
            piece, origin = self._floating
            if self.selection == origin:
                self.doc.stamp(piece, origin[0], origin[1])
            self.set_selection(self.selection)
        elif pt == self._start:
            self.set_selection(None)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        pt = self.to_pixel(e.position())
        right = e.button() == Qt.MouseButton.RightButton
        if self.tool is Tool.SELECT:
            self._select_press(pt, right)
            return
        self._paint_color = self.background if (right or self.tool is Tool.ERASER) else self.color
        if self.tool is Tool.PICKER:
            if self.doc.in_bounds(pt):
                self.colorPicked.emit(self.doc.pixel(pt))
            return
        self.doc.checkpoint()
        if self.tool is Tool.FILL:
            self.doc.flood_fill(pt, self._paint_color)
            return
        self._start = self._last = pt
        if self.tool in SHAPES:
            self._base = self.doc.image.copy()
            self._draw_shape(pt)
        else:
            self.doc.plot([pt], self._paint_color, self.pen_size)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pt = self.to_pixel(e.position())
        self.hovered.emit(pt if self.doc.in_bounds(pt) else None)
        if self._start is None or pt == self._last:
            return
        if self.tool is Tool.SELECT:
            self._select_move(pt)
        elif self.tool in SHAPES:
            self._draw_shape(pt)
        else:
            self.doc.line(self._last, pt, self._paint_color, self.pen_size)
        self._last = pt

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self.tool is Tool.SELECT and self._start is not None:
            self._select_release(self.to_pixel(e.position()))
        self._start = self._last = None
        self._base = None
        self._floating = None

    def leaveEvent(self, _e) -> None:
        self.hovered.emit(None)

    def _draw_shape(self, pt: Point) -> None:
        assert self._base is not None and self._start is not None
        self.doc.image.paste(self._base)
        col = self._paint_color
        match self.tool:
            case Tool.LINE:
                self.doc.line(self._start, pt, col, self.pen_size)
            case Tool.RECT | Tool.RECT_FILL:
                self.doc.rect(self._start, pt, col, fill=self.tool is Tool.RECT_FILL)
            case Tool.ELLIPSE | Tool.ELLIPSE_FILL:
                self.doc.ellipse(self._start, pt, col, fill=self.tool is Tool.ELLIPSE_FILL)


class Timeline(QWidget):
    def __init__(self, doc: Animation, parent: QWidget | None = None):
        super().__init__(parent)
        self.doc = doc
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.currentRowChanged.connect(self._row_changed)
        self._updating = False

        self.duration = QSpinBox()
        self.duration.setRange(10, 10000)
        self.duration.setSingleStep(10)
        self.duration.setSuffix(" ms")
        self.duration.editingFinished.connect(lambda: self.doc.set_duration(self.duration.value()))
        all_btn = QPushButton(tr("für alle"))
        all_btn.setToolTip(tr("Diese Dauer für alle Bilder übernehmen"))
        all_btn.clicked.connect(lambda: self.doc.set_duration(self.duration.value(), all_frames=True))

        self.play = QPushButton(tr("▶ Abspielen"))
        self.play.setCheckable(True)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.play.toggled.connect(self._toggle_play)

        buttons = QHBoxLayout()
        for text, tip, slot in [
            (tr("+ Kopie"), tr("Aktuelles Bild duplizieren"), lambda: self.doc.add_frame(True)),
            (tr("+ Leer"), tr("Leeres Bild einfügen"), lambda: self.doc.add_frame(False)),
            (tr("Löschen"), tr("Aktuelles Bild löschen"), self.doc.delete_frame),
            ("◀", tr("Bild nach links verschieben"), lambda: self.doc.move_frame(-1)),
            ("▶", tr("Bild nach rechts verschieben"), lambda: self.doc.move_frame(1)),
        ]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addSpacing(16)
        buttons.addWidget(QLabel(tr("Dauer")))
        buttons.addWidget(self.duration)
        buttons.addWidget(all_btn)
        buttons.addStretch()
        buttons.addWidget(self.play)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(buttons)
        lay.addWidget(self.list)
        self.refresh()

    def refresh(self) -> None:
        self._updating = True
        scale = max(1, min(4, 128 // self.doc.width, 64 // self.doc.height))
        size = QSize(self.doc.width * scale, self.doc.height * scale)
        self.list.setIconSize(size)
        self.list.setFixedHeight(size.height() + 52)
        if self.list.count() != len(self.doc.frames):
            self.list.clear()
            for i in range(len(self.doc.frames)):
                self.list.addItem(QListWidgetItem(str(i + 1)))
        for i, f in enumerate(self.doc.frames):
            pm = QPixmap.fromImage(pil_to_qimage(f.image).scaled(size))
            item = self.list.item(i)
            item.setIcon(QIcon(pm))
            item.setText(f"{i + 1} · {f.duration} ms")
        self.list.setCurrentRow(self.doc.index)
        self.duration.setValue(self.doc.frame.duration)
        self._updating = False

    def refresh_current(self) -> None:
        if self.list.count() != len(self.doc.frames):
            self.refresh()
            return
        item = self.list.item(self.doc.index)
        size = self.list.iconSize()
        item.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(self.doc.image).scaled(size))))
        item.setText(f"{self.doc.index + 1} · {self.doc.frame.duration} ms")
        if not self.duration.hasFocus():
            self.duration.setValue(self.doc.frame.duration)

    def _row_changed(self, row: int) -> None:
        if not self._updating and row >= 0 and row != self.doc.index:
            self.doc.select(row)

    def _toggle_play(self, on: bool) -> None:
        self.play.setText(tr("■ Stopp") if on else tr("▶ Abspielen"))
        if on:
            self.timer.start(self.doc.frame.duration)
        else:
            self.timer.stop()

    def _tick(self) -> None:
        self.doc.select((self.doc.index + 1) % len(self.doc.frames))
        if self.play.isChecked():
            self.timer.start(self.doc.frame.duration)


class EditorTab(QWidget):
    status = Signal(str)

    def __init__(self, doc: Animation, device: DeviceController, parent: QWidget | None = None):
        super().__init__(parent)
        self.doc = doc
        self.device = device
        self.canvas = PixelCanvas(doc)
        self.timeline = Timeline(doc)
        self._frame_count = len(doc.frames)
        self._last_index = doc.index

        tools = QVBoxLayout()
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.tool_buttons: dict[Tool, QToolButton] = {}
        for tool in Tool:
            b = QToolButton()
            b.setText(tool.label)
            b.setCheckable(True)
            b.setToolTip(f"{tool.label} ({tool.shortcut})")
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setMinimumWidth(130)
            b.clicked.connect(lambda _=False, t=tool: self.set_tool(t))
            group.addButton(b)
            tools.addWidget(b)
            self.tool_buttons[tool] = b
            act = QAction(self)
            act.setShortcut(QKeySequence(tool.shortcut))
            act.triggered.connect(lambda _=False, t=tool: self.set_tool(t))
            self.addAction(act)
        self.tool_buttons[Tool.PEN].setChecked(True)

        self.pen_size = QSpinBox()
        self.pen_size.setRange(1, 8)
        self.pen_size.setPrefix(tr("Stärke "))
        self.pen_size.valueChanged.connect(lambda v: setattr(self.canvas, "pen_size", v))
        tools.addWidget(self.pen_size)
        grid_cb = QCheckBox(tr("Raster"))
        grid_cb.setChecked(True)
        grid_cb.toggled.connect(self._set_grid)
        onion_cb = QCheckBox(tr("Zwiebelhaut"))
        onion_cb.setToolTip(tr("Vorheriges Bild durchscheinen lassen"))
        onion_cb.toggled.connect(self._set_onion)
        tools.addWidget(grid_cb)
        tools.addWidget(onion_cb)
        tools.addWidget(self._move_box())
        tools.addStretch()
        self._clipboard: Image.Image | None = None
        self._setting_clipboard = False
        QGuiApplication.clipboard().dataChanged.connect(self._system_clipboard_changed)

        right = QVBoxLayout()
        right.addWidget(self._color_box())
        right.addWidget(self._send_box())
        right.addStretch()

        top = QHBoxLayout()
        top.addLayout(tools)
        top.addWidget(self.canvas, 1)
        top.addLayout(right)

        lay = QVBoxLayout(self)
        lay.addLayout(top, 1)
        lay.addWidget(self.timeline)

        self.canvas.colorPicked.connect(self._picked)
        self.canvas.nudge.connect(self.nudge)
        self.canvas.selectionChanged.connect(self._update_move_hint)
        self.canvas.hovered.connect(lambda pt: self.status.emit(f"x {pt[0]}  y {pt[1]}" if pt else ""))
        doc.subscribe(self._doc_changed)
        device.liveChanged.connect(self._live_changed)
        device.stateChanged.connect(lambda _s: self._update_send_enabled())
        self._update_send_enabled()

    # Panels

    def _move_box(self) -> QGroupBox:
        box = QGroupBox(tr("Verschieben"))
        lay = QVBoxLayout(box)
        pad = QGridLayout()
        pad.setSpacing(2)
        for text, (dx, dy), (row, col), tip in [
            ("↑", (0, -1), (0, 1), tr("Nach oben (Pfeiltaste, mit Umschalt 4 Pixel)")),
            ("←", (-1, 0), (1, 0), tr("Nach links (Pfeiltaste, mit Umschalt 4 Pixel)")),
            ("→", (1, 0), (1, 2), tr("Nach rechts (Pfeiltaste, mit Umschalt 4 Pixel)")),
            ("↓", (0, 1), (2, 1), tr("Nach unten (Pfeiltaste, mit Umschalt 4 Pixel)")),
        ]:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedSize(34, 30)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, d=(dx, dy): self.nudge(*d))
            pad.addWidget(b, row, col)
        lay.addLayout(pad)
        self.move_new_frame = QCheckBox(tr("als neues Bild"))
        self.move_new_frame.setToolTip(tr("Jeder Schritt hängt eine verschobene Kopie als neues Bild an"))
        self.move_all = QCheckBox(tr("alle Bilder"))
        self.move_all.setToolTip(tr("Alle Bilder der Animation gemeinsam verschieben"))
        self.move_new_frame.toggled.connect(lambda on: self.move_all.setEnabled(not on))
        lay.addWidget(self.move_new_frame)
        lay.addWidget(self.move_all)
        self.move_hint = QLabel()
        self.move_hint.setWordWrap(True)
        self.move_hint.setStyleSheet("color: gray; font-size: 11px")
        lay.addWidget(self.move_hint)
        self._update_move_hint()
        return box

    def _update_move_hint(self) -> None:
        self.move_hint.setText(tr("verschiebt die Auswahl") if self.canvas.selection else tr("verschiebt das ganze Bild"))

    # Selection, clipboard, moving

    def nudge(self, dx: int, dy: int) -> None:
        """Move the selection, or the whole frame (wrapping around) when nothing is selected."""
        if self.move_new_frame.isChecked():
            self.doc.add_frame(duplicate=True)
        all_frames = self.move_all.isChecked() and not self.move_new_frame.isChecked()
        sel = self.canvas.selection
        if sel:
            moved = self.doc.move_box(sel, dx, dy, self.canvas.background, all_frames=all_frames)
            self.canvas.set_selection(moved)
        else:
            self.doc.shift(dx, dy, all_frames=all_frames)

    def select_all(self) -> None:
        self.set_tool(Tool.SELECT)
        self.canvas.set_selection((0, 0, self.doc.width - 1, self.doc.height - 1))

    def select_none(self) -> None:
        self.canvas.set_selection(None)

    def copy(self) -> None:
        piece = self.doc.crop(self.canvas.selection)
        self._setting_clipboard = True
        QGuiApplication.clipboard().setImage(pil_to_qimage(piece))
        self._setting_clipboard = False
        self._clipboard = piece

    def _system_clipboard_changed(self) -> None:
        if not self._setting_clipboard:
            self._clipboard = None

    def cut(self) -> None:
        self.copy()
        self.delete()

    def paste(self) -> None:
        """Paste at the selection's corner (or top-left) and select the pasted pixels for dragging."""
        piece = self._clipboard_image()
        if piece is None:
            return
        x, y = self.canvas.selection[:2] if self.canvas.selection else (0, 0)
        self.doc.checkpoint()
        self.doc.stamp(piece, x, y)
        self.set_tool(Tool.SELECT)
        self.canvas.set_selection((x, y, x + piece.width - 1, y + piece.height - 1))

    def delete(self) -> None:
        sel = self.canvas.selection
        if sel:
            self.doc.checkpoint()
            self.doc.fill_box(sel, self.canvas.background)
        else:
            self.doc.clear(self.canvas.background)

    def _clipboard_image(self) -> Image.Image | None:
        if self._clipboard is not None:
            return self._clipboard
        qimg = QGuiApplication.clipboard().image()
        if not qimg.isNull():
            # Copied in another program: take it, shrunk to the panel if needed.
            rgb = qimg.convertToFormat(QImage.Format.Format_RGB888)
            data = bytes(rgb.constBits())[: rgb.bytesPerLine() * rgb.height()]
            img = Image.frombuffer("RGB", (rgb.width(), rgb.height()), data, "raw", "RGB", rgb.bytesPerLine(), 1)
            if img.width > self.doc.width or img.height > self.doc.height:
                from .. import render

                img = render.fit(img, min(img.width, self.doc.width), min(img.height, self.doc.height))
            return img
        return None

    def _color_box(self) -> QGroupBox:
        box = QGroupBox(tr("Farbe"))
        lay = QVBoxLayout(box)
        self.fg_btn = QPushButton(tr("Vordergrund"))
        self.bg_btn = QPushButton(tr("Hintergrund / Rechtsklick"))
        self.fg_btn.clicked.connect(lambda: self._choose_color(True))
        self.bg_btn.clicked.connect(lambda: self._choose_color(False))
        lay.addWidget(self.fg_btn)
        lay.addWidget(self.bg_btn)
        grid = QGridLayout()
        grid.setSpacing(2)
        for i, col in enumerate(PALETTE):
            b = QToolButton()
            b.setIcon(color_icon(col))
            b.setToolTip(tr("{hex}  (Rechtsklick: Hintergrund)", hex="#%02x%02x%02x" % col))
            b.setFixedSize(26, 26)
            b.clicked.connect(lambda _=False, c=col: self.set_color(c))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, c=col: self.set_color(c, foreground=False))
            grid.addWidget(b, i // 4, i % 4)
        lay.addLayout(grid)
        self.set_color(self.canvas.color)
        self.set_color(self.canvas.background, foreground=False)
        return box

    def _send_box(self) -> QGroupBox:
        box = QGroupBox(tr("Ans Panel"))
        lay = QVBoxLayout(box)
        self.slot = QSpinBox()
        self.slot.setRange(0, 99)
        self.slot.setSpecialValueText(tr("nur anzeigen"))
        self.slot.setPrefix(tr("Platz "))
        self.slot.setToolTip(tr("0 = nur anzeigen, 1–99 = als Programm speichern"))
        self.send_image = QPushButton(tr("Bild senden"))
        self.send_image.setToolTip(tr("Aktuelles Bild als PNG senden"))
        self.send_image.clicked.connect(self.send_current)
        self.send_anim = QPushButton(tr("Animation senden"))
        self.send_anim.setToolTip(tr("Alle Bilder als GIF senden"))
        self.send_anim.clicked.connect(self.send_animation)
        self.live_cb = QCheckBox(tr("Live: jede Änderung sofort zeigen"))
        self.live_cb.toggled.connect(self._toggle_live)
        lay.addWidget(self.slot)
        lay.addWidget(self.send_image)
        lay.addWidget(self.send_anim)
        lay.addSpacing(8)
        lay.addWidget(self.live_cb)
        return box

    # Actions

    def set_tool(self, tool: Tool) -> None:
        self.canvas.tool = tool
        self.tool_buttons[tool].setChecked(True)

    def set_color(self, color: Color, foreground: bool = True) -> None:
        if foreground:
            self.canvas.color = color
            self.fg_btn.setIcon(color_icon(color))
        else:
            self.canvas.background = color
            self.bg_btn.setIcon(color_icon(color))

    def _choose_color(self, foreground: bool) -> None:
        current = self.canvas.color if foreground else self.canvas.background
        c = QColorDialog.getColor(QColor(*current), self, tr("Farbe wählen"))
        if c.isValid():
            self.set_color((c.red(), c.green(), c.blue()), foreground)

    def _picked(self, color: Color) -> None:
        self.set_color(color)
        self.set_tool(Tool.PEN)

    def _set_grid(self, on: bool) -> None:
        self.canvas.grid = on
        self.canvas.update()

    def _set_onion(self, on: bool) -> None:
        self.canvas.onion = on
        self.canvas.update()

    def _slot(self) -> int:
        return self.slot.value() or p.SLOT_TEMPORARY

    def send_current(self) -> None:
        data, slot = self.doc.png_bytes(), self._slot()
        self.device.run(tr("Bild gesendet"), lambda px: px.upload(p.Upload.PNG, data, slot))

    def send_animation(self) -> None:
        if not self.doc.is_animated:
            self.send_current()
            return
        data, slot = self.doc.gif_bytes(), self._slot()
        n = len(self.doc.frames)
        self.device.run(tr("Animation ({count} Bilder) gesendet", count=n), lambda px: px.upload(p.Upload.GIF, data, slot))

    def _toggle_live(self, on: bool) -> None:
        self.device.set_live(on)
        if on:
            self.device.push_live(self.doc.rgb_bytes())

    def _live_changed(self, on: bool) -> None:
        self.live_cb.blockSignals(True)
        self.live_cb.setChecked(on)
        self.live_cb.blockSignals(False)

    def _update_send_enabled(self) -> None:
        ok = self.device.connected
        for w in (self.send_image, self.send_anim, self.live_cb):
            w.setEnabled(ok)

    def _doc_changed(self) -> None:
        self.canvas.update()
        self._update_move_hint()
        if len(self.doc.frames) != self._frame_count or self.doc.index != self._last_index:
            self._frame_count, self._last_index = len(self.doc.frames), self.doc.index
            self.timeline.refresh()
        else:
            self.timeline.refresh_current()
        if self.device.live:
            self.device.push_live(self.doc.rgb_bytes())
