"""Generate Diane's built-in cat avatar packs from code (D-6 interim art, v2).

v2 (2026-11): drawn at 4x and downsampled for clean anti-aliased edges, with
outlines, soft shading, a ground shadow, a breathing idle loop, a swaying
tail, perked listening ears with sound arcs, thought bubbles, a lip-flap
speaking loop, an endearing error face, a blink OVERLAY (eyelids only —
composited over the current frame) and two fidgets (ear twitch, tail flick).

The head never moves between frames: pupils are drawn by the runtime at fixed
socket coordinates, so the eyes must stay put while the body animates.

Packs written (Appendix B format, CC0):
  avatars/cat/     lavender-grey tabby
  avatars/ginger/  orange tabby

Run:  .venv/bin/python scripts/make_placeholder_cat.py
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZE = 128
S = 4  # supersampling factor
EYES = ((48, 62), (80, 62))  # socket centers, frame px — must match the manifest
EYE_RX, EYE_RY = 10, 11

PALETTES = {
    "cat": {
        "name": "Cat",
        "fur": (132, 124, 158), "fur_light": (164, 156, 190), "stripe": (100, 92, 126),
        "line": (54, 48, 74), "muzzle": (240, 234, 246), "pink": (244, 152, 178),
        "eye": (252, 252, 255), "pupil": (38, 30, 52),
    },
    "ginger": {
        "name": "Ginger",
        "fur": (232, 146, 78), "fur_light": (246, 182, 120), "stripe": (198, 108, 52),
        "line": (96, 52, 30), "muzzle": (252, 240, 224), "pink": (240, 128, 142),
        "eye": (255, 253, 248), "pupil": (52, 34, 24),
    },
}


def _s(v: float) -> int:
    return int(round(v * S))


def _box(x0, y0, x1, y1):
    return [_s(x0), _s(y0), _s(x1), _s(y1)]


class Cat:
    def __init__(self, pal: dict) -> None:
        self.p = pal

    # -- primitives (all coordinates in frame pixels) ------------------------ #

    def _outlined_ellipse(self, d, box, fill, width=1.6):
        x0, y0, x1, y1 = box
        d.ellipse(_box(x0 - width, y0 - width, x1 + width, y1 + width), fill=self.p["line"])
        d.ellipse(_box(x0, y0, x1, y1), fill=fill)

    def _ear(self, d, side: int, lift: float, flat: float, twitch: float):
        """side -1 = left, +1 = right. lift raises the tip; flat folds it outward."""
        cx = 64 + side * 24
        tip = (cx + side * (6 + flat * 10 + twitch), 14 - lift + flat * 14)
        base_in = (cx - side * 12, 44)
        base_out = (cx + side * 14, 50)
        line = self.p["line"]
        pts = [base_in, tip, base_out]
        d.polygon([(_s(x), _s(y)) for x, y in pts], fill=line)
        inner = [(x + (64 - x) * 0.0, y) for x, y in pts]
        shrink = [
            (inner[0][0] + side * 2.2, inner[0][1] + 1),
            (inner[1][0] - side * 0.4, inner[1][1] + 3.2),
            (inner[2][0] - side * 2.4, inner[2][1] + 0.5),
        ]
        d.polygon([(_s(x), _s(y)) for x, y in shrink], fill=self.p["fur"])
        pink = [
            (shrink[0][0] + side * 3, shrink[0][1] - 2),
            (shrink[1][0] - side * 0.6, shrink[1][1] + 5),
            (shrink[2][0] - side * 4, shrink[2][1] - 3),
        ]
        d.polygon([(_s(x), _s(y)) for x, y in pink], fill=self.p["pink"])

    def _tail(self, d, sway: float):
        """Thick curled tail behind the body's right side; sway in [-1, 1]."""
        pts = []
        for i in range(24):
            t = i / 23
            x = 98 + 18 * t + sway * 6 * t * t
            y = 116 - 40 * t + 10 * math.sin(t * math.pi) * (0.5 + 0.5 * sway)
            pts.append((x, y))
        for w, color in ((6.4, self.p["line"]), (4.8, self.p["fur"])):
            for x, y in pts:
                d.ellipse(_box(x - w, y - w, x + w, y + w), fill=color)
        x, y = pts[-1]  # darker tip stripe
        d.ellipse(_box(x - 4.2, y - 4.2, x + 4.2, y + 4.2), fill=self.p["stripe"])

    # -- full frame ---------------------------------------------------------- #

    def frame(
        self, *, breath=0.0, sway=0.0, ear_lift=0.0, ear_flat=0.0, twitch=0.0,
        mouth="smile", mouth_open=0.0, eyes="open", extras=(),
    ) -> Image.Image:
        img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))

        # ground shadow (blurred, separate layer)
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse(_box(24, 118, 112, 127), fill=(20, 16, 30, 70))
        img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(S * 2)))
        d = ImageDraw.Draw(img)

        self._tail(d, sway)

        # body: a loaf that swells with breath (head stays fixed — see docstring)
        b = breath * 1.6
        self._outlined_ellipse(d, (22 - b, 80 - b, 106 + b, 124), self.p["fur"])
        d.ellipse(_box(40, 92 - b, 88, 124), fill=self.p["muzzle"])  # chest
        for px in (36, 70):  # front paws
            self._outlined_ellipse(d, (px, 112, px + 22, 124), self.p["muzzle"], width=1.2)
            for t in (7, 11, 15):
                d.line([_s(px + t), _s(118), _s(px + t), _s(123)], fill=self.p["line"],
                       width=max(1, S // 2))

        # ears behind the head
        for side in (-1, 1):
            tw = twitch if side == 1 else 0.0
            self._ear(d, side, ear_lift, ear_flat, tw)

        # head
        self._outlined_ellipse(d, (24, 30, 104, 96), self.p["fur"], width=1.8)
        d.ellipse(_box(34, 34, 70, 56), fill=self.p["fur_light"])  # soft top-left sheen
        d.ellipse(_box(30, 36, 98, 94), fill=self.p["fur"])
        for dx, h in ((-8, 9), (0, 12), (8, 9)):  # forehead tabby stripes
            d.line([_s(64 + dx), _s(33), _s(64 + dx * 0.8), _s(33 + h)],
                   fill=self.p["stripe"], width=_s(2.2))
        for side in (-1, 1):  # cheek stripes
            x = 64 + side * 37
            for k in (0, 5):
                d.line([_s(x), _s(66 + k), _s(x - side * 7), _s(67 + k)],
                       fill=self.p["stripe"], width=_s(1.8))

        # muzzle + blush
        d.ellipse(_box(47, 68, 81, 90), fill=self.p["muzzle"])
        blush = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(blush)
        for cx in (36, 92):
            bd.ellipse(_box(cx - 7, 74, cx + 7, 81), fill=self.p["pink"] + (120,))
        img = Image.alpha_composite(img, blush.filter(ImageFilter.GaussianBlur(S * 1.2)))
        d = ImageDraw.Draw(img)

        self._eyes(d, eyes)

        # nose
        d.polygon([(_s(60), _s(72)), (_s(68), _s(72)), (_s(64), _s(77))], fill=self.p["pink"])
        self._mouth(d, mouth, mouth_open)

        # whiskers
        for side in (-1, 1):
            for k, dy in enumerate((-2, 2)):
                x0 = 64 + side * 14
                d.line([_s(x0), _s(80 + dy), _s(x0 + side * 22), _s(78 + dy * 2.4 + k)],
                       fill=self.p["line"], width=max(1, S // 2))

        if extras:  # own layer: translucent ink must BLEND, not overwrite fur pixels
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            for extra in extras:
                extra(ld)
            img = Image.alpha_composite(img, layer)
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    def _eyes(self, d, mode: str):
        for cx, cy in EYES:
            if mode == "x":  # error face
                for a, b in (((-5, -5), (5, 5)), ((5, -5), (-5, 5))):
                    d.line([_s(cx + a[0]), _s(cy + a[1]), _s(cx + b[0]), _s(cy + b[1])],
                           fill=self.p["line"], width=_s(2.6))
                continue
            ry = EYE_RY + (1.5 if mode == "wide" else 0)
            self._outlined_ellipse(
                d, (cx - EYE_RX, cy - ry, cx + EYE_RX, cy + ry), self.p["eye"], width=1.4
            )
            if mode == "up":  # 'thinking': gaze baked in up-right (runtime pupils hidden)
                px, py = cx + 3.5, cy - 5.5
                d.ellipse(_box(px - 4.5, py - 4.5, px + 4.5, py + 4.5), fill=self.p["pupil"])
                d.ellipse(_box(px - 2.9, py - 3.1, px - 0.1, py - 0.3), fill=(255, 255, 255))

    def _mouth(self, d, mode: str, openness: float):
        line, w = self.p["line"], _s(1.5)
        if mode == "open":
            h = 2 + openness * 7
            d.ellipse(_box(58, 78, 70, 79 + h), fill=self.p["line"])
            if h > 4:
                d.ellipse(_box(60.5, 78 + h * 0.55, 67.5, 79 + h), fill=self.p["pink"])
            return
        if mode == "o":
            d.ellipse(_box(61, 79, 67, 85), outline=line, width=w)
            return
        if mode == "flat":
            d.line([_s(59), _s(81), _s(69), _s(81)], fill=line, width=w)
            return
        if mode == "wavy":
            pts = [(_s(56 + i), _s(82 + 1.6 * math.sin(i * 0.9))) for i in range(17)]
            d.line(pts, fill=line, width=w)
            return
        # 'w' smile
        d.arc(_box(56, 74, 64, 82), 20, 160, fill=line, width=w)
        d.arc(_box(64, 74, 72, 82), 20, 160, fill=line, width=w)

    def eyelids(self, closed: float) -> Image.Image:
        """Blink overlay: ONLY the eyelids, on transparency (composited by the window)."""
        img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for cx, cy in EYES:
            top = cy - EYE_RY - 2
            bottom = top + (2 * EYE_RY + 4) * closed
            top -= 2
            bottom = top + (2 * EYE_RY + 8) * closed
            d.rectangle(_box(cx - EYE_RX - 4.5, top, cx + EYE_RX + 4.5, bottom), fill=self.p["fur"])
            if closed >= 1.0:  # closed: a happy lash curve instead of a lid edge
                d.arc(_box(cx - 7, cy - 6, cx + 7, cy + 4), 20, 160, fill=self.p["line"],
                      width=_s(2))
            else:
                d.line([_s(cx - EYE_RX - 1), _s(bottom), _s(cx + EYE_RX + 1), _s(bottom)],
                       fill=self.p["line"], width=_s(1.6))
        # clip lids to the eye area so they never paint outside the sockets
        mask = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(mask)
        for cx, cy in EYES:
            md.ellipse(_box(cx - EYE_RX - 4, cy - EYE_RY - 4, cx + EYE_RX + 4,
                            cy + EYE_RY + 4), fill=255)
        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out.resize((SIZE, SIZE), Image.LANCZOS)

    def pupil(self) -> Image.Image:
        img = Image.new("RGBA", (9 * S, 9 * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([0, 0, 9 * S - 1, 9 * S - 1], fill=self.p["pupil"])
        d.ellipse(_box(1.6, 1.4, 4.4, 4.2), fill=(255, 255, 255, 235))  # catchlight
        return img.resize((9, 9), Image.LANCZOS)


def _sound_arcs(phase: float):
    def draw(d):
        for k in range(3):
            a = max(0.0, math.sin(phase * 2 * math.pi - k * 0.9))
            if a <= 0.05:
                continue
            r = 8 + k * 6
            d.arc(_box(104 - r, 30 - r, 104 + r, 30 + r), -60, 20,
                  fill=(120, 200, 150, int(255 * a)), width=_s(2))
    return draw


def _thought(phase: int):
    def draw(d):
        for k, (x, y, r) in enumerate(((100, 34, 2.4), (108, 22, 3.4), (116, 9, 4.6))):
            if k <= phase:
                d.ellipse(_box(x - r - 1, y - r - 1, x + r + 1, y + r + 1), fill=(70, 70, 100))
                d.ellipse(_box(x - r, y - r, x + r, y + r), fill=(246, 246, 255))
    return draw


def _sweat(d):
    d.polygon([(_s(98), _s(36)), (_s(94), _s(46)), (_s(102), _s(46))], fill=(120, 180, 240))
    d.ellipse(_box(94, 42, 102, 50), fill=(120, 180, 240))


MANIFEST = """name: "{name}"
version: 2
author: "Diane built-in (code-drawn, scripts/make_placeholder_cat.py)"
frame_size: [128, 128]
anchor: bottom-center

states:
  idle:      {{anim: idle,      fps: 6,  loop: true}}
  listening: {{anim: listening, fps: 8,  loop: true}}
  thinking:  {{anim: thinking,  fps: 4,  loop: true}}
  speaking:  {{anim: speaking,  fps: 10, loop: true}}
  error:     {{anim: error,     fps: 1,  loop: false}}

fallbacks:
  speaking: idle
  thinking: idle
  listening: idle
  error: idle

overlays:
  blink: {{anim: blink, fps: 18, only_in: [idle, listening, speaking]}}
  fidgets: [fidget_ear, fidget_tail]

eyes:
  pupil: eyes/pupil.png
  sockets:
    - {{center: [{e0x}, {e0y}], radius: 4}}
    - {{center: [{e1x}, {e1y}], radius: 4}}
  hide_in: [blink, error, thinking]
"""


def build(pack: str, pal: dict) -> None:
    out = ROOT / "avatars" / pack
    if out.exists():
        shutil.rmtree(out)  # regenerate cleanly: stale extra frames would ride along
    cat = Cat(pal)

    def save(img: Image.Image, anim: str, idx: int) -> None:
        (out / anim).mkdir(parents=True, exist_ok=True)
        img.save(out / anim / f"{idx:03d}.png", optimize=True)

    n = 8
    for i in range(n):  # idle: breathing + lazy tail
        ph = 2 * math.pi * i / n
        save(cat.frame(breath=math.sin(ph), sway=math.sin(ph) * 0.6), "idle", i)
    for i in range(6):  # listening: ears up, wide eyes, sound arcs
        save(cat.frame(ear_lift=6, eyes="wide", mouth="o", sway=0.8,
                       extras=(_sound_arcs(i / 6),)), "listening", i)
    for i in range(6):  # thinking: heavy lids, bubbles rising
        save(cat.frame(eyes="up", mouth="flat", sway=-0.4 + 0.15 * (i % 2),
                       extras=(_thought(i % 3),)), "thinking", i)
    for i, o in enumerate((0.2, 0.7, 1.0, 0.5)):  # speaking: lip flaps
        save(cat.frame(mouth="open", mouth_open=o, breath=0.3 * (i % 2)), "speaking", i)
    save(cat.frame(ear_flat=1.0, eyes="x", mouth="wavy", sway=-1.0, extras=(_sweat,)),
         "error", 0)
    for i, c in enumerate((0.5, 1.0, 0.5)):
        save(cat.eyelids(c), "blink", i)
    for i, t in enumerate((0, -5, 2, -3, 0)):  # fidget: right-ear twitch
        save(cat.frame(twitch=t), "fidget_ear", i)
    for i, s in enumerate((0.0, 0.8, 1.0, 0.2, -0.8, -1.0, -0.3, 0.0)):  # tail flick
        save(cat.frame(sway=s), "fidget_tail", i)
    (out / "eyes").mkdir(parents=True, exist_ok=True)
    cat.pupil().save(out / "eyes" / "pupil.png")

    (e0x, e0y), (e1x, e1y) = EYES
    (out / "avatar.yaml").write_text(
        MANIFEST.format(name=pal["name"], e0x=e0x, e0y=e0y, e1x=e1x, e1y=e1y)
    )
    (out / "LICENSE").write_text(
        "Built-in art generated by scripts/make_placeholder_cat.py in this repo.\n"
        "CC0 1.0 / public domain. Replace with owner-chosen art per Appendix B.\n"
    )
    print(f"wrote pack: {out}")


def main() -> None:
    for pack, pal in PALETTES.items():
        build(pack, pal)


if __name__ == "__main__":
    main()
