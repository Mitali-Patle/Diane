"""Cursor eye-tracking math: socket clamping + lerp smoothing (§13A, FR12).

Pure math, no Qt: the window polls QCursor at ≤30 Hz and passes coordinates in;
positions never leave this layer and are never logged (NFR1). The lerp factor
(0.25/tick) gives the organic lag the handbook specifies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from jarvis.avatar.packs import EyeSocket

LERP = 0.25


@dataclass
class EyeState:
    """Per-eye pupil offsets, updated once per poll tick."""

    sockets: tuple[EyeSocket, ...]
    scale: float = 1.0
    pupils: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.pupils:
            self.pupils = [(0.0, 0.0) for _ in self.sockets]

    def update(self, cursor_in_frame: tuple[float, float]) -> list[tuple[float, float]]:
        """One tick: clamp the cursor vector per socket, lerp pupils toward it.

        `cursor_in_frame` is the cursor position in unscaled frame-pixel
        coordinates (the window converts from screen space).
        """
        out: list[tuple[float, float]] = []
        for i, socket in enumerate(self.sockets):
            dx = cursor_in_frame[0] - socket.center[0]
            dy = cursor_in_frame[1] - socket.center[1]
            dist = math.hypot(dx, dy)
            if dist > socket.radius and dist > 0:
                dx *= socket.radius / dist
                dy *= socket.radius / dist
            px, py = self.pupils[i]
            px += (dx - px) * LERP
            py += (dy - py) * LERP
            self.pupils[i] = (px, py)
            out.append((px, py))
        return out
