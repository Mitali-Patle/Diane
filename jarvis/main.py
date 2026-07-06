"""Composition root: builds the shared event loop, threads, tasks, and supervisor (§7).

Wired through M3: mic → wake+VAD → STT → orchestrator → chunker → console.
M5 replaces the console sink with TTS + playback; M8 swaps asyncio.run for
qasync and adds the avatar. Every stage await is cancellable (P5).
"""
from __future__ import annotations

import asyncio

from jarvis import config, logs
from jarvis.actions import Actions
from jarvis.audio.input_thread import AudioInput
from jarvis.brain.chunker import phrases
from jarvis.brain.orchestrator import OllamaClient, Orchestrator
from jarvis.bus import Bus
from jarvis.perception.stt import SpeechToText
from jarvis.perception.wake_vad import OpenWakeWord, SileroVad, WakeVadStage


async def run() -> None:
    cfg = config.get()
    log = logs.setup()
    bus = Bus()
    log.info("Diane starting (wake=%s, llm=%s)", cfg["wake"]["model"], cfg["llm"]["model"])

    orch = Orchestrator(bus, OllamaClient(), tools=Actions())
    stt = SpeechToText()
    audio = AudioInput()
    stage = WakeVadStage(
        bus,
        OpenWakeWord(cfg["wake"]["model"]),
        SileroVad(str(config.ROOT / "models" / "silero_vad.onnx")),
        current_state=lambda: orch.state,
        on_capture_start=orch.on_wake,
    )
    orch.add_activate_hook(stage.activate)

    audio.start()
    event_task = asyncio.create_task(orch.event_loop())
    turn = 0
    try:
        async for utt in stage.utterances(audio.frames()):
            turn += 1
            final = None
            async for ev in stt.transcribe(utt, turn_id=turn):
                final = ev  # last event is the FinalTranscript
            if final is None or not final.text:
                continue
            # M3 sink: print the streamed response; M5 routes phrases to TTS.
            async for phrase in phrases(orch.run_turn(final)):
                print(f"Diane: {phrase.text}", flush=True)
    except asyncio.CancelledError:
        log.info("Diane shutting down")
        raise
    finally:
        event_task.cancel()
        audio.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
