"""Command line: ``ipixel --device 50:28:13:54:ED:F4 text "Hallo"``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from . import protocol as p
from .client import IPixel, scan

DEFAULT_DEVICE = os.environ.get("IPIXEL_DEVICE")


def _color(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("colour must be RRGGBB")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ipixel", description="Control iPixel Color LED panels over BLE")
    ap.add_argument("-d", "--device", default=DEFAULT_DEVICE, help="MAC, BLE address or LED_BLE_ name (env IPIXEL_DEVICE)")
    ap.add_argument("--password", help="4/6-digit panel password if set")
    ap.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv protocol frames, -vvv incl. bleak")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan")
    sub.add_parser("info")
    sub.add_parser("on")
    sub.add_parser("off")
    sub.add_parser("clear", help="delete all stored programs")

    b = sub.add_parser("brightness")
    b.add_argument("level", type=int)

    f = sub.add_parser("flip")
    f.add_argument("state", choices=["on", "off"])

    c = sub.add_parser("clock")
    c.add_argument("--style", type=int, default=1)
    c.add_argument("--12h", dest="h12", action="store_true")
    c.add_argument("--no-date", action="store_true")

    t = sub.add_parser("text")
    t.add_argument("text")
    t.add_argument("--color", type=_color, default=(255, 255, 255))
    t.add_argument("--bg", type=_color)
    t.add_argument("--effect", choices=[e.name.lower() for e in p.TextEffect], default="scroll_left")
    t.add_argument("--speed", type=int, default=p.DEFAULT_TEXT_SPEED, help="0 slow .. 100 fast")
    t.add_argument("--height", type=int, help="glyph rows (default: panel height)")
    t.add_argument("--size", type=int, help="font size in px (default: 3/4 of the height)")
    t.add_argument("--spacing", type=int, default=1, help="empty columns between characters")
    t.add_argument("--font", help="path to a TrueType/OpenType font")
    t.add_argument("--slot", type=int, default=p.SLOT_TEMPORARY)

    for name in ("image", "gif", "frame"):
        s = sub.add_parser(name)
        s.add_argument("path")
        s.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
        if name != "frame":
            s.add_argument("--slot", type=int, default=p.SLOT_TEMPORARY)

    fl = sub.add_parser("fill")
    fl.add_argument("color", type=_color)

    cd = sub.add_parser("countdown")
    cd.add_argument("minutes", type=int)
    cd.add_argument("seconds", type=int, nargs="?", default=0)

    sc = sub.add_parser("score")
    sc.add_argument("left", type=int)
    sc.add_argument("right", type=int)

    r = sub.add_parser("raw", help="send a hex frame and print replies")
    r.add_argument("hex")
    return ap


async def _run(args: argparse.Namespace) -> None:
    if args.cmd == "scan":
        for d in await scan():
            print(d.address, d.name)
        return
    if not args.device:
        raise SystemExit("--device or IPIXEL_DEVICE required")
    async with IPixel(args.device, password=args.password) as px:
        i = px.info
        match args.cmd:
            case "info":
                print(f"type 0x{i.type_byte:02x}  size {i.width}x{i.height}  block {i.block_size}  raw {i.raw.hex(' ')}")
                a = px.advert
                print(f"advert cid {a.cid} pid {a.pid}" if a else "advert: no vendor data")
                print("features " + ", ".join(f"{f.value}={'?' if v is None else v}" for f, v in px.features.items()))
            case "on" | "off":
                await px.power(args.cmd == "on")
            case "clear":
                await px.clear()
            case "brightness":
                await px.brightness(args.level)
            case "flip":
                await px.flip(args.state == "on")
            case "clock":
                await px.clock(args.style, hour_24=not args.h12, show_date=not args.no_date)
            case "text":
                await px.text(
                    args.text, args.color, args.bg, p.TextEffect[args.effect.upper()],
                    args.speed, args.height, args.font, args.slot, args.size, args.spacing,
                )
            case "image":
                await px.image(args.path, args.slot, args.fit)
            case "gif":
                await px.gif(args.path, args.slot, args.fit)
            case "frame":
                await px.frame(args.path, args.fit)
            case "fill":
                await px.fill(args.color)
            case "countdown":
                await px.countdown(args.minutes, args.seconds)
            case "score":
                await px.scoreboard(args.left, args.right)
            case "raw":
                print((await px.request(bytes.fromhex(args.hex))).hex(" "))
        await asyncio.sleep(0.3)


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose >= 3 else logging.WARNING)
    if args.verbose:
        logging.getLogger("ipixel").setLevel(logging.DEBUG if args.verbose >= 2 else logging.INFO)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
