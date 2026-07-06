"""Chunker tests: sentence boundaries, flush, run-on cap (FR6)."""
from jarvis.brain.chunker import phrases
from jarvis.bus import Token


async def _tokens(texts, turn_id=1):
    for t in texts:
        yield Token(text=t, turn_id=turn_id)


async def _collect(texts):
    return [p async for p in phrases(_tokens(texts))]


async def test_emits_phrase_at_sentence_boundary():
    out = await _collect(["Hello", " there", ".", " How", " are", " you", "?"])
    assert [p.text for p in out] == ["Hello there.", "How are you?"]
    assert [p.index for p in out] == [0, 1]


async def test_flushes_tail_without_terminator():
    out = await _collect(["Sure, opening", " Firefox now"])
    assert [p.text for p in out] == ["Sure, opening Firefox now"]


async def test_short_fragment_waits_for_more():
    out = await _collect(["Ok.", " Done with that task."])
    # "Ok." is under the minimum; it rides along with the next sentence.
    assert out[0].text.startswith("Ok.")
    assert len(out) == 1


async def test_runon_sentence_is_force_broken():
    out = await _collect(["word " * 100])  # 500 chars, no boundary
    assert len(out) >= 1
    assert all(len(p.text) <= 360 for p in out)


async def test_empty_stream_yields_nothing():
    assert await _collect([]) == []
