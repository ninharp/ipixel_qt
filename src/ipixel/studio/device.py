"""Qt-facing wrapper around IPixel: serialised jobs, live frame pump, signals."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from PySide6.QtCore import QObject, Signal

from .. import protocol as p
from ..client import IPixel, IPixelError, scan
from .i18n import tr

log = logging.getLogger(__name__)

Job = Callable[[IPixel], Awaitable[object]]


class DeviceController(QObject):
    stateChanged = Signal(str)
    infoChanged = Signal(object)
    devicesFound = Signal(list)
    message = Signal(str)
    error = Signal(str)
    busyChanged = Signal(bool)
    liveChanged = Signal(bool)
    passwordNeeded = Signal(str)

    DISCONNECTED = "getrennt"
    CONNECTING = "verbinde…"
    CONNECTED = "verbunden"

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.px: IPixel | None = None
        self.state = self.DISCONNECTED
        self.live = False
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._busy = 0
        self._live_pending: bytes | None = None
        self._live_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self.state == self.CONNECTED and self.px is not None and self.px.connected

    @property
    def info(self) -> p.DeviceInfo | None:
        return self.px.info if self.px else None

    @property
    def features(self) -> dict[p.Feature, bool | None]:
        return self.px.features if self.px else p.supported_features(None, None)

    # Connection

    def scan(self) -> None:
        self._spawn(self._scan())

    async def _scan(self) -> None:
        self.message.emit(tr("Suche Panels …"))
        try:
            found = await scan()
        except Exception as e:  # noqa: BLE001 - surface any backend error to the user
            self.error.emit(tr("Suche fehlgeschlagen: {error}", error=e))
            return
        self.devicesFound.emit([(d.address, d.name or "") for d in found])
        self.message.emit(tr("{count} Panel(s) gefunden", count=len(found)))

    def connect_to(self, target: str, password: str | None = None) -> None:
        if self.state != self.DISCONNECTED:
            return
        self._spawn(self._connect(target, password))

    async def _connect(self, target: str, password: str | None) -> None:
        self._set_state(self.CONNECTING)
        px = IPixel(target, password=password, on_disconnect=self._lost)
        try:
            await px.connect()
        except IPixelError as e:
            await px.disconnect()
            self._set_state(self.DISCONNECTED)
            if "password" in str(e):
                self.passwordNeeded.emit(target)
            else:
                self.error.emit(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self._set_state(self.DISCONNECTED)
            self.error.emit(tr("Verbindung fehlgeschlagen: {error}", error=e))
            return
        self.px = px
        self._set_state(self.CONNECTED)
        self.infoChanged.emit(px.info)
        i = px.info
        self.message.emit(tr("Verbunden: Typ 0x{type:02x}, {width}×{height}, Uhrzeit gestellt",
                             type=i.type_byte, width=i.width, height=i.height))

    def disconnect(self) -> None:
        self._spawn(self._disconnect())

    async def _disconnect(self) -> None:
        self._set_live(False)
        px, self.px = self.px, None
        # Leave CONNECTED before the link drops so _lost() does not report an error.
        self._set_state(self.DISCONNECTED)
        if px:
            async with self._lock:
                await px.disconnect()

    def _lost(self) -> None:
        if self.state == self.CONNECTED:
            self.px = None
            self._set_live(False)
            self._set_state(self.DISCONNECTED)
            self.error.emit(tr("Verbindung zum Panel verloren"))

    # Jobs

    def run(self, label: str, job: Job, keep_live: bool = False) -> None:
        """Queue a device action; content uploads leave live mode first."""
        if not self.connected:
            self.error.emit(tr("Nicht verbunden"))
            return
        self._spawn(self._run(label, job, keep_live))

    async def _run(self, label: str, job: Job, keep_live: bool) -> None:
        self._busy_add(1)
        try:
            async with self._lock:
                px = self.px
                if px is None:
                    return
                if not keep_live and self.live:
                    self._set_live(False)
                    await px.live(p.LiveMode.QUIT_KEEP_CURRENT)
                await job(px)
            self.message.emit(f"{label}: ok")
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed", label)
            self.error.emit(f"{label}: {e}")
        finally:
            self._busy_add(-1)

    # Live mode

    def set_live(self, on: bool) -> None:
        if on == self.live:
            return
        if on and not self.connected:
            self.error.emit(tr("Für den Live-Modus erst verbinden"))
            self.liveChanged.emit(False)
            return
        self._set_live(on)
        if not on and self.connected:
            self.run(tr("Live beendet"), lambda px: px.live(p.LiveMode.QUIT_KEEP_CURRENT), keep_live=True)

    def push_live(self, rgb: bytes) -> None:
        """Show this frame as soon as the link is free; older pending frames are dropped."""
        if not self.live or not self.connected:
            return
        self._live_pending = rgb
        if self._live_task is None or self._live_task.done():
            self._live_task = self._spawn(self._pump())

    async def _pump(self) -> None:
        while self._live_pending is not None and self.live:
            data, self._live_pending = self._live_pending, None
            try:
                async with self._lock:
                    if self.px is None:
                        return
                    await self.px.rgb(data)
            except Exception as e:  # noqa: BLE001
                self.error.emit(tr("Live-Bild: {error}", error=e))
                return

    # Helpers

    def _spawn(self, coro: Awaitable[object]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _set_state(self, state: str) -> None:
        self.state = state
        self.stateChanged.emit(state)

    def _set_live(self, on: bool) -> None:
        if self.live != on:
            self.live = on
            self.liveChanged.emit(on)

    def _busy_add(self, n: int) -> None:
        was = self._busy > 0
        self._busy += n
        if (self._busy > 0) != was:
            self.busyChanged.emit(self._busy > 0)
