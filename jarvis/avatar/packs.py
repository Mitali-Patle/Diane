"""Avatar pack discovery, manifest validation, sprite loading, hot-swap (§13A, Appendix B).

This is the ONLY module that reads pack files from disk (§9). P10 is enforced
here: every manifest field is data — numeric fields range-clamped, every path
resolved and contained inside the pack directory, images size-capped and
decoded via Qt's image loader only. Validation errors reject the pack with a
speakable message; they never crash the widget (§11).

Manifest validation is pure Python (no Qt) so the §19 unit matrix runs
headless; only frame *loading* touches Qt, lazily.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from jarvis import config
from jarvis.bus import State

log = logging.getLogger("jarvis.avatar.packs")

MAX_IMAGE_SIDE = 2048
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PACK_BYTES = 40 * 1024 * 1024
FPS_MIN, FPS_MAX = 1, 30

_ALLOWED_TOP_KEYS = {
    "name", "version", "author", "frame_size", "anchor", "states", "fallbacks",
    "overlays", "eyes",
}
_STATE_NAMES = {s.value for s in State}
_ANCHORS = {"bottom-center", "center", "top-center"}


class PackError(Exception):
    """Invalid pack; .spoken is the first validation error, speakable (§11)."""

    def __init__(self, spoken: str) -> None:
        super().__init__(spoken)
        self.spoken = spoken


@dataclass(frozen=True, slots=True)
class AnimSpec:
    directory: str
    fps: int
    loop: bool


@dataclass(frozen=True, slots=True)
class EyeSocket:
    center: tuple[int, int]
    radius: int


@dataclass(frozen=True, slots=True)
class Manifest:
    name: str
    pack_dir: Path
    frame_size: tuple[int, int]
    anchor: str
    states: dict[str, AnimSpec]
    fallbacks: dict[str, str]
    blink: AnimSpec | None
    blink_only_in: tuple[str, ...]
    fidgets: tuple[str, ...]
    pupil_path: Path | None
    sockets: tuple[EyeSocket, ...] = ()
    eyes_hide_in: tuple[str, ...] = ()


def _contained(pack_dir: Path, relative: str) -> Path:
    """Resolve a manifest path and require it inside the pack directory (P10, §14)."""
    p = (pack_dir / relative).resolve()
    if not p.is_relative_to(pack_dir.resolve()):
        raise PackError(f"path {relative!r} escapes the pack directory")
    return p


def _clamp_fps(v) -> int:
    try:
        return max(FPS_MIN, min(FPS_MAX, int(v)))
    except (TypeError, ValueError):
        raise PackError(f"fps value {v!r} is not a number") from None


def _anim(pack_dir: Path, name: str, raw: dict) -> AnimSpec:
    if not isinstance(raw, dict) or "anim" not in raw:
        raise PackError(f"state {name!r} needs an 'anim' entry")
    directory = str(raw["anim"])
    frames_dir = _contained(pack_dir, directory)
    if not frames_dir.is_dir() or not sorted(frames_dir.glob("*.png")):
        raise PackError(f"animation directory {directory!r} is missing or has no frames")
    return AnimSpec(
        directory=directory,
        fps=_clamp_fps(raw.get("fps", 8)),
        loop=bool(raw.get("loop", True)),
    )


def validate(raw: dict, pack_dir: Path) -> Manifest:
    """Validate a parsed avatar.yaml against Appendix B. Raises PackError."""
    if not isinstance(raw, dict):
        raise PackError("manifest is not a mapping")
    unknown = set(raw) - _ALLOWED_TOP_KEYS
    if unknown:
        raise PackError(f"unknown manifest keys: {', '.join(sorted(unknown))}")
    if not (pack_dir / "LICENSE").exists() and not (pack_dir / "CREDITS").exists():
        raise PackError("pack has no LICENSE or CREDITS file")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise PackError("manifest needs a display name")

    fs = raw.get("frame_size")
    if (
        not isinstance(fs, (list, tuple)) or len(fs) != 2
        or not all(isinstance(v, int) and 0 < v <= MAX_IMAGE_SIDE for v in fs)
    ):
        raise PackError("frame_size must be [width, height] within 2048x2048")
    frame_size = (fs[0], fs[1])

    anchor = str(raw.get("anchor", "bottom-center"))
    if anchor not in _ANCHORS:
        raise PackError(f"anchor {anchor!r} not one of {sorted(_ANCHORS)}")

    states_raw = raw.get("states")
    if not isinstance(states_raw, dict) or "idle" not in states_raw:
        raise PackError("states table must exist and include at least 'idle'")
    states: dict[str, AnimSpec] = {}
    for sname, sval in states_raw.items():
        if sname not in _STATE_NAMES:
            raise PackError(f"unknown state {sname!r} in states table")
        states[sname] = _anim(pack_dir, sname, sval)

    fallbacks_raw = raw.get("fallbacks", {})
    if not isinstance(fallbacks_raw, dict):
        raise PackError("fallbacks must be a mapping")
    fallbacks: dict[str, str] = {}
    for k, v in fallbacks_raw.items():
        if k not in _STATE_NAMES:
            raise PackError(f"unknown state {k!r} in fallbacks")
        target = str(v)
        if target not in states_raw and target != "idle":
            raise PackError(f"fallback target {target!r} is not a defined state")
        fallbacks[k] = target
    for sname in _STATE_NAMES:
        if sname not in states and fallbacks.get(sname, "idle") not in states:
            raise PackError(f"state {sname!r} has neither an animation nor a valid fallback")

    overlays = raw.get("overlays", {}) or {}
    if not isinstance(overlays, dict):
        raise PackError("overlays must be a mapping")
    blink = None
    blink_only_in: tuple[str, ...] = ("idle",)
    if "blink" in overlays:
        blink = _anim(pack_dir, "blink", overlays["blink"])
        only_in = overlays["blink"].get("only_in", ["idle"])
        if not isinstance(only_in, list):
            raise PackError("blink only_in must be a list of states")
        blink_only_in = tuple(str(s) for s in only_in)
    fidgets: list[str] = []
    for f in overlays.get("fidgets", []) or []:
        _anim(pack_dir, f"fidget {f}", {"anim": str(f)})
        fidgets.append(str(f))

    pupil_path = None
    sockets: tuple[EyeSocket, ...] = ()
    eyes_hide_in: tuple[str, ...] = ()
    eyes = raw.get("eyes")
    if eyes is not None:
        if not isinstance(eyes, dict) or "pupil" not in eyes or "sockets" not in eyes:
            raise PackError("eyes block needs 'pupil' and 'sockets'")
        pupil_path = _contained(pack_dir, str(eyes["pupil"]))
        if not pupil_path.is_file():
            raise PackError("pupil image is missing")
        parsed = []
        for s in eyes["sockets"]:
            c = s.get("center") if isinstance(s, dict) else None
            if (
                not isinstance(c, (list, tuple)) or len(c) != 2
                or not all(isinstance(v, int) for v in c)
                or not (0 <= c[0] < frame_size[0] and 0 <= c[1] < frame_size[1])
            ):
                raise PackError("eye socket centers must lie within frame_size")
            radius = max(1, min(int(s.get("radius", 4)), max(frame_size)))
            parsed.append(EyeSocket(center=(c[0], c[1]), radius=radius))
        sockets = tuple(parsed)
        eyes_hide_in = tuple(str(v) for v in eyes.get("hide_in", []) or [])

    return Manifest(
        name=name,
        pack_dir=pack_dir,
        frame_size=frame_size,
        anchor=anchor,
        states=states,
        fallbacks=fallbacks,
        blink=blink,
        blink_only_in=blink_only_in,
        fidgets=tuple(fidgets),
        pupil_path=pupil_path,
        sockets=sockets,
        eyes_hide_in=eyes_hide_in,
    )


def _pack_size_ok(pack_dir: Path) -> bool:
    # lstat: don't follow symlinks — outside-pack links are rejected at load
    total = sum(f.lstat().st_size for f in pack_dir.rglob("*") if f.is_file())
    return total <= MAX_PACK_BYTES


def discover() -> dict[str, Path]:
    """Map pack name -> directory for every syntactically plausible pack on disk."""
    packs_dir = config.ROOT / config.get()["avatar"]["packs_dir"]
    found: dict[str, Path] = {}
    if packs_dir.is_dir():
        for child in sorted(packs_dir.iterdir()):
            if child.is_dir() and (child / "avatar.yaml").is_file():
                name = child.name.lower()
                if name.replace("_", "").replace("-", "").isalnum():
                    found[name] = child
    return found


def load_manifest(pack_dir: Path) -> Manifest:
    """Parse + validate a pack's manifest; size-cap the whole pack (P10)."""
    if not _pack_size_ok(pack_dir):
        raise PackError("pack exceeds the 40 MB size cap")
    try:
        with open(pack_dir / "avatar.yaml") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise PackError("pack has no avatar.yaml") from None
    except yaml.YAMLError as e:
        raise PackError(f"avatar.yaml is not valid YAML: {e}") from None
    return validate(raw, pack_dir)


