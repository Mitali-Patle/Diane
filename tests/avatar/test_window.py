"""Avatar widget integration tests — pytest-qt offscreen (§19).

Run with QT_QPA_PLATFORM=offscreen (CI avatar job). These exercise the Qt
layer: pack load → frame decode, state → animation switch, hot-swap under a
running timer, dot fallback, and the click-to-activate intent.
"""
import asyncio
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402

from jarvis.avatar.window import AvatarWindow  # noqa: E402
from jarvis.bus import AvatarActivate, Bus, State  # noqa: E402


@pytest.fixture
def window(qtbot):
    bus = Bus()
    w = AvatarWindow(bus, status_out=None)
    qtbot.addWidget(w)
    return w, bus


def test_pack_loads_and_decodes_frames(window):
    w, _ = window
    assert w.load_pack("cat")
    assert "idle" in w._pack.frames
    assert len(w._pack.frames["idle"]) == 2
    assert w._pack.pupil is not None
    assert w.width() == 128  # scale 1.0


def test_state_event_selects_correct_animation(window):
    w, _ = window
    w.load_pack("cat")
    w._apply_state(State.LISTENING)
    counts = {d: len(f) for d, f in w._pack.frames.items()}
    frame = w._animator.tick(time.monotonic(), counts)
    assert frame.directory == "listening"


def test_state_event_to_frame_selection_under_100ms(window):
    """SC8: the event→frame-selection path must be well inside 100 ms."""
    w, _ = window
    w.load_pack("cat")
    t0 = time.perf_counter()
    w._apply_state(State.SPEAKING)
    counts = {d: len(f) for d, f in w._pack.frames.items()}
    frame = w._animator.tick(time.monotonic(), counts)
    elapsed = (time.perf_counter() - t0) * 1000
    assert frame.directory == "speaking"
    assert elapsed < 100


def test_hot_swap_under_running_timer(window):
    w, _ = window
    w.load_pack("cat")
    assert w._frame_timer.isActive()
    t0 = time.perf_counter()
    assert w.load_pack("cat")  # swap (to itself — same code path)
    assert (time.perf_counter() - t0) < 1.0  # SC10
    assert w._frame_timer.isActive()


def test_invalid_pack_keeps_previous(window):
    w, _ = window
    assert w.load_pack("cat")
    before = w._pack
    assert not w.load_pack("no_such_pack")
    assert w._pack is before  # previous pack retained (§11)


def test_dot_fallback_paints_without_assets(window, qtbot):
    w, _ = window
    w.use_dot_fallback()
    w.show()
    w.repaint()  # paintEvent must not raise with no pack loaded
    assert w._pack is None


def test_left_click_publishes_avatar_activate(window, qtbot):
    w, bus = window
    q = bus.subscribe()
    w.load_pack("cat")
    w.show()
    qtbot.mouseClick(w, Qt.LeftButton, pos=QPoint(20, 20))
    assert isinstance(q.get_nowait(), AvatarActivate)


def _mouse_event(kind, local, global_, button):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(kind, QPointF(*local), QPointF(*global_), button, button,
                       Qt.NoModifier)


def test_drag_does_not_activate(window):
    """FR14/DL-10: real move events between press and release = drag, no intent.
    Handlers are invoked directly because offscreen qtbot.mouseMove doesn't
    deliver move events — the original test passed vacuously."""
    from PySide6.QtCore import QEvent

    w, bus = window
    q = bus.subscribe()
    w.load_pack("cat")
    w.show()
    w.mousePressEvent(_mouse_event(QEvent.MouseButtonPress, (20, 20), (400, 400),
                                   Qt.LeftButton))
    w.mouseMoveEvent(_mouse_event(QEvent.MouseMove, (20, 20), (460, 460), Qt.LeftButton))
    w.mouseReleaseEvent(_mouse_event(QEvent.MouseButtonRelease, (20, 20), (460, 460),
                                     Qt.LeftButton))
    assert q.empty()  # a drag is not a click


def test_press_release_without_motion_activates(window):
    from PySide6.QtCore import QEvent

    w, bus = window
    q = bus.subscribe()
    w.load_pack("cat")
    w.show()
    w.mousePressEvent(_mouse_event(QEvent.MouseButtonPress, (20, 20), (400, 400),
                                   Qt.LeftButton))
    w.mouseMoveEvent(_mouse_event(QEvent.MouseMove, (20, 20), (401, 401), Qt.LeftButton))
    w.mouseReleaseEvent(_mouse_event(QEvent.MouseButtonRelease, (20, 20), (401, 401),
                                     Qt.LeftButton))
    assert isinstance(q.get_nowait(), AvatarActivate)  # jitter under 4px is a click


async def test_latest_wins_event_consumption(window):
    w, bus = window
    w.load_pack("cat")
    task = asyncio.create_task(w.consume_events())
    await asyncio.sleep(0)
    for s in (State.LISTENING, State.THINKING, State.SPEAKING):
        bus.publish(__import__("jarvis.bus", fromlist=["StateChanged"]).StateChanged(s))
    await asyncio.sleep(0.05)
    assert w._state is State.SPEAKING  # stale states skipped (§10 backpressure)
    task.cancel()


def test_slot_containment_counts_errors(window):
    w, _ = window
    w.load_pack("cat")
    w._animator = "broken"  # force an exception on the tick path
    w._frame = None         # ensure the slot must consult the animator
    w._on_frame_tick()      # must not raise (contained per §13A)
    assert w.errors >= 1


def test_symlinked_frame_outside_pack_rejected(window, tmp_path, monkeypatch):
    """P10/§14 regression: a frame symlinked to a file outside the pack must
    be rejected at load even though its directory is contained."""
    import shutil

    from jarvis import config as cfgmod
    from jarvis.avatar import packs

    outside = tmp_path / "outside.png"
    shutil.copy(cfgmod.ROOT / "avatars/cat/idle/000.png", outside)
    packs_dir = tmp_path / "avatars"
    dst = packs_dir / "evil"
    shutil.copytree(cfgmod.ROOT / "avatars/cat", dst)
    (dst / "idle" / "002.png").symlink_to(outside)

    manifest = packs.load_manifest(dst)  # manifest itself is fine
    with __import__("pytest").raises(packs.PackError, match="resolves outside"):
        packs.LoadedPack(manifest)
