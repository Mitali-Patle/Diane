"""Earcons: short synthesized cues, no assets, no model (SC1 perceived latency).

A soft two-note chime on wake tells the user "go ahead" the instant capture
starts — without it, people say "Hi Diane", wait for a reaction, and the
capture times out (thousands of "capture abandoned" entries in the app log).
"""
from __future__ import annotations

import numpy as np

from jarvis.bus import PcmChunk


def _tone(freq: float, seconds: float, sr: int, volume: float) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    env = np.minimum(1.0, t / 0.008) * np.exp(-t * 18.0)  # 8 ms attack, soft decay
    return volume * env * np.sin(2 * np.pi * freq * t)


def wake_chime(sample_rate: int, volume: float = 0.18) -> PcmChunk:
    """~180 ms rising chime (E6 -> A6), quiet enough not to read as speech to VAD."""
    a = _tone(1318.5, 0.09, sample_rate, volume)
    b = _tone(1760.0, 0.09, sample_rate, volume)
    pcm = np.clip(np.concatenate([a, b]) * 32767, -32768, 32767).astype(np.int16)
    return PcmChunk(pcm=pcm.tobytes(), sample_rate=sample_rate, turn_id=-1)
