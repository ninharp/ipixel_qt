import datetime as dt
import struct
import zlib

import pytest

from ipixel import protocol as p
from ipixel import render

NOW = dt.datetime(2026, 11, 21, 17, 45, 9)  # Saturday


# Expected bytes are the literal templates from the app's BaseSend/SendCore.
@pytest.mark.parametrize(
    ("built", "expected"),
    [
        (p.power(True), [5, 0, 7, 1, 1]),
        (p.power(False), [5, 0, 7, 1, 0]),
        (p.brightness(50), [5, 0, 4, 0x80, 50]),
        (p.flip(True), [5, 0, 6, 0x80, 1]),
        (p.text_speed(7), [5, 0, 3, 1, 7]),
        (p.diy_mode(1), [5, 0, 4, 1, 1]),
        (p.clear_all(), [4, 0, 3, 0x80]),
        (p.exit_mode(), [4, 0, 1, 1]),
        (p.hw_info(), [4, 0, 5, 0x80]),
        (p.weekday(NOW), [5, 0, 0x12, 0x80, 6]),
        (p.device_info(NOW), [8, 0, 1, 0x80, 17, 45, 9, 0]),
        (p.clock(3, hour_24=True, show_date=False, now=NOW), [11, 0, 6, 1, 3, 0, 0, 26, 11, 21, 6]),
        (p.clock(1, hour_24=False, show_date=True, now=NOW), [11, 0, 6, 1, 1, 1, 1, 26, 11, 21, 6]),
        (p.rhythm(4, 2), [6, 0, 0, 2, 4, 2]),
        (p.countdown(p.TimerAction.START, 5, 30), [7, 0, 13, 0x80, 1, 5, 30]),
        (p.countdown(p.TimerAction.RESUME, 5, 30), [7, 0, 13, 0x80, 3, 5, 30]),
        (p.stopwatch(p.TimerAction.PAUSE), [5, 0, 9, 0x80, 2]),
        (p.scoreboard(258, 3), [8, 0, 10, 0x80, 2, 1, 3, 0]),
        (p.delete_slots([1, 3]), [8, 0, 2, 1, 2, 0, 1, 3]),
        (p.verify_password("1234"), [7, 0, 5, 2, 12, 34, 0]),
        (p.verify_password("123456"), [7, 0, 5, 2, 12, 34, 56]),
        (p.set_password(True, "0102"), [8, 0, 4, 2, 1, 1, 2, 0]),
    ],
)
def test_command_templates(built, expected):
    assert built == bytes(expected)


def test_rhythm_chart_scales_to_15_and_floors_zero_to_one():
    f = p.rhythm_chart(2, [0, 255, 128] + [17] * 8)
    assert f[:5] == bytes([16, 0, 1, 2, 2])
    assert f[5:8] == bytes([1, 15, 7])
    assert len(f) == 16


def test_png_upload_single_block_header():
    data = bytes(range(200))
    (block,) = p.upload_blocks(p.Upload.PNG, data, slot=101, block_size=4096)
    crc = zlib.crc32(data)
    assert block[:2] == struct.pack("<H", len(data) + 15)
    assert block[2:4] == b"\x02\x00"
    assert block[4] == 0
    assert block[5:9] == struct.pack("<I", len(data))
    assert block[9:13] == struct.pack("<I", crc)
    assert block[13:15] == bytes([0, 101])
    assert block[15:] == data


def test_upload_splits_and_marks_continuation_blocks():
    data = bytes(10000)
    blocks = p.upload_blocks(p.Upload.GIF, data, slot=1, block_size=4096)
    assert [len(b) - 15 for b in blocks] == [4096, 4096, 1808]
    assert [b[4] for b in blocks] == [0, 2, 2]
    assert all(b[5:9] == struct.pack("<I", 10000) for b in blocks)
    assert all(b[13:15] == bytes([2, 1]) for b in blocks)
    assert all(b[2:4] == b"\x03\x00" for b in blocks)


def test_text_upload_uses_command_0001():
    (block,) = p.upload_blocks(p.Upload.TEXT, b"abc", slot=101, block_size=4096)
    assert block[2:4] == b"\x00\x01"
    assert block[13:15] == bytes([0, 101])


def test_raw_rgb_has_nine_byte_header_with_frame_size():
    rgb = bytes(32 * 32 * 3)
    (block,) = p.upload_blocks(p.Upload.RAW_RGB, rgb, slot=0, block_size=12288)
    assert block[:9] == struct.pack("<H", len(rgb) + 9) + b"\x00\x00\x00" + struct.pack("<I", 12288)
    assert block[9:] == rgb