# ---------------------------------------------------------------------------- #
# Frame loading (the only Qt-touching part; lazy import keeps validation pure)
# ---------------------------------------------------------------------------- #

class LoadedPack:
    """Decoded frames for one pack. Frames decode once at load, cache dropped
    on pack switch (SC6). Decoding uses Qt's image loader only (§14)."""

    def __init__(self, manifest: Manifest) -> None:
        from PySide6.QtGui import QPixmap

        self.manifest = manifest
        self.frames: dict[str, list] = {}
        anims = dict(manifest.states)
        extra = [manifest.blink.directory] if manifest.blink else []
        extra += list(manifest.fidgets)
        for spec in anims.values():
            self._load_dir(spec.directory)
        for directory in extra:
            self._load_dir(directory)
        self.pupil = QPixmap(str(manifest.pupil_path)) if manifest.pupil_path else None

    def _load_dir(self, directory: str) -> None:
        if directory in self.frames:
            return
        from PySide6.QtGui import QImageReader, QPixmap

        frames = []
        pack_root = self.manifest.pack_dir.resolve()
        for png in sorted(_contained(self.manifest.pack_dir, directory).glob("*.png")):
            # §14: re-resolve every frame — a symlink pointing outside the pack
            # must not ride in on a contained directory.
            if not png.resolve().is_relative_to(pack_root):
                raise PackError(f"frame {png.name} resolves outside the pack")
            if png.stat().st_size > MAX_IMAGE_BYTES:
                raise PackError(f"frame {png.name} exceeds 5 MB")
            reader = QImageReader(str(png))
            size = reader.size()
            if size.width() > MAX_IMAGE_SIDE or size.height() > MAX_IMAGE_SIDE:
                raise PackError(f"frame {png.name} exceeds 2048x2048")
            if (size.width(), size.height()) != self.manifest.frame_size:
                raise PackError(
                    f"frame {png.name} is {size.width()}x{size.height()}, "
                    f"manifest says {self.manifest.frame_size}"
                )
            frames.append(QPixmap.fromImage(reader.read()))
        if not frames:
            raise PackError(f"animation directory {directory!r} has no frames")
        self.frames[directory] = frames
