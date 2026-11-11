"""SpeechToText plumbing tests with an injected fake model (§19).

Accuracy (SC5) is validated against the owner-recorded set at M7; these tests
cover the streaming contract: partials accumulate, final equals the whole.
"""
import numpy as np

from jarvis.bus import FinalTranscript, PartialTranscript
from jarvis.perception.stt import SpeechToText
from jarvis.perception.wake_vad import Utterance


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class FakeModel:
    def __init__(self, texts):
        self.texts = texts
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(FakeSegment(t) for t in self.texts), object()


def _utt(seconds: float = 1.0) -> Utterance:
    pcm = np.zeros(int(16000 * seconds), dtype=np.int16).tobytes()
    return Utterance(pcm=pcm, started_at=0.0, ended_at=seconds)


async def test_partials_accumulate_then_final():
    stt = SpeechToText(model=FakeModel([" Hello", " there ", "Priya. "]))
    events = [e async for e in stt.transcribe(_utt(), turn_id=7)]

    partials = [e for e in events if isinstance(e, PartialTranscript)]
    finals = [e for e in events if isinstance(e, FinalTranscript)]
    assert [p.text for p in partials] == [
        "Hello",
        "Hello there",
        "Hello there Priya.",
    ]
    assert len(finals) == 1
    assert finals[0].text == "Hello there Priya."
    assert finals[0].turn_id == 7


async def test_empty_audio_still_yields_final():
    stt = SpeechToText(model=FakeModel([]))
    events = [e async for e in stt.transcribe(_utt(0.1), turn_id=1)]
    assert len(events) == 1
    assert isinstance(events[0], FinalTranscript)
    assert events[0].text == ""


async def test_contact_names_bias_initial_prompt(monkeypatch, tmp_path):
    # Rebuild config with contact names and check they reach initial_prompt.
    import yaml

    from jarvis import config as cfgmod

    raw = yaml.safe_load(open(cfgmod.ROOT / "config.yaml"))
    raw["stt"]["contact_names"] = ["Priya", "Arjun"]
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw))
    frozen = cfgmod.load(p)
    monkeypatch.setattr(cfgmod, "_config", frozen)

    fake = FakeModel(["ok"])
    stt = SpeechToText(model=fake)
    async for _ in stt.transcribe(_utt(), turn_id=1):
        pass
    assert "Priya" in fake.kwargs["initial_prompt"]
    assert "Arjun" in fake.kwargs["initial_prompt"]


class ScoredSegment(FakeSegment):
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=-0.2):
        super().__init__(text)
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


async def test_noise_segments_and_stock_hallucinations_dropped():
    class Model(FakeModel):
        def transcribe(self, audio, **kwargs):
            return iter(self.texts), object()

    noisy = Model([ScoredSegment(" Hello", no_speech_prob=0.9, avg_logprob=-1.5)])
    events = [e async for e in SpeechToText(model=noisy).transcribe(_utt(), turn_id=1)]
    assert events[-1].text == ""
    stock = Model([ScoredSegment(" Thank you.")])
    events = [e async for e in SpeechToText(model=stock).transcribe(_utt(), turn_id=1)]
    assert events[-1].text == ""
    real = Model([ScoredSegment(" Thank you, Diane, that helps.")])
    events = [e async for e in SpeechToText(model=real).transcribe(_utt(), turn_id=1)]
    assert events[-1].text == "Thank you, Diane, that helps."
