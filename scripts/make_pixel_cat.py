"""Generate Diane's original pixel-art cat pack, "Mochi" (CC0).

An original character: a round white cat with small rounded ears, a tail
wrapped around the front, rosy cheeks, curled up in a pink basket bed. It
sleeps while Diane is idle and wakes up when she's active.

Drawn on a 48x48 art grid (no anti-aliasing, automatic 1-px outlines), then
upscaled x3 nearest-neighbour; the manifest's `pixel_grid: 3` tells the
window to render it crisp and move the pupils in whole art-pixels.

The head never moves between frames: pupils are drawn by the runtime at fixed
socket coordinates.

Run:  .venv/bin/python scripts/make_pixel_cat.py   -> avatars/pixelcat/
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "avatars" / "pixelcat"
G = 48       # art grid for the cat itself
W = 56       # full frame width: the cat plus room on the right for z's / bubbles
OX = 2       # where the cat sits inside the wider frame
SCALE = 3    # screen px per art px
EYES = ((15, 22), (31, 22))  # art-px eye centres in CAT space (2x3 awake eyes)

INK = (24, 22, 34, 255)
WHITE = (252, 252, 255, 255)
SHADE = (204, 214, 242, 255)
PINK = (255, 156, 186, 255)
BLUSH = (255, 196, 214, 255)
BED = (255, 186, 210, 255)
BED_DARK = (236, 128, 168, 255)
BED_LIGHT = (255, 228, 238, 255)
BLUE = (110, 170, 245, 255)
LILAC = (170, 150, 240, 255)


class Canvas:
    def __init__(self, width: int = G) -> None:
        self.w = width
        self.px = np.zeros((G, width, 4), dtype=np.uint8)

    def put(self, pts, color) -> None:
        for x, y in pts:
            if 0 <= x < self.w and 0 <= y < G:
                self.px[y, x] = color

    def stamp(self, mask: np.ndarray, color, outline=True) -> None:
        """Fill a mask; outline it with 1-px ink (4-neighbour) over what's beneath."""
        if outline:
            grown = mask.copy()
            grown[1:, :] |= mask[:-1, :]
            grown[:-1, :] |= mask[1:, :]
            grown[:, 1:] |= mask[:, :-1]
            grown[:, :-1] |= mask[:, 1:]
            self.px[grown & ~mask] = INK
        self.px[mask] = color

    def image(self) -> Image.Image:
        img = Image.fromarray(self.px, "RGBA")
        return img.resize((self.w * SCALE, G * SCALE), Image.NEAREST)


def frame_of(cat: Canvas) -> Canvas:
    """Place the 48-wide cat into the 56-wide frame canvas."""
    big = Canvas(W)
    big.px[:, OX:OX + G] = cat.px
    return big


def mask(draw_fn, width: int = G) -> np.ndarray:
    m = Image.new("1", (width, G), 0)
    draw_fn(ImageDraw.Draw(m))
    return np.array(m, dtype=bool)


# --------------------------------------------------------------------------- #
# parts
# --------------------------------------------------------------------------- #

def bed_back(c: Canvas) -> None:
    c.stamp(mask(lambda d: d.ellipse([2, 31, 45, 46], fill=1)), BED_DARK)
    c.stamp(mask(lambda d: d.ellipse([5, 32, 42, 41], fill=1)), BED, outline=False)


def bed_front(c: Canvas) -> None:
    """Front rim drawn over the cat so it looks nestled in."""
    rim = mask(lambda d: d.ellipse([2, 31, 45, 46], fill=1))
    inner = mask(lambda d: d.ellipse([5, 30, 42, 40], fill=1))
    front = rim & ~inner
    front[:37, :] = False
    c.stamp(front, BED_DARK)
    c.put([(x, 41) for x in range(10, 38, 2)], BED_LIGHT)  # woven stitches


def body(c: Canvas, breath: int) -> None:
    m = mask(lambda d: d.ellipse([7, 23 - breath, 41, 42], fill=1))
    c.stamp(m, WHITE)
    c.put([(x, 39) for x in range(12, 22)] + [(x, 38) for x in range(11, 15)], SHADE)


