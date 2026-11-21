"""iPixel Studio: desktop editor and remote for iPixel Color LED panels."""

from __future__ import annotations

import asyncio
import logging
import sys

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QToolBar,
    QWidget,
)

from .device import DeviceController
from .editor import EditorTab, pil_to_qimage
from .model import Animation
from .tabs import ControlTab, MirrorTab, TextTab

APP_NAME = "iPixel Studio"
OPEN_FILTER = "Bilder und Animationen (*.png *.gif *.jpg *.jpeg *.bmp *.webp *.apng);;Alle Dateien (*)"

ICON_ART = [
    "................",
    "..##......##....",
    ".####....####...",
    "######..######..",
    "##############..",
    "##############..",
    ".############...",
    "..##########....",
    "...########.....",
    "....######......",
    ".....####.......",
    "......##........",
]


def app_icon() -> QIcon:
    img = Image.new("RGB", (16, 16), (20, 20, 28))
    for y, row in enumerate(ICON_ART):
        for x, ch in enumerate(row):
            if ch == "#":
                img.putpixel((x + 1, y + 2), (255, 60 + 10 * y, 40))
    return QIcon(QPixmap.fromImage(pil_to_qimage(img).scaled(256, 256)))


class LogHandler(logging.Handler):
    def __init__(self, sink: QPlainTextEdit):
        super().__init__(logging.DEBUG)
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.appendPlainText(self.format(record))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.path: str | None = None
        width = int(self.settings.value("panel/width", 64))
        height = int(self.settings.value("panel/height", 16))
        self.doc = Animation(width, height)
        self.device = DeviceController(self)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)

        self.editor = EditorTab(self.doc, self.device)
        self.text_tab = TextTab(self.device)
        self.control_tab = ControlTab(self.device)
        self.mirror_tab = MirrorTab(self.device)
        for tab in (self.text_tab, self.mirror_tab):
            tab.set_panel_size(width, height)
        tabs = QTabWidget()
        tabs.addTab(self.editor, "Bild && Animation")
        tabs.addTab(self.text_tab, "Text")
        tabs.addTab(self.control_tab, "Steuerung")
        tabs.addTab(self.mirror_tab, "Bildschirm spiegeln")
        self.setCentralWidget(tabs)

        self._build_toolbar()
        self._build_menus()
        self._build_log()

        self.size_label = QLabel(f"{width}×{height}")
        self.coord_label = QLabel()
        self.statusBar().addPermanentWidget(self.coord_label)
        self.statusBar().addPermanentWidget(self.size_label)
        self.editor.status.connect(self.coord_label.setText)

        d = self.device
        d.stateChanged.connect(self._state_changed)
        d.infoChanged.connect(self._info_changed)
        d.devicesFound.connect(self._devices_found)
        d.message.connect(lambda m: self.statusBar().showMessage(m, 6000))
        d.message.connect(lambda m: logging.getLogger("ipixel.studio").info(m))
        d.error.connect(self._error)
        d.busyChanged.connect(self.busy.setVisible)
        d.passwordNeeded.connect(self._ask_password)
        self.doc.subscribe(self._update_title)
        self._state_changed(d.state)
        self._update_title()
        if geo := self.settings.value("window/geometry"):
            self.restoreGeometry(geo)

    # UI construction

    def _build_toolbar(self) -> None:
        tb = QToolBar("Verbindung")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.setMinimumWidth(260)
        self.target.lineEdit().setPlaceholderText("MAC, Adresse oder LED_BLE_…")
        last = self.settings.value("device/last", "")
        if last:
            self.target.addItem(last)
        tb.addWidget(QLabel(" Panel "))
        tb.addWidget(self.target)
        self.scan_btn = QPushButton("Suchen")
        self.scan_btn.clicked.connect(self.device.scan)
        tb.addWidget(self.scan_btn)
        self.connect_btn = QPushButton("Verbinden")
        self.connect_btn.clicked.connect(self._toggle_connection)
        tb.addWidget(self.connect_btn)
        self.state_label = QLabel()
        tb.addWidget(self.state_label)
        tb.addSeparator()
        tb.addWidget(QLabel(" Helligkeit "))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(1, 100)
        self.brightness.setValue(int(self.settings.value("panel/brightness", 50)))
        self.brightness.setFixedWidth(160)
        self.brightness.sliderReleased.connect(self._send_brightness)
        tb.addWidget(self.brightness)
        self.brightness_label = QLabel(f"{self.brightness.value()} %")
        self.brightness.valueChanged.connect(lambda v: self.brightness_label.setText(f"{v} %"))
        tb.addWidget(self.brightness_label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(90)
        self.busy.setVisible(False)
        tb.addWidget(self.busy)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        file = mb.addMenu("Datei")
        self._action(file, "Neu", QKeySequence.StandardKey.New, self.new_doc)
        self._action(file, "Öffnen …", QKeySequence.StandardKey.Open, self.open_doc)
        self._action(file, "Bild ins aktuelle Bild einfügen …", "Ctrl+Shift+I", self.import_into_frame)
        file.addSeparator()
        self._action(file, "Speichern", QKeySequence.StandardKey.Save, self.save_doc)
        self._action(file, "Speichern unter …", QKeySequence.StandardKey.SaveAs, lambda: self.save_doc(True))
        self._action(file, "Als GIF exportieren …", "Ctrl+E", self.export_gif)

        edit = mb.addMenu("Bearbeiten")
        self.undo_act = self._action(edit, "Rückgängig", QKeySequence.StandardKey.Undo, self.doc.undo)
        self.redo_act = self._action(edit, "Wiederholen", QKeySequence.StandardKey.Redo, self.doc.redo)
        edit.addSeparator()
        self._action(edit, "Bild leeren", "Delete", lambda: self.doc.clear(self.editor.canvas.background))
        self._action(edit, "Alle Bilder leeren", None, lambda: self.doc.clear(self.editor.canvas.background, True))
        edit.addSeparator()
        for label, key, args in [("Nach links schieben", "Ctrl+Left", (-1, 0)),
                                 ("Nach rechts schieben", "Ctrl+Right", (1, 0)),
                                 ("Nach oben schieben", "Ctrl+Up", (0, -1)),
                                 ("Nach unten schieben", "Ctrl+Down", (0, 1))]:
            self._action(edit, label, key, lambda a=args: self.doc.shift(*a))
        edit.addSeparator()
        self._action(edit, "Waagerecht spiegeln", "Ctrl+H", lambda: self.doc.flip(True))
        self._action(edit, "Senkrecht spiegeln", "Ctrl+Shift+H", lambda: self.doc.flip(False))
        edit.addSeparator()
        self._action(edit, "Panelgröße ändern …", None, self.ask_size)

        frames = mb.addMenu("Bilder")
        self._action(frames, "Nächstes Bild", ".", lambda: self.doc.select(self.doc.index + 1))
        self._action(frames, "Vorheriges Bild", ",", lambda: self.doc.select(self.doc.index - 1))
        self._action(frames, "Bild duplizieren", "Ctrl+D", lambda: self.doc.add_frame(True))
        self._action(frames, "Leeres Bild einfügen", "Ctrl+Shift+N", lambda: self.doc.add_frame(False))
        self._action(frames, "Bild löschen", "Ctrl+Backspace", self.doc.delete_frame)

        panel = mb.addMenu("Panel")
        self._action(panel, "Aktuelles Bild senden", "Ctrl+Return", self.editor.send_current)
        self._action(panel, "Animation senden", "Ctrl+Shift+Return", self.editor.send_animation)
        self._action(panel, "Live-Modus umschalten", "Ctrl+L",
                     lambda: self.editor.live_cb.setChecked(not self.editor.live_cb.isChecked()))

    def _action(self, menu, text, key, slot) -> QAction:
        act = QAction(text, self)
        if key is not None:
            act.setShortcut(QKeySequence(key))
        act.triggered.connect(lambda _=False: slot())
        menu.addAction(act)
        return act

    def _build_log(self) -> None:
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setMaximumBlockCount(2000)
        log.setStyleSheet("font-family: Menlo, monospace; font-size: 11px")
        dock = QDockWidget("Protokoll", self)
        dock.setObjectName("log")
        dock.setWidget(log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.hide()
        self.menuBar().addMenu("Ansicht").addAction(dock.toggleViewAction())
        handler = LogHandler(log)
        logger = logging.getLogger("ipixel")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    # Device

    def _toggle_connection(self) -> None:
        if self.device.state == DeviceController.DISCONNECTED:
            target = self._target()
            if not target:
                self._error("Erst ein Panel suchen oder MAC eintragen")
                return
            self.settings.setValue("device/last", target)
            self.device.connect_to(target, self.settings.value(f"password/{target}") or None)
        else:
            self.device.disconnect()

    def _target(self) -> str:
        data = self.target.currentData()
        return (data or self.target.currentText()).strip()

    def _devices_found(self, devices: list) -> None:
        current = self.target.currentText()
        self.target.clear()
        for address, name in devices:
            self.target.addItem(f"{name}  ({address})", address)
        if not devices and current:
            self.target.setEditText(current)

    def _state_changed(self, state: str) -> None:
        connected = state == DeviceController.CONNECTED
        self.state_label.setText(f"  {state}  ")
        self.connect_btn.setText("Trennen" if state != DeviceController.DISCONNECTED else "Verbinden")
        self.connect_btn.setEnabled(state != DeviceController.CONNECTING)
        self.target.setEnabled(state == DeviceController.DISCONNECTED)
        self.scan_btn.setEnabled(state == DeviceController.DISCONNECTED)
        self.brightness.setEnabled(connected)

    def _info_changed(self, info) -> None:
        if info.width is None:
            self._error(f"Unbekannter Paneltyp 0x{info.type_byte:02x}; Größe bitte über Bearbeiten einstellen")
            return
        self._apply_size(info.width, info.height)

    def _apply_size(self, width: int, height: int) -> None:
        self.doc.resize_canvas(width, height)
        self.settings.setValue("panel/width", width)
        self.settings.setValue("panel/height", height)
        self.text_tab.set_panel_size(width, height)
        self.mirror_tab.set_panel_size(width, height)
        self.size_label.setText(f"{width}×{height}")
        self.editor.timeline.refresh()

    def _send_brightness(self) -> None:
        v = self.brightness.value()
        self.settings.setValue("panel/brightness", v)
        self.device.run(f"Helligkeit {v} %", lambda px: px.brightness(v), keep_live=True)

    def _ask_password(self, target: str) -> None:
        pw, ok = QInputDialog.getText(self, "Passwort", "Das Panel ist geschützt. Passwort (4 oder 6 Ziffern):",
                                      QLineEdit.EchoMode.Password)
        if ok and pw:
            self.settings.setValue(f"password/{target}", pw)
            self.device.connect_to(target, pw)

    def _error(self, text: str) -> None:
        self.statusBar().showMessage(text, 10000)
        logging.getLogger("ipixel.studio").warning(text)

    # Documents

    def _update_title(self) -> None:
        name = self.path.rsplit("/", 1)[-1] if self.path else "Unbenannt"
        n = len(self.doc.frames)
        self.setWindowTitle(f"{name} – {n} Bild{'er' if n != 1 else ''} – {APP_NAME}")
        self.undo_act.setEnabled(self.doc.can_undo)
        self.redo_act.setEnabled(self.doc.can_redo)

    def new_doc(self) -> None:
        self.doc.checkpoint()
        self.doc.frames = Animation(self.doc.width, self.doc.height).frames
        self.doc.index = 0
        self.path = None
        self.doc.changed()

    def open_doc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Öffnen", self._dir(), OPEN_FILTER)
        if not path:
            return
        fit, ok = QInputDialog.getItem(self, "Skalierung", "Wie soll das Bild aufs Panel passen?",
                                       ["Einpassen", "Ausfüllen (beschneiden)", "Strecken"], 0, False)
        if not ok:
            return
        mode = {"Einpassen": "contain", "Strecken": "stretch"}.get(fit, "cover")
        try:
            self.doc.load(path, mode)
        except OSError as e:
            QMessageBox.warning(self, "Öffnen", f"Datei nicht lesbar:\n{e}")
            return
        self.path = path if path.lower().endswith(".png") else None
        self._remember_dir(path)
        self._update_title()

    def import_into_frame(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Bild einfügen", self._dir(), OPEN_FILTER)
        if path:
            self.doc.paste_image(Image.open(path), "contain")
            self._remember_dir(path)

    def save_doc(self, ask: bool = False) -> None:
        path = self.path
        if ask or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Speichern", self._dir("animation.png"),
                                                  "PNG/APNG (verlustfrei) (*.png)")
            if not path:
                return
            if not path.lower().endswith(".png"):
                path += ".png"
        self.doc.save(path)
        self.path = path
        self._remember_dir(path)
        self._update_title()
        self.statusBar().showMessage(f"Gespeichert: {path}", 5000)

    def export_gif(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Als GIF exportieren", self._dir("animation.gif"), "GIF (*.gif)")
        if path:
            self.doc.save(path if path.lower().endswith(".gif") else path + ".gif")
            self._remember_dir(path)

    def ask_size(self) -> None:
        text, ok = QInputDialog.getText(self, "Panelgröße", "Breite × Höhe:",
                                        text=f"{self.doc.width}x{self.doc.height}")
        if not ok:
            return
        try:
            w, h = (int(v) for v in text.lower().replace("×", "x").split("x"))
        except ValueError:
            return
        if 1 <= w <= 1024 and 1 <= h <= 256:
            self._apply_size(w, h)

    def _dir(self, name: str = "") -> str:
        base = self.settings.value("files/dir", "")
        return f"{base}/{name}" if base and name else base or name

    def _remember_dir(self, path: str) -> None:
        self.settings.setValue("files/dir", path.rsplit("/", 1)[0])

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        if self.device.state != DeviceController.DISCONNECTED:
            self.device.disconnect()
        event.accept()


def main() -> None:
    import qasync

    logging.basicConfig(level=logging.WARNING)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SAUER Networks")
    app.setOrganizationDomain("sauernetworks.de")
    app.setWindowIcon(app_icon())
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    closing = asyncio.Event()
    app.aboutToQuit.connect(closing.set)
    win = MainWindow()
    win.show()
    with loop:
        loop.run_until_complete(closing.wait())
        loop.run_until_complete(asyncio.sleep(0.3))


if __name__ == "__main__":
    main()
