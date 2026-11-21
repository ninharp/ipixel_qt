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


def _drag(canvas, a, b):
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=_cell_center(canvas, *a))
    QTest.mouseMove(canvas, _cell_center(canvas, *b))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=_cell_center(canvas, *b))


RED = (255, 0, 0)


def test_select_and_drag_moves_pixels(window):
    ed, canvas, doc = window.editor, window.editor.canvas, window.doc
    doc.rect((2, 2), (4, 4), RED, fill=True)
    ed.set_tool(Tool.SELECT)
    _drag(canvas, (2, 2), (4, 4))
    assert canvas.selection == (2, 2, 4, 4)
    _drag(canvas, (3, 3), (13, 5))
    assert canvas.selection == (12, 4, 14, 6)
    assert doc.pixel((12, 4)) == RED and doc.pixel((2, 2)) == (0, 0, 0)
    window.undo_act.trigger()
    assert doc.pixel((2, 2)) == RED


def test_click_inside_selection_keeps_pixels(window):
    ed, canvas, doc = window.editor, window.editor.canvas, window.doc
    doc.plot([(5, 5)], RED)
    ed.set_tool(Tool.SELECT)
    _drag(canvas, (4, 4), (6, 6))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_cell_center(canvas, 5, 5))
    assert doc.pixel((5, 5)) == RED


def test_arrow_keys_move_selection_or_whole_frame(window):
    ed, canvas, doc = window.editor, window.editor.canvas, window.doc
    doc.plot([(0, 0)], RED)
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert doc.pixel((1, 0)) == RED
    QTest.keyClick(canvas, Qt.Key.Key_Left)
    QTest.keyClick(canvas, Qt.Key.Key_Left)
    assert doc.pixel((63, 0)) == RED  # whole frame wraps around
    canvas.set_selection((63, 0, 63, 0))
    QTest.keyClick(canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert doc.pixel((63, 4)) == RED and canvas.selection == (63, 4, 63, 4)
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    assert canvas.selection is None


def test_move_as_new_frame_builds_an_animation(window):
    ed, doc = window.editor, window.doc
    doc.plot([(0, 0)], RED)
    ed.move_new_frame.setChecked(True)
    for _ in range(3):
        ed.nudge(1, 0)
    assert len(doc.frames) == 4
    assert [f.image.getpixel((i, 0)) for i, f in enumerate(doc.frames)] == [RED] * 4


def test_copy_paste_delete(window):
    ed, canvas, doc = window.editor, window.editor.canvas, window.doc
    doc.plot([(1, 1)], RED)
    canvas.set_selection((0, 0, 2, 2))
    ed.copy()
    canvas.set_selection((10, 10, 10, 10))
    ed.paste()
    assert doc.pixel((11, 11)) == RED
    assert canvas.selection == (10, 10, 12, 12)
    ed.delete()
    assert doc.pixel((11, 11)) == (0, 0, 0)
    assert doc.pixel((1, 1)) == RED
    ed.select_none()
    ed.cut()
    assert doc.pixel((1, 1)) == (0, 0, 0)  # no selection: cut takes the whole frame
    ed.paste()
    assert doc.pixel((1, 1)) == RED


def test_timeline_shows_position_and_total_length(window):
    doc, pos = window.doc, window.editor.timeline.position
    assert pos.text() == "Bild 1/1 · 0,1 s"
    doc.add_frame(True)
    doc.add_frame(True)
    doc.set_duration(250, all_frames=True)
    assert pos.text() == "Bild 3/3 · 0,8 s"
    doc.select(0)
    assert pos.text() == "Bild 1/3 · 0,8 s"
    doc.delete_frame()
    assert pos.text() == "Bild 1/2 · 0,5 s"


def test_loop_menu_entries(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    doc = window.doc
    for _ in range(9):
        doc.add_frame(True)
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Bilder")
    acts = {a.text(): a for a in menu.actions()}
    acts["Schleife: vor und zurück"].trigger()
    assert len(doc.frames) == 18
    window.undo_act.trigger()
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (3, True))
    acts["Schleife: Ende in Anfang überblenden …"].trigger()
    assert len(doc.frames) == 7
    assert window.editor.timeline.position.text().startswith("Bild ")


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


def test_text_tab_reports_fit(window):
    tab = window.text_tab
    tab.text.setText("Hello world")
    assert "passt auf eine Seite" in tab.fit_label.text()
    tab.size_spin.setValue(16)
    tab.effect.setCurrentIndex(tab.effect.findData(5))  # blink: static
    assert "2 Seiten" in tab.fit_label.text()
    tab.effect.setCurrentIndex(tab.effect.findData(1))  # scroll left
    assert "Lauftext" in tab.fit_label.text()


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
