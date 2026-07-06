"""Manifest schema validation matrix (§19 unit suite — headless, no Qt)."""
import copy

import pytest
import yaml

from jarvis import config
from jarvis.avatar.packs import PackError, load_manifest, validate

CAT = config.ROOT / "avatars" / "cat"


@pytest.fixture
def raw():
    with open(CAT / "avatar.yaml") as f:
        return yaml.safe_load(f)


def test_valid_pack_loads(raw):
    m = validate(raw, CAT)
    assert m.name == "Cat"
    assert m.frame_size == (128, 128)
    assert "idle" in m.states
    assert len(m.sockets) == 2


def test_load_manifest_end_to_end():
    m = load_manifest(CAT)
    assert m.blink is not None
    assert m.eyes_hide_in == ("blink", "error")


def test_unknown_top_level_key_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["python_hook"] = "evil.py"
    with pytest.raises(PackError, match="unknown manifest keys"):
        validate(bad, CAT)


def test_traversal_path_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["eyes"]["pupil"] = "../../etc/passwd"
    with pytest.raises(PackError, match="escapes the pack"):
        validate(bad, CAT)


def test_traversal_anim_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["states"]["idle"]["anim"] = "../cat/idle/../../../tmp"
    with pytest.raises(PackError):
        validate(bad, CAT)


def test_missing_idle_rejected(raw):
    bad = copy.deepcopy(raw)
    del bad["states"]["idle"]
    with pytest.raises(PackError, match="idle"):
        validate(bad, CAT)


def test_missing_anim_directory_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["states"]["idle"]["anim"] = "nonexistent_dir"
    with pytest.raises(PackError, match="missing or has no frames"):
        validate(bad, CAT)


def test_fallback_to_undefined_state_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["fallbacks"]["speaking"] = "dancing"
    with pytest.raises(PackError, match="not a defined state"):
        validate(bad, CAT)


def test_socket_outside_frame_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["eyes"]["sockets"][0]["center"] = [500, 72]
    with pytest.raises(PackError, match="within frame_size"):
        validate(bad, CAT)


def test_fps_clamped_not_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["states"]["idle"]["fps"] = 500
    m = validate(bad, CAT)
    assert m.states["idle"].fps == 30
    bad["states"]["idle"]["fps"] = 0
    assert validate(bad, CAT).states["idle"].fps == 1


def test_oversize_frame_size_rejected(raw):
    bad = copy.deepcopy(raw)
    bad["frame_size"] = [4096, 128]
    with pytest.raises(PackError, match="frame_size"):
        validate(bad, CAT)


def test_missing_license_rejected(raw, tmp_path):
    import shutil

    dst = tmp_path / "cat"
    shutil.copytree(CAT, dst)
    (dst / "LICENSE").unlink()
    with pytest.raises(PackError, match="LICENSE"):
        validate(raw, dst)


def test_minimum_viable_pack(tmp_path):
    """Appendix B: idle-only pack with fallbacks routing everything to idle."""
    pack = tmp_path / "min"
    (pack / "idle").mkdir(parents=True)
    (pack / "idle" / "000.png").write_bytes((CAT / "idle" / "000.png").read_bytes())
    (pack / "LICENSE").write_text("CC0")
    raw = {
        "name": "Min",
        "frame_size": [128, 128],
        "states": {"idle": {"anim": "idle", "fps": 1}},
        "fallbacks": {s: "idle" for s in ("listening", "thinking", "speaking", "error")},
    }
    m = validate(raw, pack)
    assert m.pupil_path is None  # no eyes block = no tracking, valid
