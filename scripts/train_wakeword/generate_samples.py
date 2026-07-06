"""Generate synthetic training samples for the "Hi Diane" wake word (M7, DL-12).

Positives: many LibriTTS speakers saying wake-phrase variants at varied speeds,
with noise/gain augmentation. Negatives: phonetically-adjacent phrases (the
hard cases), generic speech, and noise — same speakers, same augmentation, so
the classifier can't cheat on channel differences.

Run:  .venv/bin/python scripts/train_wakeword/generate_samples.py
Writes 16 kHz mono wavs under models/wakeword_data/{positive,negative}/.
"""
import random
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

VOICE = ROOT / "models/piper/en_US-libritts_r-medium.onnx"
OUT = ROOT / "models/wakeword_data"
SR = 16000

POSITIVE_TEXTS = ["Hi Diane.", "Hi, Diane!", "hi diane", "Hi Diane?"]
NEGATIVE_TEXTS = [
    # phonetically adjacent — the false-accept traps
    "Hi Dana.", "Hey Diane called earlier.", "Hi there.", "My dear Anne.",
    "High dive.", "Hi Ryan.", "Diane is here.", "Hi Dan.", "Bye Diane.",
    "I'm dying to know.", "Hindi and English.",
    # generic speech
    "What's the weather like today?", "Please open the browser for me.",
    "The meeting starts at five.", "I'll call you back in ten minutes.",
    "Turn the volume down a little.", "This recipe needs more salt.",
]
N_SPEAKERS = 120          # LibriTTS speaker ids sampled
LENGTH_SCALES = (0.85, 1.0, 1.2)   # speech-rate variation


def _resample_to_16k(pcm: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR:
        return pcm
    x = np.arange(len(pcm))
    xi = np.linspace(0, len(pcm) - 1, int(len(pcm) * SR / sr))
    return np.interp(xi, x, pcm.astype(np.float32)).astype(np.float32)


def _augment(pcm: np.ndarray, rng: random.Random) -> np.ndarray:
    gain = rng.uniform(0.4, 1.0)
    out = pcm * gain
    if rng.random() < 0.5:  # additive noise at random SNR
        snr_db = rng.uniform(5, 25)
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 1, len(out))
        sig_p, noise_p = np.mean(out**2) + 1e-9, np.mean(noise**2)
        noise *= np.sqrt(sig_p / (noise_p * 10 ** (snr_db / 10)))
        out = out + noise.astype(np.float32)
    pad_l = int(rng.uniform(0.1, 0.4) * SR)   # random placement in the window
    pad_r = int(rng.uniform(0.1, 0.4) * SR)
    return np.concatenate([np.zeros(pad_l, np.float32), out, np.zeros(pad_r, np.float32)])


def _write(path: Path, pcm: np.ndarray) -> None:
    pcm16 = np.clip(pcm * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm16.tobytes())


def main() -> None:
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(str(VOICE))
    n_speakers = voice.config.num_speakers
    rng = random.Random(42)
    speakers = rng.sample(range(n_speakers), min(N_SPEAKERS, n_speakers))

    for kind, texts, per_speaker in (
        ("positive", POSITIVE_TEXTS, 3),
        ("negative", NEGATIVE_TEXTS, 1),
    ):
        out_dir = OUT / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for spk in speakers:
            chosen = rng.sample(texts, min(per_speaker, len(texts)))
            for text in chosen:
                cfg = SynthesisConfig(speaker_id=spk, length_scale=rng.choice(LENGTH_SCALES))
                chunks = list(voice.synthesize(text, syn_config=cfg))
                pcm = np.concatenate([c.audio_float_array for c in chunks])
                pcm = _resample_to_16k(pcm, chunks[0].sample_rate)
                _write(out_dir / f"{kind}_{count:05d}.wav", _augment(pcm, rng))
                count += 1
        print(f"{kind}: {count} samples")

    # pure noise/silence negatives — the always-on baseline
    noise_dir = OUT / "negative"
    rng_np = np.random.default_rng(7)
    for i in range(150):
        kind_roll = rng_np.random()
        n = int(rng_np.uniform(1.0, 2.5) * SR)
        if kind_roll < 0.4:
            pcm = np.zeros(n, np.float32)
        else:
            pcm = rng_np.normal(0, rng_np.uniform(0.005, 0.08), n).astype(np.float32)
        _write(noise_dir / f"noise_{i:05d}.wav", pcm)
    print("noise: 150 samples")


if __name__ == "__main__":
    main()
