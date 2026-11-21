from PIL import Image

from ipixel.studio.model import Animation, line_points

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def test_stroke_is_one_undo_step():
    a = Animation(8, 4)
    a.checkpoint()
    a.plot([(0, 0)], RED)
    a.line((0, 1), (7, 1), RED)
    assert a.pixel((7, 1)) == RED
    assert a.undo()
    assert a.pixel((0, 0)) == (0, 0, 0) and a.pixel((7, 1)) == (0, 0, 0)
    assert a.redo()
    assert a.pixel((7, 1)) == RED


def test_flood_fill_stops_at_border():
    a = Animation(5, 5)
    a.rect((1, 1), (3, 3), RED)
    a.flood_fill((2, 2), BLUE)
    assert a.pixel((2, 2)) == BLUE
    assert a.pixel((0, 0)) == (0, 0, 0)
    assert a.pixel((1, 1)) == RED


def test_frames_add_move_delete():
    a = Animation(4, 4)
    a.plot([(0, 0)], RED)
    a.add_frame(duplicate=True)
    assert len(a.frames) == 2 and a.index == 1 and a.pixel((0, 0)) == RED
    a.add_frame(duplicate=False)
    assert a.pixel((0, 0)) == (0, 0, 0)
    a.move_frame(-1)
    assert a.index == 1
    a.delete_frame()
    assert len(a.frames) == 2


def test_shift_wraps():
    a = Animation(4, 2)
    a.plot([(3, 0)], RED)
    a.shift(1, 1)
    assert a.pixel((0, 1)) == RED


def test_save_load_roundtrip_keeps_frames_and_durations(tmp_path):
    a = Animation(6, 3)
    a.plot([(1, 1)], RED)
    a.set_duration(250)
    a.add_frame(duplicate=False)
    a.plot([(2, 2)], BLUE)
    a.set_duration(40)
    path = tmp_path / "anim.png"
    a.save(path)

    b = Animation(6, 3)
    b.load(path)
    assert len(b.frames) == 2
    assert [f.duration for f in b.frames] == [250, 40]
    assert b.frames[0].image.getpixel((1, 1)) == RED
    assert b.frames[1].image.getpixel((2, 2)) == BLUE


def test_gif_export_is_animated():
    a = Animation(4, 4)
    a.add_frame()
    a.plot([(0, 0)], RED)
    img = Image.open(__import__("io").BytesIO(a.gif_bytes()))
    assert img.n_frames == 2


def test_rgb_bytes_length():
    assert len(Animation(64, 16).rgb_bytes()) == 64 * 16 * 3


def test_line_points_diagonal():
    assert line_points((0, 0), (3, 3)) == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_resize_canvas_keeps_pixels():
    a = Animation(4, 4)
    a.plot([(1, 1)], RED)
    a.resize_canvas(8, 2)
    assert (a.width, a.height) == (8, 2)
    assert a.pixel((1, 1)) == RED
