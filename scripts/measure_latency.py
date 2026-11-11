"""Scripted latency measurement against §22 budgets (Tier A column).

Measures what is measurable without a microphone:
  - LLM speech-end -> first token (warm)     budget <1500 ms
  - first token -> first audio chunk (TTS)   budget <800 ms
  - wake scorer per-80ms-chunk cost          must be << 80 ms (real-time)
  - VAD per-32ms-chunk cost                  must be << 32 ms

Run:  .venv/bin/python scripts/measure_latency.py
"""
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.brain.chunker import phrases  # noqa: E402
from jarvis.brain.orchestrator import OllamaClient, Orchestrator  # noqa: E402
from jarvis.bus import Bus, FinalTranscript  # noqa: E402
from jarvis.perception.wake_vad import SileroVad, make_wake_scorer  # noqa: E402
from jarvis.voice.tts import TextToSpeech  # noqa: E402


async def main() -> None:
    print("=== §22 latency check (Tier A budgets) ===")

    # wake scorer real-time factor
    wake = make_wake_scorer("hey_jarvis")
    chunk = np.zeros(1280, dtype=np.int16)
    for _ in range(5):
        wake(chunk)  # warm
    t0 = time.perf_counter()
    n = 50
    for _ in range(n):
        wake(chunk)
    per = (time.perf_counter() - t0) / n * 1000
    print(f"wake scorer: {per:.1f} ms per 80 ms chunk  (budget: realtime, <150ms detect)")

    vad = SileroVad(str(ROOT / "models/silero_vad.onnx"))
    small = np.zeros(512, dtype=np.int16)
    for _ in range(5):
        vad(small)
    t0 = time.perf_counter()
    for _ in range(n):
        vad(small)
    per = (time.perf_counter() - t0) / n * 1000
    print(f"VAD: {per:.2f} ms per 32 ms chunk")

    # custom wake model, if present
    if (ROOT / "models/hi_diane.onnx").exists():
        custom = make_wake_scorer("hi_diane")
        for _ in range(5):
            custom(chunk)
        t0 = time.perf_counter()
        for _ in range(n):
            custom(chunk)
        per = (time.perf_counter() - t0) / n * 1000
        print(f"hi_diane scorer: {per:.1f} ms per 80 ms chunk")

    # LLM + TTS pipeline (warm)
    llm = OllamaClient()
    await llm.warmup()
    orch = Orchestrator(Bus(), llm)
    tts = TextToSpeech()

    for run in (1, 2):
        t0 = time.monotonic()
        first_token = first_audio = None
        async for phrase in phrases(orch.run_turn(FinalTranscript("Tell me a fun fact.", run))):
            if first_token is None:
                first_token = time.monotonic() - t0
            async for _chunk in tts.synthesize(phrase):
                if first_audio is None:
                    first_audio = time.monotonic() - t0
                break
            break
        print(
            f"turn {run}: first phrase {first_token:.2f}s, first audio {first_audio:.2f}s "
            f"(budgets: token <1.5s, audio <2.3s total)"
        )


if __name__ == "__main__":
    asyncio.run(main())
