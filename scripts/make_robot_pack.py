"""Generate Diane's built-in original robot avatar pack, "Bolt" (CC0).

An original design: a retro rounded TV-head with a glowing screen face, an
antenna bulb whose colour follows the assistant state, stubby arms, and a
hovering body with a thruster glow. Drawn at 4x and downsampled.

The head never moves between frames: pupils are drawn by the runtime at fixed
socket coordinates, so the screen-eyes must stay put while the rest animates.

Run:  .venv/bin/python scripts/make_robot_pack.py   -> avatars/bolt/
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "avatars" / "bolt"
SIZE = 128
S = 4
EYES = ((50, 48), (78, 48))   # socket centers (frame px) — must match the manifest
EYE_W, EYE_H = 9, 9          # half-extents of the glowing eye shapes

C = {
    "line": (40, 44, 64), "shell": (236, 168, 72), "shell_hi": (250, 204, 128),
    "shell_lo": (204, 132, 52), "metal": (176, 186, 204), "metal_lo": (132, 142, 164),
    "screen": (26, 34, 52), "glow": (110, 236, 230), "pupil": (236, 255, 252),
    "idle": (110, 236, 230), "listening": (120, 236, 140), "thinking": (150, 150, 255),
    "speaking": (255, 214, 110), "error": (255, 96, 96),
}


def _s(v: float) -> int:
    return int(round(v * S))


def _box(x0, y0, x1, y1):
    return [_s(x0), _s(y0), _s(x1), _s(y1)]


def _rrect(d, box, r, fill, outline=None, width=1.6):
    x0, y0, x1, y1 = box
    if outline is not None:
        d.rounded_rectangle(_box(x0 - width, y0 - width, x1 + width, y1 + width),
                            radius=_s(r + width), fill=outline)
    d.rounded_rectangle(_box(x0, y0, x1, y1), radius=_s(r), fill=fill)


def _glow_layer(size, draw_fn, blur):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def frame(*, state="idle", hover=0.0, antenna_tilt=0.0, arm_l=0.0, arm_r=0.0,
          eyes="open", mouth="smile", level=0.0, extras=()) -> Image.Image:
    img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
    light = C[state]
    hy = hover * 1.5  # body bob in px (head stays fixed)

    # ground shadow + thruster glow
    img = Image.alpha_composite(img, _glow_layer(
        img.size, lambda d: d.ellipse(_box(40, 119, 88, 126), fill=(20, 20, 40, 80)), S * 2))
    img = Image.alpha_composite(img, _glow_layer(
        img.size, lambda d: d.ellipse(_box(54, 108 + hy, 74, 120 + hy),
                                      fill=(255, 170, 80, 150 - int(hover * 30))), S * 2.5))
    d = ImageDraw.Draw(img)

    # arms (behind body): rounded capsules angled by arm_l / arm_r
    for side, ang in ((-1, arm_l), (1, arm_r)):
        sx, sy = 64 + side * 22, 88 + hy
        a = math.radians(20 + ang * 50)
        ex, ey = sx + side * 12 * math.cos(a), sy + 12 * math.sin(a) - ang * 6
        for w, col in ((5.4, C["line"]), (3.8, C["metal"])):
            for t in range(11):
                x = sx + (ex - sx) * t / 10
                y = sy + (ey - sy) * t / 10
                d.ellipse(_box(x - w, y - w, x + w, y + w), fill=col)
        d.ellipse(_box(ex - 5, ey - 5, ex + 5, ey + 5), fill=C["line"])
        d.ellipse(_box(ex - 3.6, ey - 3.6, ex + 3.6, ey + 3.6), fill=C["shell"])

    # body: rounded barrel with a chest light
    _rrect(d, (44, 80 + hy, 84, 110 + hy), 12, C["shell"], C["line"])
    d.rounded_rectangle(_box(47, 83 + hy, 60, 107 + hy), radius=_s(8), fill=C["shell_hi"])
    d.ellipse(_box(59, 90 + hy, 69, 100 + hy), fill=C["line"])
    d.ellipse(_box(60.5, 91.5 + hy, 67.5, 98.5 + hy), fill=light)
    # thruster nozzle
    d.polygon([(_s(56), _s(109 + hy)), (_s(72), _s(109 + hy)), (_s(69), _s(115 + hy)),
               (_s(59), _s(115 + hy))], fill=C["metal_lo"])

    # neck
    d.rectangle(_box(58, 74, 70, 82), fill=C["metal_lo"])

    # antenna (behind head top) — tilts for the fidget
    ax0, ay0 = 64, 24
    tip = (ax0 + antenna_tilt * 8, 8 + abs(antenna_tilt) * 2)
    d.line([_s(ax0), _s(ay0), _s(tip[0]), _s(tip[1])], fill=C["line"], width=_s(2.6))
    img = Image.alpha_composite(img, _glow_layer(
        img.size, lambda g: g.ellipse(_box(tip[0] - 8, tip[1] - 8, tip[0] + 8, tip[1] + 8),
                                      fill=light + (150,)), S * 2))
    d = ImageDraw.Draw(img)
    d.ellipse(_box(tip[0] - 4.6, tip[1] - 4.6, tip[0] + 4.6, tip[1] + 4.6), fill=C["line"])
    d.ellipse(_box(tip[0] - 3.2, tip[1] - 3.2, tip[0] + 3.2, tip[1] + 3.2), fill=light)

    # head: rounded TV with side "ear" bolts and a screen face
    for side in (-1, 1):
        x = 64 + side * 38
        d.rounded_rectangle(_box(x - 5, 42, x + 5, 62), radius=_s(3), fill=C["line"])
        d.rounded_rectangle(_box(x - 3.6, 43.4, x + 3.6, 60.6), radius=_s(2), fill=C["metal"])
    _rrect(d, (28, 24, 100, 78), 16, C["shell"], C["line"], width=1.8)
    d.rounded_rectangle(_box(31, 27, 70, 40), radius=_s(7), fill=C["shell_hi"])  # sheen
    d.rounded_rectangle(_box(30, 66, 98, 76), radius=_s(8), fill=C["shell_lo"])
    _rrect(d, (35, 32, 93, 72), 11, C["screen"], C["line"], width=1.2)

    # face on the screen (glow layer for that CRT look)
    face = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    _eyes(fd, eyes)
    _mouth(fd, mouth, level)
    img = Image.alpha_composite(img, face.filter(ImageFilter.GaussianBlur(S * 1.6)))
    img = Image.alpha_composite(img, face)
    d = ImageDraw.Draw(img)
    # screen glare
    d.line([_s(40), _s(37), _s(48), _s(35)], fill=(255, 255, 255), width=_s(1.2))

    if extras:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for fn in extras:
            fn(ld)
        img = Image.alpha_composite(img, layer)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def _eyes(d, mode):
    g = C["glow"]
    for cx, cy in EYES:
        if mode == "x":
            for a, b in (((-5, -5), (5, 5)), ((5, -5), (-5, 5))):
                d.line([_s(cx + a[0]), _s(cy + a[1]), _s(cx + b[0]), _s(cy + b[1])],
                       fill=C["error"], width=_s(2.8))
            continue
        h = EYE_H + (1.5 if mode == "wide" else 0)
        # dim eye "socket" plate; the bright pupil is drawn by the runtime
        d.rounded_rectangle(_box(cx - EYE_W, cy - h, cx + EYE_W, cy + h), radius=_s(6),
                            fill=(g[0] // 3, g[1] // 3 + 10, g[2] // 3 + 14))
        d.rounded_rectangle(_box(cx - EYE_W, cy - h, cx + EYE_W, cy + h), radius=_s(6),
                            outline=g, width=_s(1.4))
        if mode == "up":  # thinking: gaze baked in (runtime pupils hidden)
            px, py = cx + 3, cy - 4.5
            d.ellipse(_box(px - 4, py - 4, px + 4, py + 4), fill=C["pupil"])


def _mouth(d, mode, level):
    g = C["glow"]
    if mode == "smile":
        d.arc(_box(56, 57, 72, 68), 25, 155, fill=g, width=_s(1.8))
    elif mode == "o":
        d.ellipse(_box(61, 62, 67, 68), outline=g, width=_s(1.6))
    elif mode == "flat":
        d.line([_s(58), _s(65), _s(70), _s(65)], fill=g, width=_s(1.8))
    elif mode == "bars":  # speaking: equaliser
        for i, x in enumerate((55, 60, 64.5, 69, 74)):
            h = 0.9 + level * (1.4 + 1.8 * math.sin(i * 1.3 + level * 4) ** 2)
            d.rounded_rectangle(_box(x - 1.3, 65.5 - h, x + 1.3, 65.5 + h), radius=_s(1.2), fill=g)
    elif mode == "wavy":
        pts = [(_s(55 + i), _s(65 + 1.6 * math.sin(i * 0.9))) for i in range(19)]
        d.line(pts, fill=C["error"], width=_s(1.6))


def eyelids(closed: float) -> Image.Image:
    """Blink overlay: screen-coloured shutters over the eyes only (composited)."""
    img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for cx, cy in EYES:
        top = cy - EYE_H - 2.5
        bottom = top + (2 * EYE_H + 5) * closed
        d.rectangle(_box(cx - EYE_W - 2.5, top, cx + EYE_W + 2.5, bottom), fill=C["screen"])
        if closed >= 1.0:
            d.line([_s(cx - EYE_W + 1), _s(cy + 1), _s(cx + EYE_W - 1), _s(cy + 1)],
                   fill=C["glow"], width=_s(1.8))
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def pupil() -> Image.Image:
    img = Image.new("RGBA", (9 * S, 9 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 9 * S - 1, 9 * S - 1], radius=_s(3), fill=C["pupil"] + (255,))
    return img.resize((9, 9), Image.LANCZOS)


def _waves(phase):
    def draw(d):
        for k in range(3):
            a = max(0.0, math.sin(phase * 2 * math.pi - k * 0.9))
            if a > 0.05:
                r = 7 + k * 6
                d.arc(_box(104 - r, 48 - r, 104 + r, 48 + r), -50, 50,
                      fill=C["listening"] + (int(255 * a),), width=_s(2))
    return draw


def _dots(phase):
    def draw(d):
        for k in range(3):
            on = k == phase
            x = 108 + k * 0  # stacked rising dots beside the head
            y = 30 - k * 8
            r = 2.4 + k * 0.8
            col = C["thinking"] + ((255,) if on else (110,))
            d.ellipse(_box(x - r, y - r, x + r, y + r), fill=col)
    return draw


def _sparks(d):
    for x0, y0, x1, y1 in ((100, 24, 108, 18), (104, 30, 112, 30), (20, 26, 14, 20)):
        d.line([_s(x0), _s(y0), _s(x1), _s(y1)], fill=(255, 220, 90, 255), width=_s(1.6))


MANIFEST = f"""name: "Bolt"
version: 1
author: "Diane built-in (code-drawn, scripts/make_robot_pack.py)"
frame_size: [128, 128]
anchor: bottom-center

