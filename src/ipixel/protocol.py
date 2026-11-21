"""Frame builders for the iPixel Color BLE protocol (no I/O).

Every frame starts with its total length as little-endian uint16, followed by
a two-byte command and the payload. Multi-byte integers are little-endian
unless noted otherwise.
"""

from __future__ import annotations

import datetime as _dt
import struct
import zlib
from dataclasses import dataclass
from enum import Enum, IntEnum

SERVICE_UUID = "000000fa-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fa02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fa03-0000-1000-8000-00805f9b34fb"

# Largest GATT write the app uses after requesting MTU 512.
MAX_WRITE = 509

# The device accepts at most this many payload bytes per acknowledged block.
BLOCK_SIZE_LARGE = 12288
BLOCK_SIZE_SMALL = 4096

SLOT_TEMPORARY = 101

# Text speed runs 0 (slow) .. 100 (fast); the vendor app starts at 65-80, which is hectic for
# blink/breathe on a 64x16 panel.
DEFAULT_TEXT_SPEED = 30


class Upload(IntEnum):
    """Command words for chunked uploads (low byte, high byte)."""

    RAW_RGB = 0x0000
    PNG = 0x0002
    GIF = 0x0003
    TEXT = 0x0100

    @property
    def word(self) -> bytes:
        return struct.pack("<H", self.value)


class ClockStyle(IntEnum):
    STYLE_1 = 1
    STYLE_2 = 2
    STYLE_3 = 3
    STYLE_4 = 4
    STYLE_5 = 5
    STYLE_6 = 6
    STYLE_7 = 7
    STYLE_8 = 8


class TimerAction(IntEnum):
    """Countdown and stopwatch control as sent by the app's buttons."""

    RESET = 0
    START = 1
    PAUSE = 2
    RESUME = 3


class LiveMode(IntEnum):
    """DIY/live drawing mode; raw RGB frames are only shown while it is entered."""

    QUIT_SHOW_PREVIOUS = 0
    ENTER_CLEAR = 1
    QUIT_KEEP_CURRENT = 2
    ENTER_KEEP = 3


class TextEffect(IntEnum):
    FIXED = 0
    SCROLL_LEFT = 1
    SCROLL_RIGHT = 2
    SCROLL_UP = 3
    SCROLL_DOWN = 4
    BLINK = 5
    BREATH = 6
    SNOWFLAKES = 7
    LASER = 8

    @property
    def scrolls(self) -> bool:
        """Scrolling effects run the text through; all others show it screen by screen."""
        return self in (TextEffect.SCROLL_LEFT, TextEffect.SCROLL_RIGHT)


# Index = the app's internal LED type (see _TYPE_BYTE_TO_LED_TYPE), value = (width, height).
LED_SIZES: list[tuple[int, int]] = [
    (64, 64), (96, 16), (32, 32), (64, 16), (32, 16), (64, 20), (128, 32),
    (144, 16), (192, 16), (48, 24), (64, 32), (96, 32), (128, 32), (96, 32),
    (160, 32), (192, 32), (256, 32), (320, 32), (384, 32), (448, 32), (16, 16),
    (32, 32), (32, 16), (48, 24), (64, 64), (96, 64), (128, 64), (160, 64),
    (192, 64), (256, 64), (320, 64), (384, 64), (448, 64), (512, 64),
    (576, 64), (640, 64),
]

# Device type byte (response byte 4) -> app LED type index.
_TYPE_BYTE_TO_LED_TYPE = {
    0x00: 0, 0x80: 0, 0x81: 2, 0x82: 4, 0x83: 3, 0x84: 1, 0x85: 5, 0x86: 6,
    0x87: 7, 0x88: 8, 0x89: 9, 0x8A: 10, 0x8B: 11, 0x8C: 12, 0x8D: 13,
    0x8E: 14, 0x8F: 15, 0x90: 16, 0x91: 17, 0x92: 18, 0x93: 19, 0x94: 20,
    0x95: 24, 0x96: 25, 0x97: 26, 0x98: 27, 0x99: 28, 0x9A: 29, 0x9B: 30,
    0x9C: 31,
}

# Only these LED types get the large block size; everything else uses 4 KiB.
_LARGE_BLOCK_TYPE_BYTES = {0x00, 0x80, 0x81}


@dataclass(frozen=True)
class DeviceInfo:
    type_byte: int
    led_type: int | None
    width: int | None
    height: int | None
    block_size: int
    password_required: bool
    raw: bytes

    @property
    def has_border_fields(self) -> bool:
        t = self.led_type
        return t is not None and (16 <= t < 20 or t >= 24)


def frame(cmd: bytes, payload: bytes = b"") -> bytes:
    length = 2 + len(cmd) + len(payload)
    return struct.pack("<H", length) + cmd + payload


def power(on: bool) -> bytes:
    return frame(b"\x07\x01", bytes([1 if on else 0]))


