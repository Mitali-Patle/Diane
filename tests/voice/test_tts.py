"""TextToSpeech streaming contract with an injected fake synthesizer (§19)."""
from jarvis.bus import PcmChunk, Phrase
from jarvis.voice.tts import TextToSpeech


class FakeChunk:
    def __init__(self, data: bytes, sr: int = 16000):
        self.audio_int16_bytes = data
        self.sample_rate = sr


class FakeVoice:
    def __init__(self, n_chunks: int = 3):
        self.n = n_chunks
        self.texts: list[str] = []

    def synthesize(self, text: str):
        self.texts.append(text)
        return (FakeChunk(bytes([i]) * 320) for i in range(self.n))


async def test_synthesize_streams_pcm_chunks():
    voice = FakeVoice(n_chunks=2)
    tts = TextToSpeech(voice=voice)
    chunks = [c async for c in tts.synthesize(Phrase(text="Hello.", turn_id=5, index=0))]
    assert len(chunks) == 2
    assert all(isinstance(c, PcmChunk) for c in chunks)
    assert all(c.turn_id == 5 for c in chunks)
    assert all(c.sample_rate == 16000 for c in chunks)
    assert voice.texts == ["Hello."]


async def test_empty_synthesis_yields_nothing():
    tts = TextToSpeech(voice=FakeVoice(n_chunks=0))
    chunks = [c async for c in tts.synthesize(Phrase(text="x", turn_id=1, index=0))]
    assert chunks == []
