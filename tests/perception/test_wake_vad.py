"""Headless WakeVadStage tests with synthetic wake/VAD scorers (§19)."""
import numpy as np

from jarvis.bus import Bus, Cancel, State
from jarvis.perception.wake_vad import (
    SAMPLE_RATE,
    VAD_CHUNK,
    WAKE_CHUNK,
    Utterance,
    WakeVadStage,
)

FRAME_S = VAD_CHUNK / SAMPLE_RATE  # 32 ms


def _frame(fill: int = 0) -> np.ndarray:
    return np.full(VAD_CHUNK, fill, dtype=np.int16)


async def _feed(frames):
    """Wrap a list of numpy frames as the AudioFrame async iterator."""
    from jarvis.bus import AudioFrame

    for i, f in enumerate(frames):
        yield AudioFrame(f.tobytes(), float(i) * FRAME_S)


class ScriptedScorer:
    """Returns 1.0 for frame indices in `hot`, else 0.0. Counts calls."""

    def __init__(self, hot: set[int]):
        self.hot = hot
        self.calls = 0
        self.chunk_sizes: list[int] = []

    def __call__(self, chunk: np.ndarray) -> float:
        self.chunk_sizes.append(len(chunk))
        score = 1.0 if self.calls in self.hot else 0.0
        self.calls += 1
        return score


def make_stage(bus=None, wake=None, vad=None, state=State.IDLE, eou_ms=100):
    return WakeVadStage(
        bus or Bus(),
        wake or (lambda c: 0.0),
        vad or (lambda c: 0.0),
        wake_threshold=0.5,
        vad_threshold=0.5,
        end_of_utterance_ms=eou_ms,
        current_state=lambda: state,
    )


async def test_wake_then_silence_yields_utterance_with_preroll():
    # Wake fires on the first 1280-sample window; then 5 speech frames, then silence.
    wake = ScriptedScorer(hot={0})
    speech_idx = set(range(3, 8))
    vad = ScriptedScorer(hot=speech_idx)
    stage = make_stage(wake=wake, vad=vad, eou_ms=100)

    frames = [_frame(i) for i in range(20)]
    utterances = [u async for u in stage.utterances(_feed(frames))]

    assert len(utterances) == 1
    u = utterances[0]
    assert isinstance(u, Utterance)
    # Captured audio includes pre-roll frames from before the wake hit.
    n_samples = len(u.pcm) // 2
    assert n_samples >= 4 * VAD_CHUNK  # >100ms of trailing silence + speech + pre-roll


async def test_wake_scorer_receives_80ms_chunks():
    wake = ScriptedScorer(hot=set())
    stage = make_stage(wake=wake)
    frames = [_frame() for _ in range(10)]  # 10*512 = 5120 samples = 4 full wake chunks
    async for _ in stage.utterances(_feed(frames)):
        pass
    assert all(size == WAKE_CHUNK for size in wake.chunk_sizes)
    assert wake.calls == 4


async def test_activate_starts_capture_without_wake():
    vad = ScriptedScorer(hot=set())  # all silence: capture ends after eou
    stage = make_stage(vad=vad, eou_ms=64)  # 2 frames of silence end it
    stage.activate()
    frames = [_frame() for _ in range(10)]
    utterances = [u async for u in stage.utterances(_feed(frames))]
    assert len(utterances) == 1


async def test_max_length_cutoff():
    # Continuous speech, wake at start: must cut at MAX_UTTERANCE_S not hang.
    wake = ScriptedScorer(hot={0})
    stage = make_stage(wake=wake, vad=lambda c: 1.0, eou_ms=100)
    n = int(16.0 / FRAME_S)  # 16 s of speech
    utterances = [u async for u in stage.utterances(_feed(_frame() for _ in range(n)))]
    assert len(utterances) == 1
    assert len(utterances[0].pcm) // 2 / SAMPLE_RATE <= 15.5


async def test_speech_during_speaking_publishes_barge_in_cancel():
    bus = Bus()
    q = bus.subscribe()
    stage = make_stage(bus=bus, vad=lambda c: 1.0, state=State.SPEAKING)
    frames = [_frame() for _ in range(3)]
    async for _ in stage.utterances(_feed(frames)):
        pass
    assert isinstance(q.get_nowait(), Cancel)


async def test_no_barge_in_when_idle():
    bus = Bus()
    q = bus.subscribe()
    stage = make_stage(bus=bus, vad=lambda c: 1.0, state=State.IDLE)
    async for _ in stage.utterances(_feed([_frame() for _ in range(3)])):
        pass
    assert q.empty()


async def test_barge_in_cancel_is_debounced_per_episode():
    bus = Bus()
    q = bus.subscribe()
    stage = make_stage(bus=bus, vad=lambda c: 1.0, state=State.SPEAKING)
    async for _ in stage.utterances(_feed([_frame() for _ in range(10)])):
        pass
    assert isinstance(q.get_nowait(), Cancel)
    assert q.empty()  # exactly one Cancel for continuous speech