def brightness(level: int) -> bytes:
    return frame(b"\x04\x80", bytes([_clamp(level, 0, 100)]))


def flip(upside_down: bool) -> bytes:
    return frame(b"\x06\x80", bytes([1 if upside_down else 0]))


def text_speed(speed: int) -> bytes:
    return frame(b"\x03\x01", bytes([_clamp(speed, 0, 255)]))


def diy_mode(mode: int) -> bytes:
    return frame(b"\x04\x01", bytes([_clamp(mode, 0, 255)]))


def clear_all() -> bytes:
    return frame(b"\x03\x80")


def exit_mode() -> bytes:
    return frame(b"\x01\x01")


def hw_info() -> bytes:
    return frame(b"\x05\x80")


def weekday(now: _dt.datetime | None = None) -> bytes:
    return frame(b"\x12\x80", bytes([_week_of_date(now or _dt.datetime.now())]))


def device_info(now: _dt.datetime | None = None, chinese: bool = False) -> bytes:
    """Query the LED type; this also sets the device clock (HH:MM:SS)."""
    now = now or _dt.datetime.now()
    return frame(b"\x01\x80", bytes([now.hour, now.minute, now.second, 1 if chinese else 0]))


def clock(
    style: int = 1,
    hour_24: bool = True,
    show_date: bool = True,
    now: _dt.datetime | None = None,
) -> bytes:
    now = now or _dt.datetime.now()
    return frame(
        b"\x06\x01",
        bytes([
            _clamp(style, 0, 255),
            0 if hour_24 else 1,
            1 if show_date else 0,
            now.year - 2000,
            now.month,
            now.day,
            _week_of_date(now),
        ]),
    )


def rhythm(level: int, mode: int) -> bytes:
    return frame(b"\x00\x02", bytes([_clamp(level, 0, 255), _clamp(mode, 0, 255)]))


def rhythm_chart(mode: int, levels: list[int]) -> bytes:
    """Eleven spectrum bars, each 0..255 (scaled to 0..15 like the app)."""
    bars = bytearray(11)
    for i, v in enumerate(levels[:11]):
        scaled = (v & 0xFF) * 15 // 255
        bars[i] = scaled if scaled else 1
    return frame(b"\x01\x02", bytes([_clamp(mode, 0, 255)]) + bytes(bars))


def countdown(action: TimerAction, minutes: int, seconds: int) -> bytes:
    return frame(b"\x0d\x80", bytes([int(action), _clamp(minutes, 0, 255), _clamp(seconds, 0, 59)]))


def stopwatch(action: TimerAction) -> bytes:
    return frame(b"\x09\x80", bytes([int(action)]))


def scoreboard(left: int, right: int) -> bytes:
    return frame(b"\x0a\x80", struct.pack("<HH", _clamp(left, 0, 999), _clamp(right, 0, 999)))


def delete_slots(slots: list[int]) -> bytes:
    body = struct.pack("<H", len(slots)) + bytes(_clamp(s, 0, 255) for s in slots)
    return frame(b"\x02\x01", body)


def set_password(enabled: bool, password: str) -> bytes:
    a, b, c = _password_bytes(password)
    return frame(b"\x04\x02", bytes([1 if enabled else 0, a, b, c]))


def verify_password(password: str) -> bytes:
    a, b, c = _password_bytes(password)
    return frame(b"\x05\x02", bytes([a, b, c]))


def upload_blocks(kind: Upload, data: bytes, slot: int, block_size: int) -> list[bytes]:
    """Split an upload into device blocks; send each and wait for its ack.

    ``slot`` 1.. stores the content in that program slot, ``SLOT_TEMPORARY``
    shows it without storing.
    """
    if not data:
        raise ValueError("empty upload")
    if kind is Upload.RAW_RGB:
        return _raw_blocks(data, block_size)
    crc = zlib.crc32(data) & 0xFFFFFFFF
    marker = 0x02 if kind is Upload.GIF else 0x00
    blocks = []
    for index, start in enumerate(range(0, len(data), block_size)):
        chunk = data[start:start + block_size]
        header = (
            kind.word
            + bytes([0 if index == 0 else 2])
            + struct.pack("<II", len(data), crc)
            + bytes([marker, _clamp(slot, 0, 255)])
        )
        blocks.append(frame(header[:2], header[2:] + chunk))
    return blocks


def _raw_blocks(rgb: bytes, frame_size: int) -> list[bytes]:
    # The app splits live frames at 12 KiB but writes its per-type frame size
    # into the length field, not the real data length.
    blocks = []
    for index, start in enumerate(range(0, len(rgb), BLOCK_SIZE_LARGE)):
        chunk = rgb[start:start + BLOCK_SIZE_LARGE]
        body = bytes([0 if index == 0 else 2]) + struct.pack("<I", frame_size) + chunk
        blocks.append(frame(Upload.RAW_RGB.word, body))
    return blocks