states:
  idle:      {{anim: idle,      fps: 6,  loop: true}}
  listening: {{anim: listening, fps: 8,  loop: true}}
  thinking:  {{anim: thinking,  fps: 4,  loop: true}}
  speaking:  {{anim: speaking,  fps: 12, loop: true}}
  error:     {{anim: error,     fps: 1,  loop: false}}

fallbacks:
  speaking: idle
  thinking: idle
  listening: idle
  error: idle

overlays:
  blink: {{anim: blink, fps: 18, only_in: [idle, listening, speaking]}}
  fidgets: [fidget_antenna, fidget_wave]

eyes:
  pupil: eyes/pupil.png
  sockets:
    - {{center: [{EYES[0][0]}, {EYES[0][1]}], radius: 4}}
    - {{center: [{EYES[1][0]}, {EYES[1][1]}], radius: 4}}
  hide_in: [blink, error, thinking]
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)

    def save(img, anim, idx):
        (OUT / anim).mkdir(parents=True, exist_ok=True)
        img.save(OUT / anim / f"{idx:03d}.png", optimize=True)

    for i in range(8):
        save(frame(hover=math.sin(2 * math.pi * i / 8)), "idle", i)
    for i in range(6):
        save(frame(state="listening", eyes="wide", mouth="o", arm_r=0.3,
                   hover=math.sin(2 * math.pi * i / 6) * 0.5, extras=(_waves(i / 6),)),
             "listening", i)
    for i in range(6):
        save(frame(state="thinking", eyes="up", mouth="flat", arm_l=0.6,
                   extras=(_dots(i % 3),)), "thinking", i)
    for i, lv in enumerate((0.3, 0.9, 0.6, 1.0, 0.4, 0.8)):
        save(frame(state="speaking", mouth="bars", level=lv, hover=0.3 * (i % 2)),
             "speaking", i)
    save(frame(state="error", eyes="x", mouth="wavy", antenna_tilt=-1.0, arm_l=-0.4,
               arm_r=-0.4, extras=(_sparks,)), "error", 0)
    for i, c in enumerate((0.5, 1.0, 0.5)):
        save(eyelids(c), "blink", i)
    for i, t in enumerate((0, 0.8, -0.8, 0.5, -0.3, 0)):
        save(frame(antenna_tilt=t), "fidget_antenna", i)
    for i, a in enumerate((0, 0.6, 1.0, 0.7, 1.0, 0.5, 0)):
        save(frame(arm_r=a), "fidget_wave", i)
    (OUT / "eyes").mkdir(parents=True, exist_ok=True)
    pupil().save(OUT / "eyes" / "pupil.png")
    (OUT / "avatar.yaml").write_text(MANIFEST)
    (OUT / "LICENSE").write_text(
        "Original character 'Bolt', generated by scripts/make_robot_pack.py in this repo.\n"
        "CC0 1.0 / public domain.\n"
    )
    print(f"wrote pack: {OUT}")


if __name__ == "__main__":
    main()
