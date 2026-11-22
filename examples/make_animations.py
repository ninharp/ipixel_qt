"""Generate the example animations in examples/animations for a 64x16 panel.

    uv run python examples/make_animations.py [output-dir]
"""

import colorsys
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from ipixel import render
from ipixel.studio.model import Animation

W, H = 64, 16
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "animations"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(7)

# Animations whose last frame does not lead into the first get a crossfaded "-loop" version.
LOOPS = {"feuer": 10, "matrix-regen": 8, "sternenfeld": 10, "equalizer": 6, "feuerwerk": 10}


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def save(name, frames, ms):
    (OUT / name).write_bytes(render.encode_gif(frames, [ms] * len(frames)))


def blank():
    return Image.new("RGB", (W, H))


def fire():
    pal = [(0, 0, 0)] + [(min(255, i * 12), min(255, max(0, i * 12 - 120)), max(0, i * 12 - 280)) for i in range(1, 36)]
    heat = [[0] * W for _ in range(H)]
    frames = []
    for step in range(70):
        for x in range(W):
            heat[H - 1][x] = 35 if random.random() < 0.75 else 20
        for y in range(H - 1):
            for x in range(W):
                src = heat[y + 1][(x + random.randint(-1, 1)) % W]
                heat[y][x] = max(0, src - random.randint(0, 4))
        if step >= 30:
            img = blank()
            px = img.load()
            for y in range(H):
                for x in range(W):
                    px[x, y] = pal[min(35, heat[y][x])]
            frames.append(img)
    save("feuer.gif", frames, 60)


def plasma():
    frames = []
    for t in range(36):
        img = blank()
        px = img.load()
        a = t / 36 * 2 * math.pi
        for y in range(H):
            for x in range(W):
                v = (math.sin(x / 6 + a) + math.sin((y / 3 + a) / 1.3) + math.sin((x + y) / 8 + a * 2)
                     + math.sin(math.hypot(x - 32, y - 8) / 4 - a))
                px[x, y] = hsv(v / 8 + t / 36, 1, 0.9)
        frames.append(img)
    save("plasma.gif", frames, 70)


