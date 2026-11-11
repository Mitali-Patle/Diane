"""Orchestrator: sole owner of the assistant state machine (§7) and rolling context (§12).

State machine:
    IDLE -(wake | AVATAR_ACTIVATE)-> LISTENING -(VAD end)-> THINKING
         -(first token)-> SPEAKING -(complete | cancel)-> IDLE
    LISTENING -(capture abandoned | empty transcript)-> IDLE
    barge-in: SPEAKING -> LISTENING (Cancel published by wake_vad, §9)
    LLM failure: THINKING -> ERROR (apology spoken) -> IDLE

Every transition publishes StateChanged on the bus; the avatar renders these,
never its own copy. The LLM client is injected (Protocol) so all state-machine
logic is headless-testable without Ollama (§19).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import aclosing, suppress
from typing import Protocol, TypeVar

from jarvis import config
from jarvis.bus import (
    AvatarActivate,
    Bus,
    Cancel,
    FinalTranscript,
    State,
    StateChanged,
    Token,
    ToolCall,
)

log = logging.getLogger("jarvis.orchestrator")

_T = TypeVar("_T")

_MAX_TOOL_ROUNDS = 3  # tool -> result -> generation loops per turn; then answer as-is

SYSTEM_PROMPT = (
    "You are Diane, a concise voice assistant running fully locally on the "
    "owner's laptop. Answers are spoken aloud: keep them short (one to three "
    "sentences), conversational, and free of markdown, lists, emoji, or code "
    "unless explicitly asked to spell something out. Only call a tool when the "
    "user clearly asks for something a tool does; otherwise just answer. Never "
    "invent capabilities you don't have."
)

# Spoken when the LLM is unreachable or crashes (§16: errors are spoken, never silent).
APOLOGY_UNREACHABLE = (
    "Sorry, I can't reach my language model right now. Is the Ollama service running?"
)
APOLOGY_GENERIC = "Sorry, something went wrong while I was thinking. Please try again."


class TurnCancelled(Exception):
    """Internal: a barge-in arrived while awaiting a tool."""


class LlmClient(Protocol):
    """Streaming chat: yields text deltas (str) and proposed ToolCalls (§13).

    The model only ever *proposes* tools; resolution and execution live in
    actions/ (P1 — the model proposes, the code disposes).
    """

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[str | ToolCall]: ...


class ToolRunner(Protocol):
    """actions layer entry point: validate + execute one proposed call (P1/P2)."""

    async def run(self, call: ToolCall, turn_id: int) -> str: ...

    def specs(self) -> list[dict]: ...


def _is_connection_error(exc: BaseException) -> bool:
    """True for 'Ollama isn't there' failures (without importing httpx here)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ConnectionError, OSError)) or "ConnectError" in type(cur).__name__:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


class OllamaClient:
    """The one sanctioned external contract: localhost:11434 (§11, NFR1)."""

    def __init__(self) -> None:
        import ollama

        cfg = config.get()["llm"]
        self._client = ollama.AsyncClient(host=cfg["host"])
        self._model = cfg["model"]
        self._keep_alive = cfg["keep_alive"]
        # Generation options (num_ctx, num_predict, temperature, ...) — a small
        # context and a reply cap keep prompt-eval and generation fast (SC2).
        self._options = dict(cfg.get("options") or {})

    async def warmup(self) -> None:
        """Load the model into memory so turn 1 isn't 10s slower than turn 2 (SC2).

        At login the Ollama unit may accept connections seconds after us, so
        retry patiently (and keep retrying slowly) instead of giving up for the
        whole session or burning the supervisor's 3 strikes at boot.
        """
        delay = 2.0
        warned = False
        while True:
            try:
                await self._client.generate(
                    model=self._model, prompt="", keep_alive=self._keep_alive
                )
                log.info("LLM %s warm", self._model)
                return
            except Exception as exc:  # noqa: BLE001
                if not warned:
                    log.warning("LLM warmup failed (%s); retrying in background", type(exc).__name__)
                    warned = True
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 30.0)

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[str | ToolCall]:
        stream = await self._client.chat(
            model=self._model,
            messages=messages,
            stream=True,
            tools=tools or [],
            keep_alive=self._keep_alive,
            options=self._options or None,
        )
        # aclosing: if our caller stops early (barge-in), the httpx stream must
        # be closed in THIS task — GC-finalizing it elsewhere corrupts the pool.
        async with aclosing(stream) as parts:
            async for part in parts:
                msg = part["message"]
                for tc in msg.get("tool_calls") or []:
                    fn = tc["function"]
                    args = {k: str(v) for k, v in (fn.get("arguments") or {}).items()}
                    yield ToolCall(name=fn["name"], args=args)
                if msg.get("content"):
                    yield msg["content"]