def text_payload(
    glyphs: list[tuple[int, int, bytes, tuple[int, int, int]]],
    color: tuple[int, int, int],
    background: tuple[int, int, int] | None = None,
    effect: int = TextEffect.SCROLL_LEFT,
    speed: int = DEFAULT_TEXT_SPEED,
    h_align: int = 0,
    v_align: int = 0,
    border: tuple[int, int, int] | None = None,
) -> bytes:
    """Assemble the text upload body from pre-rendered 1-bit glyphs.

    Each glyph is (width, height, packed_bits, rgb). ``border`` (type, speed,
    effect) is required on the long 32/64-row panels and must be omitted on
    the others.
    """
    bg = background or (0, 0, 0)
    head = bytes([
        len(glyphs) & 0xFF, (len(glyphs) >> 8) & 0xFF,
        h_align & 0xFF, v_align & 0xFF, effect & 0xFF, _clamp(speed, 0, 255),
        1, *color,
        0 if bg == (0, 0, 0) else 1, *bg,
    ])
    if border is not None:
        head += bytes(b & 0xFF for b in border)
    body = bytearray(head)
    for width, height, bits, rgb in glyphs:
        body += bytes([0x80, *rgb, width, height]) + bits
    return bytes(body)


def pack_bits(pixels: list[list[bool]]) -> bytes:
    """Pack rows of on/off pixels, each row padded to 8 bits, LSB first."""
    out = bytearray()
    for row in pixels:
        padded = list(row) + [False] * (-len(row) % 8)
        for i in range(0, len(padded), 8):
            byte = 0
            for bit, on in enumerate(padded[i:i + 8]):
                if on:
                    byte |= 1 << bit
            out.append(byte)
    return bytes(out)


MANUFACTURER_ID = 0x5254  # "TR"


@dataclass(frozen=True)
class Advert:
    """Vendor fields from the advertisement, formatted like the app does (cid "0007", pid "04")."""

    cid: str
    pid: str
    size_byte: int | None


def parse_advert(manufacturer_data: dict[int, bytes]) -> Advert | None:
    data = manufacturer_data.get(MANUFACTURER_ID)
    if data is None or len(data) < 5 or data[0] != 0 or data[1] != 0x72:
        return None
    cid = ("0000" + str(data[2]) + str(data[3]))[-4:]
    pid = ("000" + str(data[4]))[-3:] if data[4] >= 100 else ("00" + str(data[4]))[-2:]
    return Advert(cid, pid, data[6] if len(data) > 6 else None)


class Feature(str, Enum):
    STOPWATCH = "stopwatch"
    SCOREBOARD = "scoreboard"
    COUNTDOWN = "countdown"


_MECHA_TYPES = {20, 21, 22, 23}


def supported_features(led_type: int | None, advert: Advert | None) -> dict[Feature, bool | None]:
    """Which optional screens the vendor app offers for this panel; None = rule unknown."""
    if led_type in _MECHA_TYPES:
        return {f: True for f in Feature}
    if led_type in (3, 5):
        new = advert is not None and advert.cid == "0001" and advert.pid in {"04", "05", "28"}
        return {Feature.STOPWATCH: new, Feature.SCOREBOARD: new, Feature.COUNTDOWN: False}
    return {f: None for f in Feature}


@dataclass(frozen=True)
class Ack:
    cmd: int
    status: int
    raw: bytes

    @property
    def done(self) -> bool:
        return self.status == 3


def parse_ack(data: bytes) -> Ack | None:
    if len(data) == 5 and data[0] == 5 and data[1] == 0:
        return Ack(cmd=data[2] | data[3] << 8, status=data[4], raw=bytes(data))
    return None


def parse_device_info(data: bytes) -> DeviceInfo:
    if len(data) < 5:
        raise ValueError(f"short device info response: {data.hex(' ')}")
    type_byte = data[4]
    led_type = _TYPE_BYTE_TO_LED_TYPE.get(type_byte)
    width = height = None
    if led_type is not None:
        width, height = LED_SIZES[led_type]
    block = BLOCK_SIZE_LARGE if type_byte in _LARGE_BLOCK_TYPE_BYTES else BLOCK_SIZE_SMALL
    return DeviceInfo(
        type_byte=type_byte,
        led_type=led_type,
        width=width,
        height=height,
        block_size=block,
        password_required=len(data) >= 11 and data[10] == 1,
        raw=bytes(data),
    )


def _password_bytes(password: str) -> tuple[int, int, int]:
    if len(password) not in (4, 6) or not password.isdigit():
        raise ValueError("password must be 4 or 6 digits")
    parts = [int(password[i:i + 2]) for i in range(0, len(password), 2)] + [0]
    return parts[0], parts[1], parts[2]


def _week_of_date(d: _dt.datetime) -> int:
    return d.isoweekday()


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))