def rainbow_wave():
    frames = []
    for t in range(32):
        img = blank()
        d = ImageDraw.Draw(img)
        for x in range(W):
            y = 7.5 + 6 * math.sin((x / W * 2 + t / 32) * 2 * math.pi)
            c = hsv(x / W + t / 32)
            d.line([(x, round(y)), (x, H - 1)], fill=tuple(v // 4 for v in c))
            d.point((x, round(y)), fill=c)
            d.point((x, round(y) + 1), fill=c)
        frames.append(img)
    save("regenbogen-welle.gif", frames, 60)


def matrix_rain():
    drops = [[random.randint(-H, H), random.randint(4, 10), random.choice([1, 1, 2])] for _ in range(W)]
    frames = []
    for t in range(48):
        img = blank()
        px = img.load()
        for x, (y, length, speed) in enumerate(drops):
            for k in range(length):
                yy = y - k
                if 0 <= yy < H:
                    v = 255 if k == 0 else max(40, 200 - k * 25)
                    px[x, yy] = (180, 255, 180) if k == 0 else (0, v, 0)
            if t % speed == 0:
                drops[x][0] += 1
            if drops[x][0] - length > H:
                drops[x] = [random.randint(-6, 0), random.randint(4, 10), random.choice([1, 1, 2])]
        frames.append(img)
    save("matrix-regen.gif", frames, 70)


def starfield():
    stars = [[random.uniform(0, W), random.randint(0, H - 1), random.choice([0.5, 1, 2])] for _ in range(40)]
    frames = []
    for _ in range(64):
        img = blank()
        px = img.load()
        for s in stars:
            v = {0.5: 70, 1: 150, 2: 255}[s[2]]
            px[int(s[0]) % W, s[1]] = (v, v, min(255, v + 30))
            s[0] -= s[2]
            if s[0] < 0:
                s[0] += W
                s[1] = random.randint(0, H - 1)
        frames.append(img)
    save("sternenfeld.gif", frames, 50)


def pacman():
    frames = []
    for t in range(0, W + 28, 2):
        img = blank()
        d = ImageDraw.Draw(img)
        x = t - 14
        for dx in range(4, W, 8):
            if dx > x + 6:
                d.rectangle([dx, 7, dx + 1, 8], fill=(255, 200, 160))
        mouth = 35 if (t // 2) % 2 == 0 else 5
        d.pieslice([x, 1, x + 13, 14], mouth, 360 - mouth, fill=(255, 220, 0))
        gx = x - 20
        d.chord([gx, 1, gx + 12, 13], 180, 360, fill=(255, 60, 60))
        d.rectangle([gx, 7, gx + 12, 13], fill=(255, 60, 60))
        for k in range(3):
            d.point((gx + 2 + k * 4, 14), fill=(255, 60, 60))
        d.rectangle([gx + 3, 5, gx + 4, 7], fill=(255, 255, 255))
        d.rectangle([gx + 8, 5, gx + 9, 7], fill=(255, 255, 255))
        d.point((gx + 4, 6), fill=(0, 0, 200))
        d.point((gx + 9, 6), fill=(0, 0, 200))
        frames.append(img)
    save("pacman.gif", frames, 60)


HEART = ["..##...##..", ".####.####.", "###########", "###########", ".#########.", "..#######..",
         "...#####...", "....###....", ".....#....."]


def heart():
    frames = []
    for t in range(16):
        img = blank()
        scale = 1.0 + 0.18 * max(0.0, math.sin(t / 16 * 2 * math.pi * 2))
        base = Image.new("RGB", (11, 9))
        bp = base.load()
        for y, row in enumerate(HEART):
            for x, ch in enumerate(row):
                if ch == "#":
                    bp[x, y] = (255, 30, 60)
        size = (round(11 * scale), round(9 * scale))
        h = base.resize(size, Image.Resampling.NEAREST)
        for cx in (10, 32, 54):
            img.paste(h, (cx - size[0] // 2, 8 - size[1] // 2))
        frames.append(img)
    save("herz-pulsierend.gif", frames, 80)


def equalizer():
    bars = 16
    levels = [random.uniform(2, 14) for _ in range(bars)]
    frames = []
    for _ in range(40):
        img = blank()
        d = ImageDraw.Draw(img)
        for i in range(bars):
            levels[i] = max(1, min(16, levels[i] + random.uniform(-4, 4)))
            top = H - round(levels[i])
            for y in range(top, H):
                d.line([(i * 4, y), (i * 4 + 2, y)], fill=hsv(0.33 - 0.33 * (H - y) / H))
        frames.append(img)
    save("equalizer.gif", frames, 90)


def fireworks():
    frames = []
    particles = []
    for t in range(72):
        if t % 12 == 0:
            cx, cy, hue = random.randint(8, W - 8), random.randint(4, 10), random.random()
            for k in range(18):
                ang = k / 18 * 2 * math.pi
                sp = random.uniform(0.6, 1.4)
                particles.append([cx, cy, math.cos(ang) * sp, math.sin(ang) * sp * 0.6, 14, hue])
        img = blank()
        px = img.load()
        for p in particles:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += 0.06
            p[4] -= 1
            if p[4] > 0 and 0 <= p[0] < W and 0 <= p[1] < H:
                px[int(p[0]), int(p[1])] = hsv(p[5], 0.8, p[4] / 14)
        particles = [p for p in particles if p[4] > 0]
        frames.append(img)
    save("feuerwerk.gif", frames[12:], 60)


def scanner():
    bar, trail, step = 6, 6, 2
    right = list(range(0, W - bar + 1, step))
    path = [(x, +1) for x in right] + [(x, -1) for x in right[-2:0:-1]]
    frames = []
    for x, direction in path:
        img = blank()
        d = ImageDraw.Draw(img)
        for k in range(trail - 1, -1, -1):  # tail behind the bar, bar drawn last
            tx = x - direction * k * 2
            d.rectangle([tx, 6, tx + bar - 1, 9], fill=(255 >> k, 0, 0))
        frames.append(img)
    save("lauflicht-rot.gif", frames, 30)


def snake():
    path = [(x, 2) for x in range(2, 62)] + [(61, y) for y in range(3, 14)] + \
           [(x, 13) for x in range(60, 1, -1)] + [(2, y) for y in range(12, 2, -1)]
    frames = []
    for i in range(0, len(path), 2):
        img = blank()
        px = img.load()
        for k in range(14):
            x, y = path[(i - k) % len(path)]
            px[x, y] = (0, 255 - k * 12, 80) if k else (180, 255, 120)
        frames.append(img)
    save("schlange.gif", frames, 40)


def smiley_png():
    img = blank()
    d = ImageDraw.Draw(img)
    for cx in (12, 32, 52):
        d.ellipse([cx - 7, 1, cx + 7, 14], fill=(255, 210, 0))
        d.rectangle([cx - 3, 5, cx - 2, 6], fill=(0, 0, 0))
        d.rectangle([cx + 2, 5, cx + 3, 6], fill=(0, 0, 0))
        d.arc([cx - 4, 5, cx + 4, 11], 20, 160, fill=(0, 0, 0))
    img.save(OUT / "smileys.png")


for fn in (fire, plasma, rainbow_wave, matrix_rain, starfield, pacman, heart, equalizer, fireworks,
           scanner, snake, smiley_png):
    fn()

for name, overlap in LOOPS.items():
    anim = Animation(W, H)
    anim.load(OUT / f"{name}.gif")
    anim.crossfade_loop(overlap)
    anim.save(OUT / f"{name}-loop.gif")

for f in sorted(OUT.iterdir()):
    img = Image.open(f)
    print(f"{f.name:24} {getattr(img, 'n_frames', 1):3} Bilder  {f.stat().st_size / 1024:5.1f} KB")
