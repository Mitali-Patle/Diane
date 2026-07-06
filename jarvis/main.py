"""Composition root: builds the shared event loop, threads, tasks, and supervisor (§7).

Wired through M5: mic → wake+VAD → STT → orchestrator → chunker → TTS → speaker,
with concurrent capture so barge-in works while Diane is speaking (FR7).
M8 swaps asyncio.run for qasync and adds the avatar. Every stage await is
cancellable (P5).
"""
from __future__ import annotations

import asyncio

from jarvis import config, logs
from jarvis.actions import Actions
from jarvis.audio.input_thread import AudioInput
from jarvis.audio.output_thread import AudioOutput
from jarvis.brain.chunker import phrases
from jarvis.brain.orchestrator import OllamaClient, Orchestrator
from jarvis.bus import Bus, Cancel, State
from jarvis.perception.stt import SpeechToText
from jarvis.perception.vision import Vision
from jarvis.perception.wake_vad import OpenWakeWord, SileroVad, Utterance, WakeVadStage
from jarvis.voice.tts import TextToSpeech


async def run() -> None:
    cfg = config.get()
    log = logs.setup()
    bus = Bus()
    log.info("Diane starting (wake=%s, llm=%s)", cfg["wake"]["model"], cfg["llm"]["model"])

    output = AudioOutput()
    orch = Orchestrator(
        bus, OllamaClient(), tools=Actions(vision=Vision()), external_playback=True
    )
    stt = SpeechToText()
    tts = TextToSpeech()
    audio = AudioInput()
    stage = WakeVadStage(
        bus,
        OpenWakeWord(cfg["wake"]["model"]),
        SileroVad(str(config.ROOT / "models" / "silero_vad.onnx")),
        current_state=lambda: orch.state,
        on_capture_start=orch.on_wake,
    )
    orch.add_activate_hook(stage.activate)

    utterance_q: asyncio.Queue[Utterance] = asyncio.Queue()

    async def capture() -> None:
        """Producer: always listening, even while Diane speaks (barge-in, FR7)."""
        async for utt in stage.utterances(audio.frames()):
            utterance_q.put_nowait(utt)

    async def flush_on_cancel() -> None:
        """Barge-in playback stop: buffer flush in <100 ms (SC3)."""
        q = bus.subscribe()
        try:
            while True:
                if isinstance(await q.get(), Cancel):
                    output.flush()
        finally:
            bus.unsubscribe(q)

    async def converse() -> None:
        """Consumer: utterance → transcript → LLM turn → phrases → TTS → speaker."""
        turn = 0
        while True:
            utt = await utterance_q.get()
            turn += 1
            final = None
            async for ev in stt.transcribe(utt, turn_id=turn):
                final = ev
            if final is None or not final.text:
                continue
            gen = phrases(orch.run_turn(final))
            try:
                async for phrase in gen:
                    async for chunk in tts.synthesize(phrase):
                        if orch.state is not State.SPEAKING:
                            break  # barge-in mid-synthesis: drop stale chunks (SC3)
                        output.put(chunk)
                        await output.wait_below(30.0)  # backpressure (SC6)
                    if orch.state is not State.SPEAKING:
                        break
            finally:
                # Close promptly so run_turn's turn-accounting finally runs NOW,
                # not whenever the event loop garbage-finalizes the generator.
                await gen.aclose()
            await output.drain()
            orch.finish_turn()

    audio.start()
    tasks = [
        asyncio.create_task(orch.event_loop(), name="events"),
        asyncio.create_task(capture(), name="capture"),
        asyncio.create_task(flush_on_cancel(), name="flush"),
        asyncio.create_task(converse(), name="converse"),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("Diane shutting down")
        raise
    finally:
        for t in tasks:
            t.cancel()
        audio.stop()
        output.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
