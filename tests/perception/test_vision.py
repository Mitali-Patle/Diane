"""Vision plumbing tests with an injected fake VLM (§19). P8: capture only on demand."""
import pytest

from jarvis.actions import Actions
from jarvis.bus import ToolCall
from jarvis.perception import vision
from jarvis.perception.vision import DEFAULT_PROMPT, Vision


class FakeVlm:
    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    async def describe_image(self, png: bytes, prompt: str) -> str:
        self.calls.append((len(png), prompt))
        return "a code editor is open"


@pytest.fixture
def fake_grab(monkeypatch):
    grabs = []

    def _grab():
        grabs.append(1)
        return b"\x89PNG fake"

    monkeypatch.setattr(vision, "_grab_png", _grab)
    return grabs


async def test_describe_captures_once_and_returns_text(fake_grab):
    vlm = FakeVlm()
    v = Vision(vlm=vlm)
    out = await v.describe()
    assert out == "a code editor is open"
    assert len(fake_grab) == 1          # exactly one capture per call (P8)
    assert vlm.calls[0][1] == DEFAULT_PROMPT


async def test_custom_question_reaches_vlm(fake_grab):
    vlm = FakeVlm()
    v = Vision(vlm=vlm)
    await v.describe("is a terminal visible?")
    assert vlm.calls[0][1] == "is a terminal visible?"


async def test_actions_routes_describe_screen_to_vision(fake_grab):
    vlm = FakeVlm()
    actions = Actions(vision=Vision(vlm=vlm))
    out = await actions.run(ToolCall(name="describe_screen", args={}), turn_id=1)
    assert "a code editor is open" in out
    assert len(fake_grab) == 1


async def test_describe_screen_without_vision_rejected_no_capture(fake_grab):
    actions = Actions(vision=None)
    out = await actions.run(ToolCall(name="describe_screen", args={}), turn_id=1)
    assert out.startswith("REJECTED:")
    assert fake_grab == []              # nothing captured


def test_no_continuous_capture_constructs():
    """P8 guard: vision module must contain no timers/loops around capture."""
    import inspect

    src = inspect.getsource(vision)
    for forbidden in ("QTimer", "call_later", "while True", "sleep("):
        assert forbidden not in src
