"""Orchestrator: sole owner of the assistant state machine (§7) and rolling context (§12).

State machine:
    IDLE -(wake | AVATAR_ACTIVATE)-> LISTENING -(VAD end)-> THINKING
         -(first token)-> SPEAKING -(complete | cancel)-> IDLE
    barge-in: SPEAKING -> LISTENING (Cancel published by wake_vad, §9)

Every transition publishes StateChanged on the bus; the avatar renders these,
never its own copy. The LLM client is injected (Protocol) so all state-machine
logic is headless-testable without Ollama (§19).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Protocol

from jarvis import config
from jarvis.bus import (
    AvatarActivate,
    Bus,
    Cancel,
    FinalTranscript,
    State,
    StateChanged,
    Token,
)

log = logging.getLogger("jarvis.orchestrator")

SYSTEM_PROMPT = (
    "You are Diane, a concise voice assistant running fully locally on the "
    "owner's laptop. Answers are spoken aloud: keep them short, conversational, "
    "and free of markdown, lists, or code unless explicitly asked to spell "
    "something out. You can execute whitelisted tools when asked; never invent "
    "capabilities you don't have."
)


class LlmClient(Protocol):
    """Streaming chat: messages in, content deltas out. Injectable for tests."""

    def chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class OllamaClient:
    """The one sanctioned external contract: localhost:11434 (§11, NFR1)."""

    def __init__(self) -> None:
        import ollama

        cfg = config.get()["llm"]
        self._client = ollama.AsyncClient(host=cfg["host"])
        self._model = cfg["model"]

    async def chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        stream = await self._client.chat(model=self._model, messages=messages, stream=True)
        async for part in stream:
            delta = part["message"]["content"]
            if delta:
                yield delta


class Orchestrator:
    """Owns state + context. Other stages call into it; it publishes StateChanged."""

    def __init__(self, bus: Bus, llm: LlmClient, system_prompt: str = SYSTEM_PROMPT) -> None:
        self._bus = bus
        self._llm = llm
        self._context_turns: int = config.get()["llm"]["context_turns"]
        self._messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self._state = State.IDLE
        self._turn_id = 0
        self._turn_active = False
        self._cancelled = asyncio.Event()
        self._on_activate: list = []  # callbacks for AVATAR_ACTIVATE (wake stage hook)

    # -- state ----------------------------------------------------------- #

    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, new: State) -> None:
        if new is self._state:
            return
        self._state = new
        self._bus.publish(StateChanged(new))
        log.info("state -> %s (turn=%d)", new.value, self._turn_id)

    # -- external triggers ------------------------------------------------ #

    def on_wake(self) -> None:
        """Wake word detected (called by the wake stage's capture-start hook).

        Runs synchronously BEFORE the bus Cancel is dequeued on barge-in, so
        it must itself cancel any in-flight turn — guarding Cancel handling on
        state alone would race with this transition and drop the cancellation.
        """
        if self._turn_active:
            self._cancelled.set()
        if self._state in (State.IDLE, State.SPEAKING):
            self._set_state(State.LISTENING)

    def add_activate_hook(self, fn) -> None:
        """Register a callable run when AVATAR_ACTIVATE is accepted (e.g. wake_stage.activate)."""
        self._on_activate.append(fn)

    async def event_loop(self) -> None:
        """Consume bus events: Cancel (barge-in) and AVATAR_ACTIVATE. Cancellable (P5)."""
        q = self._bus.subscribe()
        try:
            while True:
                ev = await q.get()
                if isinstance(ev, Cancel) and (
                    self._turn_active or self._state is State.SPEAKING
                ):
                    self._cancelled.set()
                    if self._state is State.SPEAKING:
                        self._set_state(State.LISTENING)
                elif isinstance(ev, AvatarActivate):
                    # Valid only in IDLE; ignored otherwise (DL-10).
                    if self._state is State.IDLE:
                        for fn in self._on_activate:
                            fn()
                        self._set_state(State.LISTENING)
        finally:
            self._bus.unsubscribe(q)

    # -- the turn ---------------------------------------------------------- #

    def _trim_context(self) -> None:
        """Keep system prompt + last N user/assistant exchanges (§12)."""
        keep = self._context_turns * 2
        if len(self._messages) - 1 > keep:
            self._messages = [self._messages[0]] + self._messages[-keep:]

    async def run_turn(self, final: FinalTranscript) -> AsyncIterator[Token]:
        """Drive one turn: THINKING -> stream tokens (SPEAKING at first token) -> IDLE.

        The caller (chunker/TTS at M5, console at M3) consumes the Token stream.
        A barge-in Cancel stops generation mid-stream; state then stays LISTENING
        (set by event_loop), not IDLE.
        """
        self._turn_id = final.turn_id
        self._turn_active = True
        self._cancelled.clear()
        self._set_state(State.THINKING)
        self._messages.append({"role": "user", "content": final.text})
        self._trim_context()

        generated: list[str] = []
        try:
            async for delta in self._llm.chat(list(self._messages)):
                if self._cancelled.is_set():
                    log.info("turn %d cancelled (barge-in)", final.turn_id)
                    break
                if not generated:
                    self._set_state(State.SPEAKING)
                generated.append(delta)
                yield Token(text=delta, turn_id=final.turn_id)
        except Exception:
            self._set_state(State.ERROR)
            log.exception("LLM stream failed, turn=%d", final.turn_id)
            self._set_state(State.IDLE)
            return
        finally:
            self._turn_active = False

        if generated:
            self._messages.append({"role": "assistant", "content": "".join(generated)})
            self._trim_context()
        if not self._cancelled.is_set():
            self._set_state(State.IDLE)
