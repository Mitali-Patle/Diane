"""Animator mapping + fallback chain + frame stepping (§19 — headless, no Qt)."""
import random

from jarvis import config
from jarvis.avatar.animator import Animator, select_animation
from jarvis.avatar.packs import load_manifest
from jarvis.bus import State

CAT = config.ROOT / "avatars" / "cat"


def manifest():
    return load_manifest(CAT)


def test_direct_state_mapping():
    m = manifest()
    for state in State:
        name, spec = select_animation(state, m)
        assert name == state.value  # cat pack defines all five


def test_fallback_chain_routes_missing_states_to_idle(tmp_path):
    import shutil

    dst = tmp_path / "cat"
    shutil.copytree(CAT, dst)
    shutil.rmtree(dst / "speaking")
    import yaml

    raw = yaml.safe_load((dst / "avatar.yaml").read_text())
    del raw["states"]["speaking"]
    (dst / "avatar.yaml").write_text(yaml.safe_dump(raw))
    m = load_manifest(dst)
    name, _ = select_animation(State.SPEAKING, m)
    assert name == "idle"  # speaking -> fallbacks[speaking]=idle


def test_loop_animation_wraps():
    m = manifest()
    a = Animator(m, rng=random.Random(1))
    a.set_state(State.SPEAKING, now=0.0)
    counts = {"speaking": 2}
    seen = [a.tick(1.0 + i, counts).index for i in range(4)]
    assert seen == [0, 1, 0, 1] or seen == [1, 0, 1, 0]


def test_non_loop_holds_last_frame():
    m = manifest()
    a = Animator(m, rng=random.Random(1))
    a.set_state(State.ERROR, now=0.0)
    counts = {"error": 1}
    frames = [a.tick(float(i), counts) for i in range(3)]
    assert all(f.index == 0 for f in frames)
    assert all(not f.show_pupils for f in frames)  # error in eyes_hide_in


def test_blink_fires_only_in_idle_and_state_change_cancels():
    m = manifest()
    a = Animator(m, rng=random.Random(7))
    counts = {"idle": 2, "blink": 2, "listening": 2}
    # advance far past any blink schedule while LISTENING: no blink overlay
    a.set_state(State.LISTENING, now=0.0)
    frames = [a.tick(100.0 + i, counts) for i in range(10)]
    assert all(f.directory == "listening" for f in frames)
    # in IDLE the blink eventually fires
    a.set_state(State.IDLE, now=200.0)
    dirs = {a.tick(200.0 + i * 5, counts).directory for i in range(20)}
    assert "blink" in dirs
    # mid-blink state change cancels the overlay (P5)
    a.set_state(State.IDLE, now=400.0)
    while a.tick(500.0, counts).directory != "blink":
        pass
    a.set_state(State.THINKING, now=500.1)
    assert a.tick(500.2, counts).directory == "thinking"


def test_blink_hides_pupils():
    m = manifest()
    a = Animator(m, rng=random.Random(7))
    counts = {"idle": 2, "blink": 2}
    a._blink_at = 0.0  # force the blink to be due NOW — deterministic
    frame = a.tick(1.0, counts)
    assert frame.directory == "blink"
    assert not frame.show_pupils  # blink is in eyes_hide_in


def test_is_static_stops_timer_for_single_frame_no_micro(tmp_path):
    """NFR3: a one-frame pack with no blink/fidgets must report static."""
    import shutil

    import yaml

    dst = tmp_path / "min"
    (dst / "idle").mkdir(parents=True)
    shutil.copy(CAT / "idle" / "000.png", dst / "idle" / "000.png")
    (dst / "LICENSE").write_text("CC0")
    (dst / "avatar.yaml").write_text(yaml.safe_dump({
        "name": "Min", "frame_size": [128, 128],
        "states": {"idle": {"anim": "idle", "fps": 1}},
        "fallbacks": {s: "idle" for s in ("listening", "thinking", "speaking", "error")},
    }))
    a = Animator(load_manifest(dst), rng=random.Random(1))
    assert a.is_static(frame_count=1)
    # the cat pack (has blink) must NOT report static in idle
    assert not Animator(manifest(), rng=random.Random(1)).is_static(frame_count=1)
