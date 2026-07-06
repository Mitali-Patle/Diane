"""Streaming STT: Utterance → PartialTranscript segments → FinalTranscript (FR2).

faster-whisper on CPU (int8, Tier A). Partials are emitted per decoded segment
while the utterance is being transcribed, so the orchestrator can show/act on
text before the full decode completes. Indian-English contact names from
config bias decoding via initial_prompt (the whisper-native glossary channel).

Transcripts are never written to disk (D-2); they exist only as bus/queue
payloads and the orchestrator's in-memory rolling context.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Protocol

import numpy as np

from jarvis import config
from jarvis.bus import FinalTranscript, PartialTranscript
from jarvis.perception.wake_vad import SAMPLE_RATE, Utterance

log = logging.getLogger("jarvis.stt")


class TranscriptionModel(Protocol):
    """The slice of faster_whisper.WhisperModel we use; injectable for tests."""

    def transcribe(self, audio: np.ndarray, **kwargs) -> tuple[Iterable, object]: ...


def _load_whisper() -> TranscriptionModel:
    from faster_whisper import WhisperModel

    cfg = config.get()["stt"]
    root = config.ROOT / "models" / "whisper"
    kwargs = dict(
        device="cpu",
        compute_type=cfg["compute_type"],
        download_root=str(root),
    )
    try:
        # NFR1: once the snapshot exists, never touch the network again.
        return WhisperModel(cfg["model"], local_files_only=True, **kwargs)
    except Exception:
        return WhisperModel(cfg["model"], **kwargs)  # first run: download


class SpeechToText:
    """Wraps the whisper model; decode runs in a thread so the loop stays live (P5)."""

    def __init__(self, model: TranscriptionModel | None = None) -> None:
        cfg = config.get()["stt"]
        self._model = model if model is not None else _load_whisper()
        self._language: str = cfg["language"]
        names = list(cfg["contact_names"])
        self._initial_prompt = (
            "Names that may occur: " + ", ".join(names) + "." if names else None
        )

    async def transcribe(
        self, utterance: Utterance, turn_id: int
    ) -> AsyncIterator[PartialTranscript | FinalTranscript]:
        """Yield a PartialTranscript per decoded segment, then one FinalTranscript."""
        audio = np.frombuffer(utterance.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        loop = asyncio.get_running_loop()

        segments, _info = await loop.run_in_executor(
            None,
            lambda: self._model.transcribe(
                audio,
                language=self._language,
                initial_prompt=self._initial_prompt,
                beam_size=1,          # greedy: latency over marginal accuracy (§2.1)
                vad_filter=False,     # segmentation already done upstream
                condition_on_previous_text=False,
            ),
        )

        parts: list[str] = []
        iterator = iter(segments)
        while True:
            # Each next() decodes lazily and blocks; keep it off the loop (P5).
            segment = await loop.run_in_executor(None, next, iterator, None)
            if segment is None:
                break
            text = segment.text.strip()
            if not text:
                continue
            parts.append(text)
            yield PartialTranscript(text=" ".join(parts), turn_id=turn_id)

        final = " ".join(parts).strip()
        log.info("transcribed %.2fs of audio, turn=%d", len(audio) / SAMPLE_RATE, turn_id)
        yield FinalTranscript(text=final, turn_id=turn_id)
