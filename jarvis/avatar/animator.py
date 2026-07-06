"""Animation selection and frame stepping — pure logic, no Qt (§13A, §18).

`select_animation` is the pure state→animation mapping with the manifest's
fallback chain. `Animator` tracks the current animation + frame index and is
advanced by tick() calls from the window's QTimer; it owns no timers itself,
which keeps every §19 unit test headless.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from jarvis.bus import State
from jarvis.avatar.packs import AnimSpec, Manifest


def select_animation(state: State, manifest: Manifest) -> tuple[str, AnimSpec]:
    """Pure §13A mapping: state name → animation directory, via fallbacks."""
    name = state.value
    seen: set[str] = set()
    while name not in manifest.states:
        if name in seen:
            name = "idle"
            break
        seen.add(name)
        name = manifest.fallbacks.get(name, "idle")
    spec = manifest.states.get(name, manifest.states["idle"])
    return name, spec


@dataclass
class Frame:
    """What the window should paint this tick."""

    directory: str
    index: int
    show_pupils: bool


class Animator:
    """Frame counter + blink/fidget scheduling. Advanced externally via tick()."""

    def __init__(self, manifest: Manifest, rng: random.Random | None = None) -> None:
        self.manifest = manifest
        self._rng = rng or random.Random()
        self._state = State.IDLE
        self._anim_name, self._spec = select_animation(self._state, manifest)
        self._index = 0
        self._overlay: str | None = None       # active blink/fidget directory
        self._overlay_index = 0
        self._blink_at = self._next_blink(0.0)
        self._fidget_at = self._next_fidget(0.0)

    # -- scheduling -------------------------------------------------------- #

    def _next_blink(self, now: float) -> float:
        cfg = _blink_cfg()
        return now + self._rng.uniform(cfg[0], cfg[1])

    def _next_fidget(self, now: float) -> float:
        cfg = _fidget_cfg()
        return now + self._rng.uniform(cfg[0], cfg[1])

    # -- events ------------------------------------------------------------ #

    def set_state(self, state: State, now: float) -> None:
        """STATE_CHANGED consumer: switch animation; any state change cancels
        micro-behaviors (P5)."""
        if state is self._state:
            return
        self._state = state
        self._anim_name, self._spec = select_animation(state, self.manifest)
        self._index = 0
        self._overlay = None
        self._blink_at = self._next_blink(now)
        self._fidget_at = self._next_fidget(now)

    @property
    def state(self) -> State:
        return self._state

    @property
    def current_animation(self) -> str:
        return self._anim_name

    @property
    def fps(self) -> int:
        return self._spec.fps

    def is_static(self, frame_count: int) -> bool:
        """True when the current animation is a single frame and no overlay is
        due soon — the window may stop its timer entirely (NFR3)."""
        return frame_count <= 1 and self._overlay is None and not self._can_micro()

    def _can_micro(self) -> bool:
        return self._state is State.IDLE and (
            self.manifest.blink is not None or bool(self.manifest.fidgets)
        )

    # -- stepping ------------------------------------------------------------ #

    def tick(self, now: float, frame_counts: dict[str, int]) -> Frame:
        """Advance one animation step; returns what to paint."""
        # micro-behaviors (§13A): blink where the manifest's only_in allows
        # (default idle); fidgets are idle-only. Overlay finishes before next starts.
        if self._overlay is None:
            if (
                self.manifest.blink is not None
                and now >= self._blink_at
                and self._state.value in self.manifest.blink_only_in
            ):
                self._overlay = self.manifest.blink.directory
                self._overlay_index = 0
                self._blink_at = self._next_blink(now)
            elif self._state is State.IDLE and self.manifest.fidgets \
                    and now >= self._fidget_at:
                self._overlay = self._rng.choice(list(self.manifest.fidgets))
                self._overlay_index = 0
                self._fidget_at = self._next_fidget(now)

        if self._overlay is not None:
            count = frame_counts.get(self._overlay, 1)
            frame = Frame(
                directory=self._overlay,
                index=self._overlay_index,
                show_pupils="blink" not in self._overlay
                and self._anim_name not in self.manifest.eyes_hide_in,
            )
            self._overlay_index += 1
            if self._overlay_index >= count:
                self._overlay = None
            return frame

        count = frame_counts.get(self._anim_name, 1)
        if self._spec.loop:
            self._index = (self._index + 1) % max(count, 1)
        else:
            self._index = min(self._index + 1, count - 1)  # holds last frame
        return Frame(
            directory=self._anim_name,
            index=min(self._index, count - 1),
            show_pupils=self._anim_name not in self.manifest.eyes_hide_in,
        )


def _blink_cfg() -> tuple[float, float]:
    from jarvis import config

    b = config.get()["avatar"]["blink"]
    return float(b["min_s"]), float(b["max_s"])


def _fidget_cfg() -> tuple[float, float]:
    from jarvis import config

    f = config.get()["avatar"]["fidget"]
    return float(f["min_s"]), float(f["max_s"])
