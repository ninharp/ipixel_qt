import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ipixel.studio.app import MainWindow  # noqa: E402
from ipixel.studio.editor import Tool  # noqa: E402


@pytest.fixture
def window(tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("ipixel-tests")
    app.setApplicationName("studio-test")
    win = MainWindow()
    win.resize(1100, 700)
    win.show()
    app.processEvents()
    yield win
    win.close()


def _cell_center(canvas, x, y):
    c = canvas.cell()
    ox, oy = canvas.origin()
    return QPoint(int(ox + (x + 0.5) * c), int(oy + (y + 0.5) * c))


def test_pen_draws_on_canvas_and_undo(window):
    canvas = window.editor.canvas
    window.editor.set_color((255, 0, 0))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_cell_center(canvas, 3, 2))
    assert window.doc.pixel((3, 2)) == (255, 0, 0)
    window.undo_act.trigger()
    assert window.doc.pixel((3, 2)) == (0, 0, 0)


def test_fill_tool_and_frames_timeline(window):
    canvas = window.editor.canvas
    window.editor.set_tool(Tool.FILL)
    window.editor.set_color((0, 0, 255))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_cell_center(canvas, 0, 0))
    assert window.doc.pixel((63, 15)) == (0, 0, 255)
    window.doc.add_frame(False)
    assert window.editor.timeline.list.count() == 2
    assert window.editor.timeline.list.currentRow() == 1


def test_send_buttons_disabled_while_disconnected(window):
    assert not window.editor.send_image.isEnabled()
    assert not window.control_tab.isEnabled()


def test_send_text_queues_upload(window, monkeypatch):
    jobs = []
    monkeypatch.setattr(window.device, "run", lambda label, job, keep_live=False: jobs.append(label))
    window.text_tab.text.setText("Hallo")
    window.text_tab.send()
    assert jobs == ["Text „Hallo“ gesendet"]


def test_unsupported_features_are_greyed_out(window):
    from ipixel import protocol as p

    adv = p.Advert("0007", "04", 0x83)
    window.control_tab.apply_features(p.supported_features(3, adv))
    tab = window.control_tab
    box, _ = tab.feature_boxes[p.Feature.SCOREBOARD]
    assert not box.isEnabledTo(tab) and "nicht unterstützt" in box.title()
    tab.apply_features(p.supported_features(2, None))
    assert box.isEnabledTo(tab) and box.title() == "Anzeigetafel"


def test_text_preview_renders(window):
    window.text_tab.text.setText("Test")
    assert window.text_tab.preview.pixmap() is not None


def test_english_ui(tmp_path):
    from ipixel.studio import i18n

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("ipixel-tests")
    app.setApplicationName("studio-test-en")
    i18n.set_language("en")
    try:
        win = MainWindow()
        assert win.editor.send_image.text() == "Send image"
        assert win.centralWidget().tabText(2) == "Control"
        assert win.editor.tool_buttons[Tool.FILL].text() == "Fill"
        assert win.windowTitle().startswith("Untitled – 1 frame")
        assert [a.text() for a in win.menuBar().actions()][0] == "File"
        win.close()
    finally:
        i18n.set_language("de")


def test_panel_size_applies_everywhere(window):
    window._apply_size(32, 32)
    assert (window.doc.width, window.doc.height) == (32, 32)
    assert window.text_tab.height_spin.value() == 32
    assert window.size_label.text() == "32×32"
