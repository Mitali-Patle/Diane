"""Frozen-config tests (§15)."""
import pytest

from jarvis import config


def test_config_loads_expected_sections():
    cfg = config.load(config.ROOT / "config.yaml")
    for section in ("audio", "wake", "stt", "llm", "tts", "tools", "logging", "avatar"):
        assert section in cfg


def test_config_is_deeply_immutable():
    cfg = config.load(config.ROOT / "config.yaml")
    with pytest.raises(TypeError):
        cfg["llm"]["model"] = "evil"  # type: ignore[index]
    # Lists become tuples — no appends either.
    assert isinstance(cfg["tools"]["command_whitelist"], tuple)


def test_audio_contract_matches_handbook():
    cfg = config.load(config.ROOT / "config.yaml")
    assert cfg["audio"]["sample_rate"] == 16000
    assert cfg["audio"]["blocksize"] == 512
    assert cfg["avatar"]["eye_poll_hz"] <= 30
    assert cfg["avatar"]["fps_cap"] <= 30