def tail(c: Canvas, flick: int) -> None:
    """Curled tail tip peeking over the front of the bed (the rest hides inside)."""
    def draw(d):
        d.ellipse([36 + flick, 32 - flick, 42 + flick, 40], fill=1)  # tip curls up

    c.stamp(mask(draw), WHITE)
    c.put([(38 + flick, 35 - flick), (39 + flick, 35 - flick), (39 + flick, 36)], SHADE)


def head(c: Canvas, ear_up: int, ear_flat: int, twitch: int) -> None:
    """Wide head with a flat top between the ears; pointed ears at the corners."""
    lift = ear_up - ear_flat

    def draw(d):
        d.rounded_rectangle([7, 12, 40, 32], radius=8, fill=1)
        d.polygon([(7, 19), (9 - ear_flat * 2, 4 - lift), (18, 12)], fill=1)
        d.polygon([(29, 12), (38 + ear_flat * 2, 4 - lift - twitch), (40, 19)], fill=1)

    c.stamp(mask(draw), WHITE)
    lx, rx = 10 - ear_flat * 2, 37 + ear_flat * 2
    c.put([(lx, 8 - lift), (lx, 9 - lift), (lx + 1, 10 - lift), (lx + 1, 9 - lift)], PINK)
    c.put([(rx, 8 - lift - twitch), (rx, 9 - lift - twitch), (rx - 1, 10 - lift - twitch),
           (rx - 1, 9 - lift - twitch)], PINK)
    c.put([(x, 30) for x in range(12, 18)] + [(11, 29)], SHADE)  # chin shade
    # Soften the long chin outline where the head sits on the body: a harsh ink
    # line there reads as a belt. A light shadow separates head and body instead.
    for x in range(12, 36):
        for y in (32, 33):
            if tuple(c.px[y, x]) == INK:
                c.px[y, x] = SHADE


def cheeks(c: Canvas) -> None:
    c.put([(10, 25), (11, 25), (12, 25), (35, 25), (36, 25), (37, 25)], BLUSH)


def nose_mouth(c: Canvas, mouth: str, level: int = 0) -> None:
    c.put([(23, 23), (24, 23)], PINK)
    if mouth == "smile":  # small cat-smile under the nose
        c.put([(22, 25), (23, 24), (24, 24), (25, 25)], INK)
    elif mouth == "open":
        h = 1 + level
        c.put([(x, y) for x in (23, 24) for y in range(24, 24 + h + 1)], INK)
        if h >= 2:
            c.put([(23, 24 + h), (24, 24 + h)], PINK)
    elif mouth == "o":
        c.put([(23, 24), (24, 24), (22, 25), (25, 25), (23, 26), (24, 26)], INK)
    elif mouth == "flat":
        c.put([(22, 25), (23, 25), (24, 25), (25, 25)], INK)
    elif mouth == "wavy":
        c.put([(21, 26), (22, 25), (23, 26), (24, 25), (25, 26), (26, 25)], INK)


def eyes(c: Canvas, mode: str) -> None:
    for ex, ey in EYES:
        if mode == "sleep":  # long, gentle closed-eye lashes
            c.put([(ex - 2, ey), (ex - 1, ey + 1), (ex, ey + 1), (ex + 1, ey + 1),
                   (ex + 2, ey + 1), (ex + 3, ey)], INK)
        elif mode == "up":  # thinking: gaze baked in, looking up
            c.put([(ex, ey - 2), (ex + 1, ey - 2), (ex, ey - 1), (ex + 1, ey - 1),
                   (ex, ey), (ex + 1, ey)], INK)
            c.put([(ex + 1, ey - 2)], WHITE)
        elif mode == "dizzy":  # x eyes
            c.put([(ex - 1, ey - 1), (ex, ey), (ex + 1, ey + 1), (ex + 2, ey + 2),
                   (ex + 2, ey - 1), (ex + 1, ey), (ex, ey + 1), (ex - 1, ey + 2)], INK)
        # "open": nothing here — the runtime draws the tracking pupils


