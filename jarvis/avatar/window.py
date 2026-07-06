"""The avatar widget: frameless, translucent, always-on-top, draggable (§13A, FR10-FR15).

P9: this is a passive view. It consumes STATE_CHANGED (latest-wins mailbox)
and AvatarCommand; the ONLY thing it publishes is AVATAR_ACTIVATE on left
click (§9). Every Qt slot wraps its body in try/except → log + AvatarStatus
(failure containment, §13A); the supervisor applies the 3-strikes rule.

If no pack loads, the built-in code-drawn "dot" avatar (a pulse circle) keeps
state visible with zero assets (§16).
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import time

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from jarvis import config
from jarvis.avatar.animator import Animator
from jarvis.avatar.eyes import EyeState
from jarvis.avatar import packs
from jarvis.avatar.packs import LoadedPack, PackError
from jarvis.bus import AvatarActivate, AvatarCommand, AvatarStatus, Bus, State, StateChanged

log = logging.getLogger("jarvis.avatar")

STATE_FILE = config.ROOT / "avatar_state.json"

_DOT_COLORS = {
    State.IDLE: QColor(120, 120, 130),
    State.LISTENING: QColor(80, 200, 120),
    State.THINKING: QColor(90, 140, 240),
    State.SPEAKING: QColor(240, 180, 60),
    State.ERROR: QColor(230, 90, 90),
}
_DOT_SIZE = 72


def contained(fn):
    """§13A slot containment: never let a paint/timer exception escape Qt."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.exception("avatar slot %s failed", fn.__name__)
            self.report_error(str(e))

    return wrapper


