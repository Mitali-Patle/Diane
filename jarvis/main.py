"""Composition root: builds the shared event loop, threads, tasks, and supervisor (§7).

Full pipeline: mic → wake+VAD → STT → orchestrator → chunker → TTS → speaker,
concurrent capture for barge-in (FR7), plus the avatar view on the same
qasync loop when enabled (§7 domain 2). The pipeline runs identically with
avatar.enabled: false (P9 — it is a view, never a dependency).
"""
from __future__ import annotations

import asyncio
import signal
from contextlib import aclosing

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
from jarvis.voice.earcon import wake_chime
from jarvis.voice.tts import TextToSpeech

SHUTDOWN_TIMEOUT_S = 3.0  # P5: shutdown must never hang


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

    def installed() -> tuple[str, ...]:  # rescanned per set_avatar call (Appendix B.3)
        return tuple(pack_discovery.discover())

    orch = Orchestrator(
        bus,
        llm,
        tools=Actions(avatar_out=avatar_commands, installed_packs=installed, vision=Vision()),
        external_playback=True,
    )
    stt = SpeechToText()
    tts = TextToSpeech()
    audio = AudioInput()
    chime = wake_chime(tts.sample_rate) if cfg["audio"].get("wake_chime", True) else None

    def on_capture_start() -> None:
        """Wake (or click / barge-in) -> LISTENING, plus the 'go ahead' chime."""
        prev = orch.state
        barge_in = prev is State.SPEAKING
        if prev in (State.ERROR, State.THINKING):
            # wake_vad only publishes barge-in Cancel during SPEAKING (§9), so
            # nothing else would flush an apology that's still playing.
            output.flush()
        orch.on_wake()
        if chime is not None and not barge_in:  # mid-reply the user is already talking
            output.put(chime)

    stage = WakeVadStage(
        bus,
        make_wake_scorer(cfg["wake"]["model"]),
        SileroVad(str(config.ROOT / "models" / "silero_vad.onnx")),
        current_state=lambda: orch.state,
        on_capture_start=on_capture_start,
        on_capture_abandoned=orch.end_listening,
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
                if not stage.capturing:  # a newer wake may already be capturing
                    orch.end_listening()  # nothing said: LISTENING -> IDLE, not stuck
                continue
            gen = phrases(orch.run_turn(final))
            try:
                async for phrase in gen:
                    async with aclosing(tts.synthesize(phrase)) as audio_chunks:
                        async for chunk in audio_chunks:
                            if not orch.is_voicing:
                                break  # barge-in mid-synthesis: drop stale chunks (SC3)
                            output.put(chunk)
                            await output.wait_below(30.0)  # backpressure (SC6)
                    if not orch.is_voicing:
                        break
            finally:
                # Close promptly: phrases() closes run_turn beneath it, so the
                # turn's accounting and HTTP stream finish NOW, not at GC time.
                await gen.aclose()
            await output.drain()
            orch.finish_turn()

    async def warm_models() -> None:
        """First-turn latency (SC2): pay STT/TTS init at boot, not on turn 1."""
        await stt.warmup()
        await tts.warmup()

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

    stop = asyncio.Event()  # Quit menu or SIGTERM/SIGINT (P9: quit is our call)
    _install_signal_handlers(stop)

    tasks: list[asyncio.Task] = []
    window = None
    try:
        audio.start()
        tasks += [
            asyncio.create_task(supervised("events", orch.event_loop), name="events"),
            asyncio.create_task(supervised("capture", capture), name="capture"),
            asyncio.create_task(supervised("flush", flush_on_cancel), name="flush"),
            asyncio.create_task(supervised("converse", converse), name="converse"),
            asyncio.create_task(supervised("warmup", llm.warmup), name="warmup"),
            asyncio.create_task(supervised("warm-models", warm_models), name="warm-models"),
            asyncio.create_task(supervised("avatar-status", avatar_notices), name="avatar-status"),
        ]

        if cfg["avatar"]["enabled"]:
            try:
                window, avatar_tasks = _start_avatar(
                    bus, avatar_commands, avatar_status, supervised, request_quit=stop.set
                )
                tasks += avatar_tasks
            except Exception:  # NFR4: the view must never take down the pipeline
                log.exception("avatar failed to start; continuing headless")
        await stop.wait()
        log.info("Diane shutting down")
    finally:
        # Cancel AND await every task while the loop is still running. Leaving
        # them to be finalized after the loop stops is what produced "Event loop
        # stopped before Future completed", "no running event loop" and
        # "Internal C++ object already deleted" tracebacks on every shutdown.
        for t in tasks:
            t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), SHUTDOWN_TIMEOUT_S
            )
        except (TimeoutError, asyncio.CancelledError):
            log.warning("some stages did not stop within %.0fs", SHUTDOWN_TIMEOUT_S)
        if window is not None:
            window.save_state()  # §12: persist avatar state on clean shutdown
            window.close()       # stops its frame/eye timers (P5)
        audio.stop()
        output.stop()
        log.info("Diane stopped")


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """systemd stops us with SIGTERM: turn it into a cooperative shutdown (P5)."""
    loop = asyncio.get_running_loop()

    def request_stop(*_args) -> None:
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # qasync's loop doesn't implement add_signal_handler; a plain handler
            # works because the audio thread wakes the loop ~30x per second.
            signal.signal(sig, request_stop)


def _start_avatar(bus, commands, status, supervised, request_quit):
    """Create the avatar widget on the (qasync) loop and its consumer tasks."""
    import json

    from jarvis.avatar.window import AvatarWindow, state_file

    window = AvatarWindow(bus, status_out=status, request_quit=request_quit)
    startup_pack = config.get()["avatar"]["active_pack"]
    try:  # runtime pack choice persists across restarts (DL-8, §12)
        startup_pack = json.loads(state_file().read_text()).get("active_pack", startup_pack)
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
        except ImportError:
            logs.setup().warning("PySide6/qasync unavailable; running without avatar")
        else:
            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)
            loop = qasync.QEventLoop(app)
            asyncio.set_event_loop(loop)
            with loop:
                loop.run_until_complete(run())
            return
    asyncio.run(run())


if __name__ == "__main__":
    main()
