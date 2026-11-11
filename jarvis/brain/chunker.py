"""Sentence-level chunking of the token stream for TTS (FR6, §2.1).

Playback begins on the first complete sentence, so the boundary heuristic
favors emitting early over grammatical perfection. Abbreviation false-splits
(e.g. "Dr.") are an accepted v1 tradeoff — a slightly odd TTS pause, nothing
more.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jarvis.bus import Phrase, Token

_BOUNDARIES = (".", "!", "?", "\n", "؟", "।")
_MIN_CHARS = 12   # don't ship micro-fragments like "Ok." alone if more is coming fast
_MAX_CHARS = 350  # force a break so one run-on sentence can't stall playback
# First phrase only: also break at a clause boundary once this long. Speech
# starts on the first clause instead of waiting for a long first sentence (SC2).
_FIRST_CLAUSE_CHARS = 28
_CLAUSE = (",", ";", ":", " -", "—")


def _boundary_at_end(text: str) -> bool:
    stripped = text.rstrip("\"')] *_")
    return bool(stripped) and stripped.endswith(_BOUNDARIES)


def _clause_at_end(text: str) -> bool:
    return text.rstrip().endswith(_CLAUSE)


@asynccontextmanager
async def _closing_upstream(tokens: AsyncIterator[Token]):
    """Closing the chunker must close the token stream under it NOW (the LLM
    turn, which holds an HTTP stream and turn accounting) — otherwise it is left
    for the garbage collector to finalize later, possibly during the next turn."""
    try:
        yield tokens
    finally:
        aclose = getattr(tokens, "aclose", None)
        if aclose is not None:
            await aclose()


async def phrases(tokens: AsyncIterator[Token]) -> AsyncIterator[Phrase]:
    """Group a Token stream into sentence-level Phrases; flush the tail at end."""
    async with _closing_upstream(tokens):
        async for phrase in _phrases(tokens):
            yield phrase


async def _phrases(tokens: AsyncIterator[Token]) -> AsyncIterator[Phrase]:
    buf = ""
    index = 0
    turn_id = 0
    async for tok in tokens:
        turn_id = tok.turn_id
        buf += tok.text
        early = index == 0 and len(buf) >= _FIRST_CLAUSE_CHARS and _clause_at_end(buf)
        if (len(buf) >= _MIN_CHARS and _boundary_at_end(buf)) or early:
            text = buf.strip()
            buf = ""
            if text:
                yield Phrase(text=text, turn_id=turn_id, index=index)
                index += 1
        while len(buf) >= _MAX_CHARS:
            # Run-on with no boundary: force a break at the last space in budget.
            cut = buf.rfind(" ", 0, _MAX_CHARS)
            cut = cut if cut > 0 else _MAX_CHARS
            head, buf = buf[:cut].strip(), buf[cut:].lstrip()
            if head:
                yield Phrase(text=head, turn_id=turn_id, index=index)
                index += 1
    tail = buf.strip()
    if tail:
        yield Phrase(text=tail, turn_id=turn_id, index=index)