class AvatarWindow(QWidget):
    """Renders the active pack (or the dot fallback) and forwards one intent."""

    def __init__(
        self,
        bus: Bus,
        status_out: asyncio.Queue[AvatarStatus] | None = None,
        request_quit=None,
    ) -> None:
        super().__init__()
        cfg = config.get()["avatar"]
        self._bus = bus
        self._status_out = status_out
        # P9: quit is an app decision — the widget only *requests* it and the
        # composition root shuts down cooperatively (P5).
        self._request_quit = request_quit
        self._scale = max(0.5, min(2.0, float(cfg["scale"])))
        self._click_to_activate = bool(cfg["click_to_activate"])
        self._eyes_enabled = bool(cfg["eye_tracking"])
        self._fps_cap = min(30, int(cfg["fps_cap"]))
        self._state = State.IDLE
        self._pack: LoadedPack | None = None
        self._animator: Animator | None = None
        self._eye_state: EyeState | None = None
        self._frame = None          # current Frame from the animator
        self._pupils: list[tuple[float, float]] = []
        self._drag_offset: QPoint | None = None
        self._press_global: QPoint | None = None
        self._dragged = False
        self._dot_phase = 0.0
        self.errors = 0             # read by the supervisor (NFR4)

        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if bool(cfg["click_through"]):
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._on_frame_tick)
        self._eye_timer = QTimer(self)
        self._eye_timer.timeout.connect(self._on_eye_tick)
        self._eye_timer.setInterval(max(33, int(1000 / int(cfg["eye_poll_hz"]))))

        self._restore_geometry()

    # -- pack management (FR15/FR16) --------------------------------------- #

    def load_pack(self, name: str) -> bool:
        """Hot-swap to a pack; on failure keep the current one (§11). <1s (SC10)."""
        try:
            pack_dir = packs.discover().get(name)
            if pack_dir is None:
                raise PackError(f"no pack named {name}")
            manifest = packs.load_manifest(pack_dir)
            new_pack = LoadedPack(manifest)
        except PackError as e:
            log.warning("pack %s rejected: %s", name, e.spoken)
            self._emit_status(error=e.spoken)
            return False
        self._pack = new_pack  # old pack's frame cache dropped with the reference (SC6)
        self._animator = Animator(manifest)
        self._animator.set_state(self._state, time.monotonic())
        self._eye_state = EyeState(sockets=manifest.sockets)
        w, h = manifest.frame_size
        self.resize(int(w * self._scale), int(h * self._scale))
        self._retune_timers()
        self._emit_status()
        self.save_state()  # DL-8: runtime pack choice must survive a restart
        log.info("avatar pack -> %s", name)
        return True

    def use_dot_fallback(self) -> None:
        """Zero-asset pulse circle: state visibility survives any pack failure (§16)."""
        self._pack = None
        self._animator = None
        self._eye_state = None
        self._eye_timer.stop()
        self.resize(_DOT_SIZE, _DOT_SIZE)
        self._frame_timer.start(100)
        self._emit_status()

    # -- bus plumbing ------------------------------------------------------- #

    async def consume_events(self) -> None:
        """Latest-wins STATE_CHANGED consumption (§10 backpressure)."""
        q = self._bus.subscribe(latest_wins=True)
        try:
            while True:
                ev = await q.get()
                if isinstance(ev, StateChanged):
                    self._apply_state(ev.new_state)
        finally:
            self._bus.unsubscribe(q)

    async def consume_commands(self, commands: asyncio.Queue[AvatarCommand]) -> None:
        while True:
            cmd = await commands.get()
            if cmd.action == "set_pack" and cmd.arg:
                self.load_pack(cmd.arg)
            elif cmd.action == "toggle_eyes":
                self._toggle_eyes()
            elif cmd.action == "hide":
                self.hide()
            elif cmd.action == "show":
                self.show()

    @contained
    def _apply_state(self, state: State) -> None:
        self._state = state
        if self._animator is not None:
            self._animator.set_state(state, time.monotonic())
            self._retune_timers()
        self.update()  # SC8: repaint scheduled immediately on the event

    def _emit_status(self, error: str | None = None) -> None:
        if error:
            self.errors += 1
        if self._status_out is not None:
            name = self._pack.manifest.name if self._pack else "dot"
            self._status_out.put_nowait(
                AvatarStatus(pack=name, visible=self.isVisible(), error=error)
            )

    def report_error(self, message: str) -> None:
        self._emit_status(error=message)

    # -- timers -------------------------------------------------------------- #

    def _retune_timers(self) -> None:
        if self._animator is None:
            return
        fps = min(self._animator.fps, self._fps_cap)
        anim_dir = self._animator.current_animation  # not the stale painted frame
        count = len(self._pack.frames.get(anim_dir, [])) if self._pack else 1
        if self._animator.is_static(count):
            self._frame_timer.stop()  # NFR3: static frame -> no timer at all
            self._on_frame_tick()     # paint the single frame once
        else:
            self._frame_timer.start(int(1000 / fps))
        if self._eyes_enabled and self._eye_state is not None and self._eye_state.sockets:
            self._eye_timer.start()
        else:
            self._eye_timer.stop()

    @contained
    def _on_frame_tick(self) -> None:
        if self._animator is not None and self._pack is not None:
            counts = {d: len(f) for d, f in self._pack.frames.items()}
            self._frame = self._animator.tick(time.monotonic(), counts)
        else:
            self._dot_phase += 0.15
        self.update()

    @contained
    def _on_eye_tick(self) -> None:
        if self._eye_state is None or not self._eye_state.sockets:
            return
        cursor = QCursor.pos()  # polled, never hooked (§13A); never logged (NFR1)
        local = self.mapFromGlobal(cursor)
        frame_pos = (local.x() / self._scale, local.y() / self._scale)
        self._pupils = self._eye_state.update(frame_pos)
        self.update()

    # -- painting -------------------------------------------------------------- #

    @contained
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._pack is None or self._animator is None:
            self._paint_dot(painter)
            return
        frame = self._frame
        if frame is None:
            frame = self._animator.tick(time.monotonic(), {})
        frames = self._pack.frames.get(frame.directory) or self._pack.frames.get("idle")
        if not frames:
            self._paint_dot(painter)
            return
        pixmap = frames[min(frame.index, len(frames) - 1)]
        painter.save()
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, pixmap)
        if (
            frame.show_pupils
            and self._pack.pupil is not None
            and self._eyes_enabled
            and self._eye_state is not None
        ):
            pupil = self._pack.pupil
            for socket, (dx, dy) in zip(self._eye_state.sockets, self._pupils):
                painter.drawPixmap(
                    int(socket.center[0] + dx - pupil.width() / 2),
                    int(socket.center[1] + dy - pupil.height() / 2),
                    pupil,
                )
        painter.restore()

    def _paint_dot(self, painter: QPainter) -> None:
        import math

        color = _DOT_COLORS.get(self._state, _DOT_COLORS[State.IDLE])
        pulse = 0.85 + 0.15 * math.sin(self._dot_phase)
        r = int(_DOT_SIZE * 0.4 * pulse)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        center = self.rect().center()
        painter.drawEllipse(center, r, r)

    # -- input (FR14) -------------------------------------------------------- #

    @contained
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._press_global = event.globalPosition().toPoint()
            self._dragged = False
        elif event.button() == Qt.RightButton:
            self._context_menu(event.globalPosition().toPoint())

    @contained
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and self._press_global is not None:
            # Discriminate on cumulative motion of the PRESS point — the window
            # position tracks the cursor by construction, so it can't be used.
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() >= 4:
                self._dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    @contained
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            was_drag = self._dragged
            self._drag_offset = None
            self._press_global = None
            self._dragged = False
            self.save_state()
            if not was_drag and self._click_to_activate:
                # The ONE intent this widget may emit (P9, DL-10).
                self._bus.publish(AvatarActivate())

    def _context_menu(self, at: QPoint) -> None:
        menu = QMenu(self)
        for name in packs.discover():
            act = QAction(f"Switch to {name}", menu)
            act.triggered.connect(lambda _=False, n=name: self.load_pack(n))
            menu.addAction(act)
        menu.addSeparator()
        eyes = QAction("Toggle eye tracking", menu)
        eyes.triggered.connect(self._toggle_eyes)
        menu.addAction(eyes)
        hide = QAction("Hide avatar", menu)
        hide.triggered.connect(self.hide)
        menu.addAction(hide)
        if self._request_quit is not None:
            quit_ = QAction("Quit Diane", menu)
            quit_.triggered.connect(lambda: self._request_quit())
            menu.addAction(quit_)
        menu.exec(at)

    @contained
    def _toggle_eyes(self) -> None:
        self._eyes_enabled = not self._eyes_enabled
        self._retune_timers()
        self.save_state()

    # -- geometry persistence (§12) ------------------------------------------- #

    def _restore_geometry(self) -> None:
        try:
            data = json.loads(STATE_FILE.read_text())
            x, y = int(data["window_x"]), int(data["window_y"])
            screen = self.screen().availableGeometry()
            self.move(min(max(x, 0), screen.width() - 40),
                      min(max(y, 0), screen.height() - 40))
            self._eyes_enabled = bool(data.get("eyes_enabled", self._eyes_enabled))
        except Exception:  # noqa: BLE001 — missing/corrupt state file is normal
            pass

    def save_state(self) -> None:
        """Persist the five §12 fields. Called on drag-end, pack switch, eye
        toggle, and by the composition root at clean shutdown."""
        active = self._pack.manifest.pack_dir.name if self._pack else "dot"
        try:
            STATE_FILE.write_text(json.dumps({
                "active_pack": active,
                "window_x": self.pos().x(),
                "window_y": self.pos().y(),
                "eyes_enabled": self._eyes_enabled,
                "visible": self.isVisible(),
            }))
        except OSError:
            log.warning("could not persist avatar state")
