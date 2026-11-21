# ipixel

Python library and CLI for LED matrix panels made for the "iPixel Color" app (`com.wifiled.ipixels`),
which advertise over BLE as `LED_BLE_xxxxxxxx`. The protocol was reconstructed from APK v3.8.1.

```bash
uv sync
uv run ipixel scan
export IPIXEL_DEVICE=50:28:13:54:ED:F4   # MAC, BLE address or LED_BLE_ name
uv run ipixel info
uv run ipixel brightness 40
uv run ipixel text "Hello world" --color ff8000 --effect scroll_left --speed 60
uv run ipixel image photo.jpg --fit cover
uv run ipixel gif cat.gif
uv run ipixel frame picture.png       # live frame (raw RGB, not stored)
uv run ipixel clock --style 2
uv run ipixel raw "05000780 01"       # send any frame and print the reply
```

```python
import asyncio
from ipixel import IPixel, TextEffect

async def main():
    async with IPixel("50:28:13:54:ED:F4") as px:
        print(px.info)               # type, size, block size
        await px.brightness(60)
        await px.text("Hi", color=(0, 255, 0), effect=TextEffect.BLINK)
        await px.image("logo.png", slot=1)   # store in program slot 1

asyncio.run(main())
```

## iPixel Studio (desktop app)

```bash
uv sync --extra studio
uv run ipixel-studio        # start it from a terminal that has Bluetooth permission
```

The user interface is available in English and German. It follows the system language on first
start and can be switched under View → Language / Sprache (the app offers to restart).

- **Image & animation:** pixel editor at panel size (pen, eraser, line, rectangle, ellipse,
  fill, eyedropper; right-click paints with the background colour), frames with individual
  durations, onion skin, preview playback, undo/redo. Opens PNG/GIF/JPEG, saves losslessly as
  (A)PNG, exports GIF. Sends a single image (PNG) or the animation (GIF), either display-only or
  stored in slot 1–99. **Live:** every edit and the playing preview appear on the panel at once.
- **Text:** system fonts, height, threshold, colour, background, effect, speed, raster preview.
- **Control:** power, rotate 180°, clock, set time, countdown, stopwatch, scoreboard,
  delete slots, raw commands with their reply.
- **Screen mirror:** streams a screen region (fixed or around the mouse pointer) live to the panel.

![Editor](docs/screenshots/editor-en.png)

| Text | Control | Screen mirror |
|---|---|---|
| ![Text](docs/screenshots/text-en.png) | ![Control](docs/screenshots/control-en.png) | ![Screen mirror](docs/screenshots/mirror-en.png) |

German UI: [editor](docs/screenshots/editor.png), [text](docs/screenshots/text.png),
[control](docs/screenshots/steuerung.png), [screen mirror](docs/screenshots/spiegeln.png).

## Other panel sizes

The library and the studio are not tied to 64×16. On connect the panel reports its type
(byte 4 of the info reply); that determines the size, the upload block size and whether the
text header needs three extra border bytes. The known sizes are the same as in the phone app:

| Type | Size | Type | Size | Type | Size |
|---|---|---|---|---|---|
| 0x80 / 0x00 | 64×64 | 0x8a | 64×32 | 0x94 | 16×16 |
| 0x81 | 32×32 | 0x8b, 0x8d | 96×32 | 0x95 | 64×64 |
| 0x82 | 32×16 | 0x8c, 0x86 | 128×32 | 0x96 | 96×64 |
| 0x83 | 64×16 | 0x8e | 160×32 | 0x97 | 128×64 |
| 0x84 | 96×16 | 0x8f | 192×32 | 0x98 | 160×64 |
| 0x85 | 64×20 | 0x90 | 256×32 | 0x99 | 192×64 |
| 0x87 | 144×16 | 0x91 | 320×32 | 0x9a | 256×64 |
| 0x88 | 192×16 | 0x92 | 384×32 | 0x9b | 320×64 |
| 0x89 | 48×24 | 0x93 | 448×32 | 0x9c | 384×64 |

Only 64×16 (0x83) has been tested so far. If a panel reports an unknown type, set the size by
hand in the studio under Edit → Change panel size.

macOS does not expose MAC addresses, so on macOS a MAC is also matched by the advertised name
`LED_BLE_` + the last four MAC bytes (logged as a warning, since names can be spoofed). On other
platforms a MAC matches the address only. Python needs the terminal's Bluetooth permission on
macOS.

## Protocol

GATT: service `0x00FA`, write `0xFA02` (with response, at most 509 bytes per write after
MTU 512), replies as notifications on the notify characteristic of the same service (`0xFA03`).

Every frame: `len:u16le` (including these two bytes) · `cmd:2` · payload.

| Command | Frame |
|---|---|
| Power | `05 00 07 01 <0/1>` |
| Brightness 0–100 | `05 00 04 80 <n>` |
| Rotate 180° | `05 00 06 80 <0/1>` |
| Info + set time | `08 00 01 80 HH MM SS <lang>` → reply byte 4 = type byte, byte 10 = password enabled |
| Clock | `0b 00 06 01 <style> <12h?> <date?> YY MM DD WD` |
| Scrolling text speed | `05 00 03 01 <n>` |
| Delete everything | `04 00 03 80` |
| Delete slots | `len 02 01 <count:u16le> <slots…>` |
| Countdown | `07 00 0d 80 <action> <min> <sec>` (0 reset, 1 start, 2 pause, 3 resume) |
| Stopwatch | `05 00 09 80 <action>` (same as countdown) |
| Scoreboard | `08 00 0a 80 <left:u16le> <right:u16le>` |
| Verify password | `07 00 05 02 <aa> <bb> <cc>` (digit pairs as decimal bytes) |

Uploads (PNG `02 00`, GIF `03 00`, text `00 01`) are sent in blocks of 12 KiB
(types 0x00/0x80/0x81) or 4 KiB (all others):

`len:u16le · cmd · opt(0 first, 2 following) · total:u32le · crc32(total):u32le · marker(02 for GIF, else 00) · slot · data`

Slot 1.. stores the content as a program, 101 only displays it. After each block the panel
acknowledges with `05 00 <cmd> <status>`: 1 = block ok, 3 = done, 0 = CRC error, 2 = rejected.

Live RGB (`00 00`): `len · 00 00 · opt · frame_size:u32le · R,G,B…` without CRC. It is only
shown in DIY mode: send `05 00 04 01 01` first (enter and clear; 3 = enter without clearing),
afterwards `… 02` (leave, keep the image) or `… 00` (leave, back to the previous program). The
panel acknowledges live frames with status 1, not 3. As frame size the app sends 4096 or 12288
rather than the data length; that works on the 64×16 panel.

Text payload: 14-byte header (`count:u16le, h-align, v-align, effect, speed, 1, R,G,B,
bg?, R,G,B`, plus 3 border bytes on the long 32/64-row panels), then per character
`80 R G B width height` + a 1-bit bitmap (rows padded to 8 bits, LSB first).
Effects: 0 fixed, 1 left, 2 right, 3 up, 4 down, 5 blink, 6 breathe, 7 snowflakes, 8 laser.