class Orchestrator:
    """Owns state + context. Other stages call into it; it publishes StateChanged."""

    def __init__(
        self,
        bus: Bus,
        llm: LlmClient,
        tools: ToolRunner | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        external_playback: bool = False,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._tools = tools
        # With external playback (M5+), SPEAKING must cover audio playout, not
        # just generation: the composition root calls finish_turn() after the
        # output buffer drains, and run_turn leaves the state at SPEAKING.
        self._external_playback = external_playback
        self._context_turns: int = config.get()["llm"]["context_turns"]
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._state = State.IDLE
        self._turn_id = 0
        self._turn_active = False
        self._cancelled = asyncio.Event()
        self._on_activate: list = []  # callbacks for AVATAR_ACTIVATE (wake stage hook)

    # -- state ----------------------------------------------------------- #

    @property
    def state(self) -> State:
        return self._state

    @property
    def is_voicing(self) -> bool:
        """True while audio for this turn should play: a reply, or a spoken error."""
        return self._state in (State.SPEAKING, State.ERROR)

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
        A wake while THINKING also cancels: the user has moved on.
        """
        if self._turn_active:
            self._cancelled.set()
        if self._state is not State.LISTENING:
            self._set_state(State.LISTENING)

    def end_listening(self) -> None:
        """Capture produced nothing usable (no speech / empty transcript): LISTENING -> IDLE.

        Without this, one false wake left the state stuck at LISTENING forever:
        the avatar kept showing 'listening' and click-to-activate (IDLE-only,
        DL-10) stopped working until the next real turn.
        """
        if self._state is State.LISTENING and not self._turn_active:
            self._set_state(State.IDLE)

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
        """Keep system prompt + the last N user turns (§12).

        Trims on user-message boundaries so an assistant tool_calls message is
        never separated from its tool results (orphaned tool messages confuse
        the model), however many tool rounds a turn used.
        """
        user_idx = [i for i, m in enumerate(self._messages) if m["role"] == "user"]
        if len(user_idx) > self._context_turns:
            cut = user_idx[-self._context_turns]
            self._messages = [self._messages[0]] + self._messages[cut:]

    async def _until_cancelled(self, aw: Awaitable[_T]) -> _T:
        """Await `aw`, but abort it the moment a barge-in arrives (P5).

        A screen description or slow command can take 10+ s; the user must be
        able to talk over it, not wait for it to finish.
        """
        task = asyncio.ensure_future(aw)
        waiter = asyncio.create_task(self._cancelled.wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            # WE are being cancelled (shutdown): the tool must die with us, so
            # e.g. the executor's kill-the-subprocess handler actually runs.
            waiter.cancel()
            task.cancel()
            with suppress(BaseException):
                await task
            raise
        waiter.cancel()
        if task.done():
            return task.result()
        task.cancel()  # barge-in: abort the tool
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # an outer cancellation arrived meanwhile — never swallow it
        except Exception:  # noqa: BLE001 — the aborted tool's own failure is moot
            pass
        raise TurnCancelled

    async def run_turn(self, final: FinalTranscript) -> AsyncIterator[Token]:
        """Drive one turn: THINKING -> stream tokens (SPEAKING at first token) -> IDLE.

        The caller (chunker/TTS at M5, console at M3) consumes the Token stream.
        A barge-in Cancel stops generation mid-stream; state then stays LISTENING
        (set by event_loop), not IDLE. An LLM failure yields a spoken apology
        while the state is ERROR.
        """
        self._turn_id = final.turn_id
        self._turn_active = True
        self._cancelled.clear()
        self._set_state(State.THINKING)
        self._messages.append({"role": "user", "content": final.text})
        self._trim_context()

        generated: list[str] = []
        failure: str | None = None
        tool_round_start: int | None = None  # set while a tool batch is in flight
        specs = self._tools.specs() if self._tools is not None else None
        try:
            for _round in range(_MAX_TOOL_ROUNDS):
                pending_calls: list[ToolCall] = []
                round_text: list[str] = []
                # aclosing: breaking out on barge-in must aclose() the stream
                # here, not leave it to GC in another task (it holds an httpx
                # connection; abandoning it poisons the shared client's pool).
                async with aclosing(
                    self._llm.chat(list(self._messages), tools=specs)
                ) as stream:
                    async for item in stream:
                        if self._cancelled.is_set():
                            log.info("turn %d cancelled (barge-in)", final.turn_id)
                            break
                        if isinstance(item, ToolCall):
                            pending_calls.append(item)
                            continue
                        if not generated:
                            self._set_state(State.SPEAKING)
                        generated.append(item)
                        round_text.append(item)
                        yield Token(text=item, turn_id=final.turn_id)
                if self._cancelled.is_set() or not pending_calls or self._tools is None:
                    break
                if _round == _MAX_TOOL_ROUNDS - 1:
                    break  # no generation round left to consume results — don't execute
                # The assistant's tool_calls message must precede the tool
                # results, or the model sees answers to questions it never asked.
                tool_round_start = len(self._messages)
                self._messages.append({
                    "role": "assistant",
                    "content": "".join(round_text),
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": dict(c.args)}}
                        for c in pending_calls
                    ],
                })
                # FR8: execute proposed tools, feed results back, same turn.
                for call in pending_calls:
                    if self._cancelled.is_set():  # barge-in mid-tool-batch (P5)
                        break
                    output = await self._until_cancelled(self._tools.run(call, final.turn_id))
                    self._messages.append(
                        {"role": "tool", "content": output, "tool_name": call.name}
                    )
                if not self._cancelled.is_set():
                    tool_round_start = None  # batch complete: keep it in history
        except TurnCancelled:
            log.info("turn %d cancelled during tool execution (barge-in)", final.turn_id)
        except Exception as exc:  # noqa: BLE001
            if _is_connection_error(exc):
                log.error("LLM unreachable (%s), turn=%d", type(exc).__name__, final.turn_id)
                failure = APOLOGY_UNREACHABLE
            else:
                log.exception("LLM stream failed, turn=%d", final.turn_id)
                failure = APOLOGY_GENERIC
        finally:
            self._turn_active = False
            if tool_round_start is not None:
                # Interrupted mid-batch: drop the dangling tool_calls message and
                # any partial results (trimming only cuts at user boundaries).
                del self._messages[tool_round_start:]

        if failure is not None:
            # §16: spoken + rendered. Drop the failed user message so a retry
            # doesn't see two copies of it.
            if self._messages[-1].get("role") == "user":
                self._messages.pop()
            self._set_state(State.ERROR)
            if self._external_playback:
                yield Token(text=failure, turn_id=final.turn_id)
                return  # ERROR persists until finish_turn() after the apology plays
            self._set_state(State.IDLE)
            return

        if generated:
            self._messages.append({"role": "assistant", "content": "".join(generated)})
            self._trim_context()
        if not self._cancelled.is_set():
            if self._external_playback and generated:
                return  # SPEAKING persists until finish_turn() after playout
            self._set_state(State.IDLE)

    def finish_turn(self) -> None:
        """Playback drained (external_playback mode): SPEAKING/THINKING/ERROR -> IDLE.

        A no-op if a barge-in already moved the state to LISTENING.
        """
        if not self._cancelled.is_set() and self._state in (
            State.SPEAKING, State.THINKING, State.ERROR
        ):
            self._set_state(State.IDLE)