def zzz(c: Canvas, phase: int) -> None:
    """Small z and big Z drifting up from the right ear."""
    small = [(0, 0), (1, 0), (2, 0), (1, 1), (0, 2), (1, 2), (2, 2)]
    big = [(0, 0), (1, 0), (2, 0), (3, 0), (2, 1), (1, 2), (0, 3), (1, 3), (2, 3), (3, 3)]
    sy = 12 - phase
    by = 5 - phase
    if phase < 4:
        c.put([(44 + x, sy + y) for x, y in small], LILAC)
    if 1 <= phase:
        c.put([(49 + x, by + y + 2) for x, y in big], LILAC)


def bang(c: Canvas, on: bool) -> None:
    if on:
        c.put([(46, y) for y in range(6, 11)] + [(46, 12)], (255, 186, 70, 255))


def thought(c: Canvas, phase: int) -> None:
    def bubble(d):
        d.ellipse([43, 0, 55, 8], fill=1)
        d.ellipse([43, 10, 45, 12], fill=1)

    c.stamp(mask(bubble, W), WHITE)
    for k, x in enumerate((45, 48, 51)):
        c.put([(x, 3), (x + 1, 3), (x, 4), (x + 1, 4)], INK if k <= phase else SHADE)


def sweat(c: Canvas) -> None:
    c.put([(45, 14), (44, 15), (45, 15), (44, 16), (45, 16), (46, 16)], BLUE)


def compose(*, breath=0, flick=0, ear_up=0, ear_flat=0, twitch=0, eye="sleep",
            mouth="smile", level=0, extras=()) -> Image.Image:
    c = Canvas()
    bed_back(c)
    body(c, breath)
    tail(c, flick)
    head(c, ear_up, ear_flat, twitch)
    cheeks(c)
    eyes(c, eye)
    nose_mouth(c, mouth, level)
    bed_front(c)
    f = frame_of(c)
    for fn in extras:
        fn(f)
    return f.image()


def blink_overlay(closed: bool) -> Image.Image:
    """Eyelids only, on transparency (the window composites it over the frame)."""
    c = Canvas()
    for ex, ey in EYES:
        patch = [(x, y) for x in range(ex - 2, ex + 4) for y in range(ey - 2, ey + 3)]
        c.put(patch, WHITE)
        if closed:
            c.put([(ex - 2, ey), (ex - 1, ey + 1), (ex, ey + 1), (ex + 1, ey + 1),
                   (ex + 2, ey + 1), (ex + 3, ey)], INK)
        else:
            c.put([(ex, ey), (ex + 1, ey)], INK)
    return frame_of(c).image()


def pupil() -> Image.Image:
    """The awake eye: 3x4 art-px, ink with a 1-px catchlight (bright, readable)."""
    p = np.zeros((4, 3, 4), dtype=np.uint8)
    p[:, :] = INK
    p[0, 2] = (255, 255, 255, 255)  # catchlight, top-right
    p[1, 2] = (255, 255, 255, 255)
    return Image.fromarray(p, "RGBA").resize((3 * SCALE, 4 * SCALE), Image.NEAREST)


def _socket(ex: int, ey: int) -> tuple[int, int]:
    """Screen-px centre such that the 3x4 eye's top-left snaps to art cell (ex-1, ey-2)."""
    return ((ex - 1 + OX) * SCALE + 4, (ey - 2) * SCALE + 6)


