"""Async BLE client for iPixel Color LED panels (advertised as ``LED_BLE_xxxxxxxx``)."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

from . import protocol as p
from . import render
from .render import ImageSource

log = logging.getLogger(__name__)

NAME_PREFIX = "LED_BLE_"

Progress = Callable[[int, int], None]


class IPixelError(Exception):
    pass


async def scan(timeout: float = 8.0) -> list[BLEDevice]:
    found = await BleakScanner.discover(timeout=timeout)
    return [d for d in found if (d.name or "").startswith(NAME_PREFIX)]


def name_for_mac(mac: str) -> str:
    """The advertised name is the prefix plus the last four MAC bytes."""
    return NAME_PREFIX + mac.replace(":", "").upper()[-8:]


class IPixel:
    """One connected panel.

    ``target`` is a BLE address (a MAC on Linux/Windows, a CoreBluetooth UUID on
    macOS) or the advertised name. A MAC also matches by name, so it works on
    macOS where MACs are hidden.
    """

    def __init__(
        self,
        target: str,
        password: str | None = None,
        timeout: float = 15.0,
        on_disconnect: Callable[[], None] | None = None,
    ):
        self.target = target
        self.on_disconnect = on_disconnect
        self.password = password
        self.timeout = timeout
        self.info: p.DeviceInfo | None = None
        self.advert: p.Advert | None = None
        self._client: BleakClient | None = None
        self._notify_uuid = p.NOTIFY_UUID
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._live = False

    async def __aenter__(self) -> IPixel:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    @property
    def features(self) -> dict[p.Feature, bool | None]:
        return p.supported_features(self.info.led_type if self.info else None, self.advert)

    @property
    def size(self) -> tuple[int, int]:
        if not self.info or self.info.width is None or self.info.height is None:
            raise IPixelError(f"unknown panel size: {self.info}")
        return self.info.width, self.info.height

    async def connect(self) -> p.DeviceInfo:
        device = await self._find()
        self._client = BleakClient(device, timeout=self.timeout, disconnected_callback=self._on_disconnected)
        await self._client.connect()
        await self._acquire_mtu()
        self._notify_uuid = self._pick_notify_char()
        await self._client.start_notify(self._notify_uuid, self._on_notify)
        self.info = await self.query_info()
        log.info("connected: %s", self.info)
        if self.info.password_required:
            if not self.password:
                raise IPixelError("panel is password protected; pass password=")
            await self.verify_password(self.password)
        return self.info

    async def _acquire_mtu(self) -> None:
        # BlueZ reports the minimum MTU (23) until asked; without this, Linux would write in 20-byte chunks.
        backend = getattr(self._client, "_backend", None)
        acquire = getattr(backend, "_acquire_mtu", None)
        if acquire is None:
            return
        try:
            await acquire()
        except Exception as e:  # noqa: BLE001 - keep working with small writes
            log.warning("could not acquire MTU, using small writes: %s", e)

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            if self._live:
                await self.live(p.LiveMode.QUIT_KEEP_CURRENT)
            await self._client.disconnect()

    async def _find(self) -> BLEDevice:
        t = self.target
        is_mac = t.count(":") == 5
        # Names are trivially spoofable; only macOS (which hides MACs) falls back to them for a MAC target.
        by_name = not is_mac or sys.platform == "darwin"
        wanted_name = name_for_mac(t) if is_mac else t

        def match(d: BLEDevice, ad) -> bool:
            if d.address.upper() == t.upper() or (by_name and (d.name or ad.local_name or "") == wanted_name):
                self.advert = p.parse_advert(ad.manufacturer_data)
                return True
            return False

        dev = await BleakScanner.find_device_by_filter(match, timeout=self.timeout)
        if dev is None:
            raise IPixelError(f"{t} not found")
        if dev.address.upper() != t.upper():
            log.warning("matched %s by advertised name %s only (address %s)", t, dev.name, dev.address)
        return dev

    def _pick_notify_char(self) -> str:
        assert self._client
        service = self._client.services.get_service(p.SERVICE_UUID)
        if service is None:
            raise IPixelError("service 0x00FA missing; not an iPixel panel?")
        for ch in service.characteristics:
            if "notify" in ch.properties:
                return ch.uuid
        raise IPixelError("no notify characteristic in service 0x00FA")

    def _on_disconnected(self, _client: BleakClient) -> None:
        self._live = False
        if self.on_disconnect:
            self.on_disconnect()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    def _on_notify(self, _ch: BleakGATTCharacteristic, data: bytearray) -> None:
        log.debug("<- %s", bytes(data).hex(" "))
        self._inbox.put_nowait(bytes(data))

    def _drain(self) -> None:
        while not self._inbox.empty():
            self._inbox.get_nowait()

    async def send(self, frame: bytes, on_written: Callable[[int], None] | None = None) -> None:
        """Write one frame, split into GATT writes the link can carry."""
        if not self._client or not self._client.is_connected:
            raise IPixelError("not connected")
        step = min(p.MAX_WRITE, max(20, self._client.mtu_size - 3))
        log.debug("-> %s%s", frame[:24].hex(" "), " ..." if len(frame) > 24 else "")
        for i in range(0, len(frame), step):
            await self._client.write_gatt_char(p.WRITE_UUID, frame[i:i + step], response=True)
            if on_written:
                on_written(min(i + step, len(frame)))

    async def request(self, frame: bytes, timeout: float = 3.0) -> bytes:
        async with self._lock:
            self._drain()
            await self.send(frame)
            return await asyncio.wait_for(self._inbox.get(), timeout)

    async def query_info(self) -> p.DeviceInfo:
        """Also sets the panel clock to the host's local time."""
        return p.parse_device_info(await self.request(p.device_info()))

    async def upload(
        self,
        kind: p.Upload,
        data: bytes,
        slot: int = p.SLOT_TEMPORARY,
        progress: Progress | None = None,
    ) -> None:
        """Send data in acknowledged blocks; ``progress(done, total)`` reports bytes written."""
        block_size = self.info.block_size if self.info else p.BLOCK_SIZE_SMALL
        blocks = p.upload_blocks(kind, data, slot, block_size)
        total = sum(len(b) for b in blocks)
        done = 0
        async with self._lock:
            for n, block in enumerate(blocks, 1):
                report = (lambda written, base=done: progress(base + written, total)) if progress else None
                ack = await self._send_block(kind, block, report)
                done += len(block)
                log.debug("block %d/%d ack %s", n, len(blocks), ack.raw.hex(" "))
                if ack.done:
                    break
        if progress:
            progress(total, total)

    async def _send_block(
        self, kind: p.Upload, block: bytes, on_written: Callable[[int], None] | None = None, attempts: int = 3
    ) -> p.Ack:
        for attempt in range(attempts):
            self._drain()
            await self.send(block, on_written)
            try:
                return await self._wait_ack(kind, 8.0 if attempt == 0 else 2.0)
            except asyncio.TimeoutError:
                log.warning("no ack, resending block (%d)", attempt + 1)
        raise IPixelError("panel did not acknowledge upload")

    async def _wait_ack(self, kind: p.Upload, timeout: float) -> p.Ack:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            data = await asyncio.wait_for(self._inbox.get(), max(0.01, deadline - loop.time()))
            ack = p.parse_ack(data)
            if ack is None or ack.cmd != kind.value:
                continue
            if ack.status == 0:
                raise IPixelError(f"transfer rejected (checksum): {data.hex(' ')}")
            if ack.status == 2 and kind is not p.Upload.RAW_RGB:
                raise IPixelError(f"transfer rejected (no space?): {data.hex(' ')}")
            return ack

    # Simple commands

    async def power(self, on: bool) -> None:
        await self.send(p.power(on))

    async def brightness(self, level: int) -> None:
        await self.send(p.brightness(level))

    async def flip(self, upside_down: bool) -> None:
        await self.send(p.flip(upside_down))

    async def clock(self, style: int = 1, hour_24: bool = True, show_date: bool = True) -> None:
        await self.send(p.clock(style, hour_24, show_date))

    async def text_speed(self, speed: int) -> None:
        await self.send(p.text_speed(speed))

    async def countdown(self, minutes: int, seconds: int = 0, action: p.TimerAction = p.TimerAction.START) -> None:
        await self.send(p.countdown(action, minutes, seconds))

    async def stopwatch(self, action: p.TimerAction) -> None:
        await self.send(p.stopwatch(action))

    async def scoreboard(self, left: int, right: int) -> None:
        await self.send(p.scoreboard(left, right))

    async def rhythm(self, level: int, mode: int = 0) -> None:
        await self.send(p.rhythm(level, mode))

    async def clear(self) -> None:
        """Delete every stored program on the panel."""
        await self.send(p.clear_all())

    async def delete_slots(self, slots: list[int]) -> None:
        await self.send(p.delete_slots(slots))

    async def verify_password(self, password: str) -> bytes:
        return await self.request(p.verify_password(password))

    async def set_password(self, enabled: bool, password: str) -> bytes:
        return await self.request(p.set_password(enabled, password))

    # Content

    async def image(
        self, src: ImageSource, slot: int = p.SLOT_TEMPORARY, fit: str = "contain",
        progress: Progress | None = None,
    ) -> None:
        w, h = self.size
        await self.upload(p.Upload.PNG, render.to_png(src, w, h, fit), slot, progress)

    async def gif(
        self, src: ImageSource, slot: int = p.SLOT_TEMPORARY, fit: str = "contain",
        progress: Progress | None = None,
    ) -> None:
        w, h = self.size
        await self.upload(p.Upload.GIF, render.to_gif(src, w, h, fit), slot, progress)

    async def live(self, mode: p.LiveMode) -> None:
        await self.send(p.diy_mode(mode))
        self._live = mode in (p.LiveMode.ENTER_CLEAR, p.LiveMode.ENTER_KEEP)

    async def frame(self, src: ImageSource, fit: str = "contain") -> None:
        """Show an image immediately in live mode (raw RGB, not stored)."""
        w, h = self.size
        await self.rgb(render.to_rgb(src, w, h, fit))

    async def rgb(self, data: bytes) -> None:
        """Show raw R,G,B row-major pixel bytes; enters live mode on first use."""
        w, h = self.size
        if len(data) != w * h * 3:
            raise ValueError(f"expected {w * h * 3} bytes for {w}x{h}")
        if not self._live:
            await self.live(p.LiveMode.ENTER_CLEAR)
        await self.upload(p.Upload.RAW_RGB, data)

    async def fill(self, color: tuple[int, int, int]) -> None:
        w, h = self.size
        await self.rgb(bytes(color) * (w * h))

    async def text(
        self,
        text: str,
        color: tuple[int, int, int] = (255, 255, 255),
        background: tuple[int, int, int] | None = None,
        effect: int = p.TextEffect.SCROLL_LEFT,
        speed: int = p.DEFAULT_TEXT_SPEED,
        height: int | None = None,
        font: str | Path | None = None,
        slot: int = p.SLOT_TEMPORARY,
        size: int | None = None,
        spacing: int = 1,
        progress: Progress | None = None,
    ) -> None:
        w, h = self.size
        glyph_height = height or min(h, 64)
        border = (0, 0, 0) if self.info and self.info.has_border_fields else None
        gl = render.glyphs(text, glyph_height, color, font, size=size, spacing=spacing)
        if not p.TextEffect(effect).scrolls:
            gl = render.page_glyphs(text, gl, w)
        body = p.text_payload(
            gl,
            color,
            background,
            effect=effect,
            speed=speed,
            border=border,
        )
        await self.upload(p.Upload.TEXT, body, slot, progress)