def test_pack_bits_lsb_first_and_row_padding():
    rows = [[True, False, False, False, False, False, False, False, True],
            [False] * 8 + [False]]
    assert p.pack_bits(rows) == bytes([0x01, 0x01, 0x00, 0x00])


def test_text_payload_head_and_glyph_records():
    g = [(2, 1, b"\x03", (255, 0, 0))]
    body = p.text_payload(g, (255, 0, 0), effect=p.TextEffect.SCROLL_LEFT, speed=40)
    assert body[:14] == bytes([1, 0, 0, 0, 1, 40, 1, 255, 0, 0, 0, 0, 0, 0])
    assert body[14:] == bytes([0x80, 255, 0, 0, 2, 1, 3])


def test_text_payload_border_fields_and_background():
    body = p.text_payload([], (1, 2, 3), background=(9, 8, 7), border=(1, 2, 3))
    assert body[10:14] == bytes([1, 9, 8, 7])
    assert body[14:] == bytes([1, 2, 3])


@pytest.mark.parametrize(
    ("type_byte", "size", "block"),
    [(0x81, (32, 32), 12288), (0x80, (64, 64), 12288), (0x84, (96, 16), 4096), (0x83, (64, 16), 4096)],
)
def test_device_info_mapping(type_byte, size, block):
    info = p.parse_device_info(bytes([0x0b, 0, 1, 0x80, type_byte, 0, 0, 0, 0, 0, 1]))
    assert (info.width, info.height) == size
    assert info.block_size == block
    assert info.password_required


def test_parse_advert_matches_real_panel():
    # Captured from LED_BLE_1354EDF4 (64x16): manufacturer 0x5254 "TR".
    adv = p.parse_advert({0x5254: bytes.fromhex("00 72 00 07 04 00 83")})
    assert (adv.cid, adv.pid, adv.size_byte) == ("0007", "04", 0x83)
    assert p.parse_advert({0x004C: b"\x02\x15"}) is None


def test_features_for_this_panel_type():
    adv = p.parse_advert({0x5254: bytes.fromhex("00 72 00 07 04 00 83")})
    f = p.supported_features(3, adv)
    assert f == {p.Feature.STOPWATCH: False, p.Feature.SCOREBOARD: False, p.Feature.COUNTDOWN: False}
    newer = p.supported_features(3, p.Advert("0001", "05", 0x83))
    assert newer[p.Feature.SCOREBOARD] and not newer[p.Feature.COUNTDOWN]
    assert all(p.supported_features(21, None).values())
    assert set(p.supported_features(2, None).values()) == {None}


def test_parse_ack():
    ack = p.parse_ack(bytes([5, 0, 2, 0, 3]))
    assert ack.cmd == p.Upload.PNG and ack.done
    assert p.parse_ack(bytes([5, 0, 0, 1, 1])).cmd == p.Upload.TEXT
    assert p.parse_ack(b"\x01\x02") is None


def test_render_sizes():
    from PIL import Image

    img = Image.new("RGB", (100, 50), (255, 0, 0))
    assert len(render.to_rgb(img, 32, 32)) == 32 * 32 * 3
    rgb = render.to_rgb(img, 32, 32)
    assert rgb[:3] == b"\x00\x00\x00"  # letterbox top row
    assert rgb[16 * 96:16 * 96 + 3] == b"\xff\x00\x00"
    assert render.to_png(img, 32, 32)[:8] == b"\x89PNG\r\n\x1a\n"


def test_default_text_fits_64_px_panel():
    """Regression: "Hello world" was 84 px wide at 16 rows and the panel split it into two screens."""
    gl = render.glyphs("Hello world", 16, (255, 255, 255))
    assert render.text_width(gl) <= 64
    assert render.pages(gl, 64) == 1


def test_glyphs_are_cropped_with_fixed_spacing():
    tight = render.glyphs("HH", 16, (255, 255, 255), spacing=0)
    loose = render.glyphs("HH", 16, (255, 255, 255), spacing=2)
    assert loose[0][0] == tight[0][0] + 2
    w, h, bits, _ = tight[0]
    rb = (w + 7) // 8
    lit_cols = {x for y in range(h) for x in range(w) if bits[y * rb + x // 8] >> (x % 8) & 1}
    assert min(lit_cols) == 0 and max(lit_cols) == w - 1


def test_pages_breaks_between_glyphs():
    g = [(30, 16, b"", (0, 0, 0))] * 3
    assert render.pages(g, 64) == 2
    assert render.pages(g[:2], 64) == 1


def test_glyphs_have_requested_height_and_packed_length():
    gl = render.glyphs("Hi", 16, (0, 255, 0))
    assert len(gl) == 2
    for w, h, bits, rgb in gl:
        assert h == 16 and rgb == (0, 255, 0)
        assert len(bits) == ((w + 7) // 8) * h
        assert any(bits)