MANIFEST = """name: "{name}"
version: 1
author: "Diane built-in (code-drawn pixel art, scripts/make_pixel_cat.py)"
frame_size: [{fw}, {fh}]
anchor: bottom-center
pixel_grid: {scale}

states:
  idle:      {{anim: idle,      fps: 3,  loop: true}}
  listening: {{anim: listening, fps: 4,  loop: true}}
  thinking:  {{anim: thinking,  fps: 3,  loop: true}}
  speaking:  {{anim: speaking,  fps: 8,  loop: true}}
  error:     {{anim: error,     fps: 1,  loop: false}}

fallbacks:
  speaking: idle
  thinking: idle
  listening: idle
  error: idle

overlays:
  blink: {{anim: blink, fps: 10, only_in: [listening, speaking]}}
  fidgets: [fidget_ear, fidget_tail]

eyes:
  pupil: eyes/pupil.png
  sockets:
    - {{center: [{s0x}, {s0y}], radius: {r}}}
    - {{center: [{s1x}, {s1y}], radius: {r}}}
  hide_in: [idle, fidget_ear, fidget_tail, blink, thinking, error]
"""


# pack dir -> (display name, body colour, body shade). Same character, three coats.
VARIANTS = {
    "pixelcat": ("Mochi", (252, 252, 255, 255), (204, 214, 242, 255)),
    "pixelcat-grey": ("Mochi (grey)", (200, 202, 214, 255), (156, 160, 182, 255)),
    "pixelcat-peach": ("Mochi (peach)", (255, 228, 204, 255), (246, 194, 162, 255)),
}


def main() -> None:
    global OUT, WHITE, SHADE
    for pack, (name, body_col, shade_col) in VARIANTS.items():
        OUT, WHITE, SHADE = ROOT / "avatars" / pack, body_col, shade_col
        build(name)


def build(name: str) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)

    def save(img: Image.Image, anim: str, idx: int) -> None:
        (OUT / anim).mkdir(parents=True, exist_ok=True)
        img.save(OUT / anim / f"{idx:03d}.png", optimize=True)

    # idle: asleep — breathing + z's drifting up
    for i in range(6):
        save(compose(breath=1 if i % 3 else 0, extras=(lambda c, i=i: zzz(c, i % 5),)),
             "idle", i)
    # listening: awake, ears perked, "!" pops
    for i in range(4):
        save(compose(ear_up=1, eye="open", mouth="o", flick=1 if i % 2 else 0,
                     extras=(lambda c, i=i: bang(c, i < 2),)), "listening", i)
    # thinking: looking up at a thought bubble filling with dots
    for i in range(6):
        save(compose(eye="up", mouth="flat", flick=(i % 3) - 1,
                     extras=(lambda c, i=i: thought(c, i % 3),)), "thinking", i)
    # speaking: mouth flaps
    for i, lv in enumerate((0, 2, 1, 2)):
        save(compose(eye="open", mouth="open", level=lv, breath=i % 2), "speaking", i)
    # error: dizzy, ears flat, sweat drop
    save(compose(eye="dizzy", mouth="wavy", ear_flat=1, extras=(sweat,)), "error", 0)
    # blink overlay (awake states only)
    for i, closed in enumerate((False, True, False)):
        save(blink_overlay(closed), "blink", i)
    # fidgets while asleep
    for i, t in enumerate((0, 2, 0, 2, 0)):
        save(compose(twitch=t, extras=(lambda c: zzz(c, 2),)), "fidget_ear", i)
    for i, f in enumerate((0, 1, 2, 1, 0, 1, 0)):
        save(compose(flick=f, extras=(lambda c: zzz(c, 2),)), "fidget_tail", i)

    (OUT / "eyes").mkdir(parents=True, exist_ok=True)
    pupil().save(OUT / "eyes" / "pupil.png")
    (s0x, s0y), (s1x, s1y) = (_socket(*e) for e in EYES)
    (OUT / "avatar.yaml").write_text(MANIFEST.format(
        name=name, fw=W * SCALE, fh=G * SCALE, scale=SCALE, s0x=s0x, s0y=s0y, s1x=s1x, s1y=s1y, r=SCALE))
    (OUT / "LICENSE").write_text(
        "Original character 'Mochi', generated by scripts/make_pixel_cat.py in this repo.\n"
        "CC0 1.0 / public domain.\n"
    )
    print(f"wrote pack: {OUT}")


if __name__ == "__main__":
    main()
