"""Wake-word + VAD stage: mic frames → wake detection → utterance segmentation (§7, FR1).

Consumes AudioFrame blocks (512 samples @ 16 kHz), runs openWakeWord on
80 ms windows, and after a wake (or AVATAR_ACTIVATE-equivalent trigger)
segments the utterance with Silero VAD using the configured trailing-silence
window (~700 ms, §22). Emits Utterance payloads for the STT stage.

This module is the ONLY publisher of barge-in (§9): when speech is detected
while the orchestrator is SPEAKING, it publishes Cancel on the bus.

Wake/VAD engines are injected as plain callables so the segmentation logic
is unit-testable headless with synthetic scores (§19).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import numpy as np

from jarvis import config
from jarvis.bus import AudioFrame, Bus, Cancel, State

log = logging.getLogger("jarvis.wake_vad")

SAMPLE_RATE = 16000
VAD_CHUNK = 512          # Silero v5 operates on 512-sample chunks @ 16 kHz
WAKE_CHUNK = 1280        # openWakeWord operates on 80 ms windows
PRE_ROLL_S = 0.5         # audio kept from before speech onset
MAX_UTTERANCE_S = 15.0
GRACE_S = 5.0            # after wake: wait this long for speech to START before giving up
REFRACTORY_S = 1.0       # after an utterance: ignore wake hits (scorer buffer residue)
BARGE_IN_TURN = -1       # Cancel.turn_id sentinel: "whatever turn is speaking now"


@dataclass(frozen=True, slots=True)
class Utterance:
    """A complete user utterance, ready for STT (16 kHz mono int16 PCM)."""

    pcm: bytes
    started_at: float
    ended_at: float


# score callables: pcm int16 numpy array -> float in [0, 1]
WakeScorer = Callable[[np.ndarray], float]
VadScorer = Callable[[np.ndarray], float]


class OpenWakeWord:
    """openWakeWord wrapper (ONNX, CPU). Import deferred so tests stay light."""

    def __init__(self, model_name: str) -> None:
        from openwakeword.model import Model

        self._model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self._name = list(self._model.models.keys())[0]

    def __call__(self, chunk: np.ndarray) -> float:
        return float(self._model.predict(chunk)[self._name])

    def reset(self) -> None:
        self._model.reset()


class CustomWakeWord:
    """Scorer for owner-trained wake models (e.g. models/hi_diane.onnx, DL-12).

    Uses the same openWakeWord streaming feature frontend as the pretrained
    models (zero train/serve skew), with a small ONNX classifier head over a
    16-frame embedding window.
    """

    WINDOW = 16

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort
        from openwakeword.utils import AudioFeatures

        self._feats = AudioFeatures(inference_framework="onnx")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name

    def __call__(self, chunk: np.ndarray) -> float:
        self._feats(chunk)  # streaming embedding update (80 ms per call)
        buf = np.array(self._feats.feature_buffer)
        if len(buf) < self.WINDOW:
            return 0.0
        x = buf[-self.WINDOW:].ravel()[None].astype(np.float32)
        # skl2onnx MLP outputs [label, probabilities]; P(wake) = probabilities[0, 1]
        prob = self._sess.run(None, {self._input: x})[1]
        return float(prob[0, 1])

    def reset(self) -> None:
        from openwakeword.utils import AudioFeatures

        self._feats = AudioFeatures(inference_framework="onnx")


def make_wake_scorer(model_name: str) -> WakeScorer:
    """Factory: custom trained model if models/<name>.onnx exists, else pretrained.

    A configured custom model that has gone missing falls back to hey_jarvis
    (logged) rather than crash-looping the service before the supervisor exists.
    """
    from jarvis.config import ROOT

    custom = ROOT / "models" / f"{model_name}.onnx"
    if custom.exists():
        return CustomWakeWord(str(custom))
    try:
        return OpenWakeWord(model_name)
    except Exception:
        log.exception("wake model %r unavailable; falling back to hey_jarvis", model_name)
        return OpenWakeWord("hey_jarvis")


class SileroVad:
    """Silero VAD v5 via onnxruntime directly (no torch dependency).

    v5 expects each 512-sample chunk to be prefixed with the last 64 samples
    of the previous chunk (the official wrapper does this internally) — feeding
    bare chunks silently degrades probabilities to ~0 on real speech.
    """

    CONTEXT = 64

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        audio = chunk.astype(np.float32) / 32768.0
        with_context = np.concatenate([self._context, audio]).reshape(1, -1)
        self._context = audio[-self.CONTEXT:]
        prob, self._state = self._sess.run(
            None, {"input": with_context, "state": self._state, "sr": self._sr}
        )
        return float(prob.item())

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)


class WakeVadStage:
    """Segments the mic stream into utterances gated by the wake word.

    States: waiting-for-wake → capturing (until trailing silence or max length).
    A concurrent trigger (click-to-activate routed by the orchestrator) can
    start capture without a wake hit via `activate()`.
    """

    def __init__(
        self,
        bus: Bus,
        wake: WakeScorer,
        vad: VadScorer,
        *,
        wake_threshold: float | None = None,
        vad_threshold: float | None = None,
        end_of_utterance_ms: int | None = None,
        current_state: Callable[[], State] = lambda: State.IDLE,
        on_capture_start: Callable[[], None] | None = None,
    ) -> None:
        cfg = config.get()["wake"]
        self._bus = bus
        self._wake = wake
        self._vad = vad
        self._wake_threshold = wake_threshold if wake_threshold is not None else cfg["threshold"]
        self._vad_threshold = vad_threshold if vad_threshold is not None else cfg["vad_threshold"]
        self._eou_s = (end_of_utterance_ms or cfg["end_of_utterance_ms"]) / 1000.0
        self._current_state = current_state
        self._activated = asyncio.Event()
        self._wake_buf = np.empty(0, dtype=np.int16)
        self._cancelled_this_episode = False
        self._on_capture_start = on_capture_start

    def activate(self) -> None:
        """External trigger equivalent to a wake hit (AVATAR_ACTIVATE path)."""
        self._activated.set()

    def _wake_hit(self, samples: np.ndarray) -> bool:
        """Feed samples to openWakeWord in 80 ms windows; True on threshold cross."""
        self._wake_buf = np.concatenate([self._wake_buf, samples])
        hit = False
        while len(self._wake_buf) >= WAKE_CHUNK:
            chunk, self._wake_buf = self._wake_buf[:WAKE_CHUNK], self._wake_buf[WAKE_CHUNK:]
            if self._wake(chunk) >= self._wake_threshold:
                hit = True
        return hit

    async def utterances(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Utterance]:
        pre_roll: deque[np.ndarray] = deque(maxlen=int(PRE_ROLL_S * SAMPLE_RATE / VAD_CHUNK))
        capturing = False
        captured: list[np.ndarray] = []
        # Timing is audio-time (samples consumed), not wall clock: deterministic
        # under test and correct if processing runs faster/slower than real time.
        trailing_silence_s = 0.0
        utterance_s = 0.0
        seen_speech = False   # trailing-silence cutoff arms only after speech starts
        audio_clock = 0.0
        refractory_until = 0.0
        capture_start = 0.0

        def _reset_wake() -> None:
            # The scorer's internal feature buffer still contains the wake
            # phrase after a hit and would re-fire until it scrolls out.
            self._wake_buf = np.empty(0, dtype=np.int16)
            reset = getattr(self._wake, "reset", None)
            if callable(reset):
                reset()

        async for frame in frames:
            samples = np.frombuffer(frame.pcm, dtype=np.int16)
            frame_s = len(samples) / SAMPLE_RATE
            audio_clock += frame_s
            if not capturing:
                pre_roll.append(samples)
                is_speech = self._vad(samples) >= self._vad_threshold
                if is_speech and self._current_state() is State.SPEAKING:
                    # Barge-in: sole publisher of Cancel from voice (§9, FR7).
                    # Debounced: one Cancel per SPEAKING episode, not per frame.
                    if not self._cancelled_this_episode:
                        self._cancelled_this_episode = True
                        self._bus.publish(Cancel(turn_id=BARGE_IN_TURN))
                        # Barge-in speech becomes the next utterance: the pre-roll
                        # already holds its onset, so start capturing now (FR7).
                        self._activated.set()
                elif self._current_state() is not State.SPEAKING:
                    self._cancelled_this_episode = False
                in_refractory = audio_clock < refractory_until
                woke = self._wake_hit(samples) and not in_refractory
                if woke or self._activated.is_set():
                    self._activated.clear()
                    capturing = True
                    captured = list(pre_roll)
                    trailing_silence_s = 0.0
                    utterance_s = 0.0
                    seen_speech = False
                    capture_start = time.monotonic()
                    _reset_wake()
                    if self._on_capture_start is not None:
                        self._on_capture_start()
                    log.info("wake detected -> capturing (t=%.3f)", frame.timestamp)
            else:
                captured.append(samples)
                utterance_s += frame_s
                if self._vad(samples) >= self._vad_threshold:
                    seen_speech = True
                    trailing_silence_s = 0.0
                else:
                    trailing_silence_s += frame_s
                too_long = utterance_s > MAX_UTTERANCE_S
                gave_up = not seen_speech and utterance_s > GRACE_S
                done = (seen_speech and trailing_silence_s >= self._eou_s) or too_long
                if done or gave_up:
                    pcm = np.concatenate(captured).tobytes()
                    capturing = False
                    captured = []
                    pre_roll.clear()
                    _reset_wake()
                    refractory_until = audio_clock + REFRACTORY_S
                    if gave_up or not seen_speech:
                        log.info("capture abandoned: no speech within %.1fs", GRACE_S)
                        continue
                    log.info(
                        "utterance complete: %.2fs (%s)",
                        len(pcm) / 2 / SAMPLE_RATE,
                        "max-length" if too_long else "silence",
                    )
                    yield Utterance(
                        pcm=pcm, started_at=capture_start, ended_at=time.monotonic()
                    )
