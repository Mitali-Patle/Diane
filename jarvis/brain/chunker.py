"""Sentence-level chunking of the token stream for TTS (FR6, §2.1).

Playback begins on the first complete sentence, so the boundary heuristic
favors emitting early over grammatical perfection. Abbreviation false-splits
(e.g. "Dr.") are an accepted v1 tradeoff — a slightly odd TTS pause, nothing
more.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.bus import Phrase, Token

_BOUNDARIES = (".", "!", "?", "\n", "؟", "।")
_MIN_CHARS = 12   # don't ship micro-fragments like "Ok." alone if more is coming fast
_MAX_CHARS = 350  # force a break so one run-on sentence can't stall playback


def _boundary_at_end(text: str) -> bool:
    stripped = text.rstrip("\"')] *_")
    return bool(stripped) and stripped.endswith(_BOUNDARIES)


async def phrases(tokens: AsyncIterator[Token]) -> AsyncIterator[Phrase]:
    """Group a Token stream into sentence-level Phrases; flush the tail at end."""
    buf = ""
    index = 0
    turn_id = 0
    async for tok in tokens:
        turn_id = tok.turn_id
        buf += tok.text
        if len(buf) >= _MIN_CHARS and _boundary_at_end(buf):
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
