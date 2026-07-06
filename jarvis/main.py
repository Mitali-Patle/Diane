"""Composition root: builds the shared event loop, threads, tasks, and supervisor (§7).

Full pipeline: mic → wake+VAD → STT → orchestrator → chunker → TTS → speaker,
concurrent capture for barge-in (FR7), plus the avatar view on the same
qasync loop when enabled (§7 domain 2). The pipeline runs identically with
avatar.enabled: false (P9 — it is a view, never a dependency).
"""
from __future__ import annotations

import asyncio

from jarvis import config, logs
from jarvis.actions import Actions
from jarvis.audio.input_thread import AudioInput
from jarvis.audio.output_thread import AudioOutput
from jarvis.brain.chunker import phrases
from jarvis.brain.orchestrator import OllamaClient, Orchestrator
from jarvis.bus import AvatarCommand, AvatarStatus, Bus, Cancel, Phrase, State
from jarvis.perception.stt import SpeechToText
from jarvis.perception.vision import Vision
from jarvis.perception.wake_vad import SileroVad, Utterance, WakeVadStage, make_wake_scorer
from jarvis.voice.tts import TextToSpeech


async def run() -> None:
    cfg = config.get()
    log = logs.setup()
    bus = Bus()
    log.info("Diane starting (wake=%s, llm=%s)", cfg["wake"]["model"], cfg["llm"]["model"])

    avatar_commands: asyncio.Queue[AvatarCommand] = asyncio.Queue()
    avatar_status: asyncio.Queue[AvatarStatus] = asyncio.Queue()

    output = AudioOutput()
    llm = OllamaClient()
    from jarvis.avatar import packs as pack_discovery  # data-only listing, no Qt

    installed = tuple(pack_discovery.discover())
    orch = Orchestrator(
        bus,
        llm,
        tools=Actions(avatar_out=avatar_commands, installed_packs=installed, vision=Vision()),
        external_playback=True,
    )
    stt = SpeechToText()
    tts = TextToSpeech()
    audio = AudioInput()
    stage = WakeVadStage(
        bus,
        make_wake_scorer(cfg["wake"]["model"]),
        SileroVad(str(config.ROOT / "models" / "silero_vad.onnx")),
        current_state=lambda: orch.state,
        on_capture_start=orch.on_wake,
    )
    orch.add_activate_hook(stage.activate)

    utterance_q: asyncio.Queue[Utterance] = asyncio.Queue()

    async def speak_notice(text: str) -> None:
        """Direct spoken notice, no LLM involved (NFR4/§16 error speech)."""
        async for chunk in tts.synthesize(Phrase(text=text, turn_id=-1, index=0)):
            output.put(chunk)

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

    async def avatar_notices() -> None:
        """Speak avatar pack failures — the user is the operator (§16, NFR5)."""
        while True:
            status = await avatar_status.get()
            if status.error:
                await speak_notice(f"Avatar problem: {status.error}")

    async def supervised(name: str, factory, on_disable: str | None = None) -> None:
        """NFR4: contain stage crashes; restart with backoff; 3 crashes/60s -> stage off."""
        crashes: list[float] = []
        while True:
            try:
                await factory()
                return  # clean completion
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stage %s crashed", name)
                now = asyncio.get_running_loop().time()
                crashes = [t for t in crashes if now - t < 60] + [now]
                if len(crashes) >= 3:
                    log.error("stage %s: 3 crashes in 60s, disabled for this session", name)
                    if on_disable:
                        await speak_notice(on_disable)
                    return
                await asyncio.sleep(1.0)

    audio.start()
    tasks = [
        asyncio.create_task(supervised("events", orch.event_loop), name="events"),
        asyncio.create_task(supervised("capture", capture), name="capture"),
        asyncio.create_task(supervised("flush", flush_on_cancel), name="flush"),
        asyncio.create_task(supervised("converse", converse), name="converse"),
        asyncio.create_task(supervised("warmup", llm.warmup), name="warmup"),
        asyncio.create_task(supervised("avatar-status", avatar_notices), name="avatar-status"),
    ]

    stop = asyncio.Event()  # set by the avatar's Quit menu (P9: quit is our call)
    window = None
    if cfg["avatar"]["enabled"]:
        try:
            window, avatar_tasks = _start_avatar(
                bus, avatar_commands, avatar_status, supervised, request_quit=stop.set
            )
            tasks += avatar_tasks
        except Exception:  # NFR4: the view must never take down the pipeline
            log.exception("avatar failed to start; continuing headless")

    try:
        gather = asyncio.gather(*tasks)
        stopper = asyncio.create_task(stop.wait(), name="stop")
        await asyncio.wait({gather, stopper}, return_when=asyncio.FIRST_COMPLETED)
        gather.cancel()
    except asyncio.CancelledError:
        log.info("Diane shutting down")
        raise
    finally:
        for t in tasks:
            t.cancel()
        if window is not None:
            window.save_state()  # §12: persist avatar state on clean shutdown
        audio.stop()
        output.stop()


def _start_avatar(bus, commands, status, supervised, request_quit):
    """Create the avatar widget on the (qasync) loop and its consumer tasks."""
    import json

    from jarvis.avatar.window import STATE_FILE, AvatarWindow

    window = AvatarWindow(bus, status_out=status, request_quit=request_quit)
    startup_pack = config.get()["avatar"]["active_pack"]
    try:  # runtime pack choice persists across restarts (DL-8, §12)
        startup_pack = json.loads(STATE_FILE.read_text()).get("active_pack", startup_pack)
    except Exception:  # noqa: BLE001
        pass
    if startup_pack == "dot" or not window.load_pack(startup_pack):
        window.use_dot_fallback()
    window.show()

    return window, [
        asyncio.create_task(
            supervised(
                "avatar-events",
                window.consume_events,
                on_disable="The avatar has been disabled for this session.",
            ),
            name="avatar-events",
        ),
        asyncio.create_task(
            supervised("avatar-commands", lambda: window.consume_commands(commands)),
            name="avatar-commands",
        ),
    ]


def main() -> None:
    cfg = config.get()
    logs.setup()
    if cfg["avatar"]["enabled"]:
        try:
            import qasync
            from PySide6.QtWidgets import QApplication

            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)
            loop = qasync.QEventLoop(app)
            asyncio.set_event_loop(loop)
            with loop:
                loop.run_until_complete(run())
            return
        except ImportError:
            logs.setup().warning("PySide6/qasync unavailable; running without avatar")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
