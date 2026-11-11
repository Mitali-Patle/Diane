"""Wake chime: valid PCM at the requested rate, short, and not clipping."""
import numpy as np

from jarvis.voice.earcon import wake_chime


def test_wake_chime_is_short_quiet_pcm():
    c = wake_chime(22050)
    pcm = np.frombuffer(c.pcm, dtype=np.int16)
    assert c.sample_rate == 22050
    assert 0.1 < len(pcm) / 22050 < 0.3
    assert np.abs(pcm).max() < 0.3 * 32767
