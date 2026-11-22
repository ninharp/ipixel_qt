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


def test_move_box_leaves_background_and_is_undoable():
    a = Animation(8, 4)
    a.rect((1, 1), (2, 2), RED, fill=True)
    moved = a.move_box((1, 1, 2, 2), 3, 0, BLUE)
    assert moved == (4, 1, 5, 2)
    assert a.pixel((4, 1)) == RED and a.pixel((5, 2)) == RED
    assert a.pixel((1, 1)) == BLUE
    a.undo()
    assert a.pixel((1, 1)) == RED and a.pixel((4, 1)) == (0, 0, 0)


def test_move_box_off_edge_is_clipped():
    a = Animation(4, 4)
    a.plot([(3, 0)], RED)
    a.move_box((3, 0, 3, 0), 2, 0, (0, 0, 0))
    assert all(a.pixel((x, 0)) == (0, 0, 0) for x in range(4))


def test_move_box_on_all_frames():
    a = Animation(4, 2)
    a.plot([(0, 0)], RED)
    a.add_frame(True)
    a.move_box((0, 0, 0, 0), 1, 0, (0, 0, 0), all_frames=True)
    assert [f.image.getpixel((1, 0)) for f in a.frames] == [RED, RED]


def test_crop_and_stamp_copy_a_region():
    a = Animation(6, 2)
    a.plot([(0, 0), (1, 1)], RED)
    piece = a.crop((0, 0, 1, 1))
    a.stamp(piece, 4, 0)
    assert a.pixel((4, 0)) == RED and a.pixel((5, 1)) == RED
    a.stamp(piece, 5, 1)  # partly outside: no error
    assert a.pixel((5, 1)) == RED


def test_clip_box_normalises_and_clips():
    a = Animation(4, 4)
    assert a.clip_box((3, 3, -2, 1)) == (0, 1, 3, 3)
    assert a.clip_box((5, 5, 9, 9)) is None


def _numbered(n):
    """Animation whose frame i is filled with grey level i*10, to track frame order."""
    a = Animation(2, 1)
    a.clear((0, 0, 0))
    for i in range(1, n):
        a.add_frame(False)
        a.clear((i * 10,) * 3)
    return a


def _levels(a):
    return [f.image.getpixel((0, 0))[0] for f in a.frames]


def test_pingpong_does_not_repeat_turning_frames():
    a = _numbered(5)
    a.pingpong()
    assert _levels(a) == [0, 10, 20, 30, 40, 30, 20, 10]
    a.undo()
    assert len(a.frames) == 5


def test_reverse():
    a = _numbered(4)
    a.select(0)
    a.reverse()
    assert _levels(a) == [30, 20, 10, 0] and a.index == 3


def test_crossfade_loop_blends_tail_into_head():
    a = _numbered(10)  # levels 0..90
    a.crossfade_loop(3)
    lv = _levels(a)
    assert len(lv) == 7
    assert lv[:4] == [30, 40, 50, 60]  # untouched middle starts at frame 3
    # tail 70,80,90 blends towards head 0,10,20 with weights 1/4, 2/4, 3/4
    expected = [70 + (0 - 70) * 0.25, 80 + (10 - 80) * 0.5, 90 + (20 - 90) * 0.75]
    assert all(abs(got - want) <= 1 for got, want in zip(lv[4:], expected))
    # wrapping from the last frame back to the first is now a small step, not 90 -> 0
    assert abs(lv[-1] - lv[0]) < abs(90 - 0)


def test_find_loop_picks_the_most_similar_pair():
    a = Animation(2, 1)
    levels = [0, 50, 100, 150, 52, 200, 250]  # frame 4 nearly repeats frame 1
    a.clear((0, 0, 0))
    for lv in levels[1:]:
        a.add_frame(False)
        a.clear((lv,) * 3)
    start, end, diff = a.find_loop(min_frames=2)
    assert (start, end) == (1, 4) and diff == 2
    a.trim_to_loop(start, end)
    assert _levels(a) == [50, 100, 150]
    a.undo()
    assert len(a.frames) == 7


def test_find_loop_is_fast_enough_for_long_animations():
    import time

    a = Animation(64, 16)
    for i in range(119):
        a.add_frame(False)
        a.plot([(i % 64, i % 16)], (255, 0, 0))
    t = time.time()
    a.find_loop(min_frames=20)
    assert time.time() - t < 5


def test_resize_canvas_keeps_pixels():
    a = Animation(4, 4)
    a.plot([(1, 1)], RED)
    a.resize_canvas(8, 2)
    assert (a.width, a.height) == (8, 2)
    assert a.pixel((1, 1)) == RED
