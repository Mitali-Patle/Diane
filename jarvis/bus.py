"""Interrupt bus, shared event/queue payloads, and the assistant state enum (§7, §10).

All cross-package communication flows through the dataclasses and Bus defined here.
Payloads are immutable (frozen=True, slots=True) so no consumer can mutate shared state.
"""
from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from typing import Literal


class State(enum.Enum):
    """Assistant state machine — owned solely by the orchestrator (§7)."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Queue payloads (§10)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class AudioFrame:
    """16 kHz mono int16 PCM block from the input thread."""

    pcm: bytes
    timestamp: float


@dataclass(frozen=True, slots=True)
class PartialTranscript:
    text: str
    turn_id: int


@dataclass(frozen=True, slots=True)
class FinalTranscript:
    text: str
    turn_id: int


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    turn_id: int


@dataclass(frozen=True, slots=True)
class Phrase:
    """Sentence-level chunk emitted by the chunker for TTS (FR6)."""

    text: str
    turn_id: int
    index: int


@dataclass(frozen=True, slots=True)
class PcmChunk:
    """Synthesized audio ready for the output thread."""

    pcm: bytes
    sample_rate: int
    turn_id: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, str] = field(default_factory=dict)
    turn_id: int = 0


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    output: str
    turn_id: int = 0


@dataclass(frozen=True, slots=True)
class ScreenQuery:
    prompt: str
    turn_id: int


@dataclass(frozen=True, slots=True)
class ScreenDescription:
    text: str
    turn_id: int


@dataclass(frozen=True, slots=True)
class AvatarCommand:
    """Orchestrator/tool-layer → avatar (pack switches, menu-equivalent voice commands)."""

    action: Literal["set_pack", "toggle_eyes", "hide", "show"]
    arg: str | None = None


@dataclass(frozen=True, slots=True)
class AvatarStatus:
    """Avatar → orchestrator, informational (lets the assistant speak pack-load failures)."""

    pack: str
    visible: bool
    error: str | None = None


# --------------------------------------------------------------------------- #
# Bus events (§10)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Cancel:
    """Barge-in / interrupt: flush TTS + playback immediately."""

    turn_id: int


@dataclass(frozen=True, slots=True)
class StateChanged:
    new_state: State


@dataclass(frozen=True, slots=True)
class AvatarActivate:
    """Click-to-listen intent; consumed only by the orchestrator, valid only in IDLE (DL-10)."""


Event = Cancel | StateChanged | AvatarActivate


class Bus:
    """In-process pub/sub over asyncio queues (§7: single process, no IPC).

    Regular subscribers get an unbounded queue. The avatar subscribes with
    latest_wins=True: a depth-1 mailbox where a new event replaces any unread
    one — animation must never queue up behind reality (§10 backpressure).
    """

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[Event]] = []
        self._latest_wins: set[int] = set()

    def subscribe(self, *, latest_wins: bool = False) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1 if latest_wins else 0)
        self._subs.append(q)
        if latest_wins:
            self._latest_wins.add(id(q))
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass
        self._latest_wins.discard(id(q))

    def publish(self, event: Event) -> None:
        for q in self._subs:
            if id(q) in self._latest_wins:
                # Drop the stale event, deliver the newest (latest-wins mailbox).
                while q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                q.put_nowait(event)
            else:
                q.put_nowait(event)
