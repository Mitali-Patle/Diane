"""Record real-mic wake-word samples for domain adaptation (DL-12 validation set).

Usage:
  .venv/bin/python scripts/train_wakeword/record_real.py positive 60
      -> say "Hi Diane" once every few seconds; VAD slices each utterance
         into models/wakeword_data/real_positive/
  .venv/bin/python scripts/train_wakeword/record_real.py negative 45
      -> speak normal sentences WITHOUT the wake word (plus some silence);
         sliced into models/wakeword_data/real_negative/

  .venv/bin/python scripts/train_wakeword/record_real.py ambient 300
      -> just leave the room as it normally is (fan, typing, TV, music);
         saved UNSLICED as 2 s chunks into models/wakeword_data/real_negative/.
         This is the class the first models never saw: live room tone through
         the AEC source. Without it the model fires on near-silence.

Audio stays local (NFR1); clips are gitignored with the rest of models/.
"""
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.perception.wake_vad import SileroVad  # noqa: E402

SR = 16000


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "positive"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    ambient = kind == "ambient"
    if ambient:
        kind = "negative"
    out = ROOT / "models/wakeword_data" / f"real_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    start_idx = len(list(out.glob("*.wav")))

    print(f"Recording {seconds}s for {kind} samples — speak now...", flush=True)
    rec = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    pcm = rec.ravel()

    if ambient:  # keep everything: room tone is exactly what we need
        n = 0
        for a in range(0, len(pcm) - 2 * SR + 1, 2 * SR):
            with wave.open(str(out / f"ambient_{start_idx + n:04d}.wav"), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(SR)
                f.writeframes(pcm[a : a + 2 * SR].tobytes())
            n += 1
        print(f"saved {n} ambient chunks to {out}")
        return

    # VAD-slice into utterances with 0.3s pre/post pad
    vad = SileroVad(str(ROOT / "models/silero_vad.onnx"))
    probs = np.array([vad(pcm[i : i + 512]) for i in range(0, len(pcm) - 512, 512)])
    speech = probs > 0.5
    clips, i = [], 0
    while i < len(speech):
        if speech[i]:
            j = i
            silence = 0
            while j < len(speech) and silence < 16:  # ~0.5s of trailing silence
                silence = silence + 1 if not speech[j] else 0
                j += 1
            a = max(0, (i - 10) * 512)
            b = min(len(pcm), (j + 10) * 512)
            if b - a >= SR // 2:  # ignore blips under 0.5s
                clips.append(pcm[a:b])
            i = j
        else:
            i += 1

    for k, clip in enumerate(clips):
        with wave.open(str(out / f"real_{start_idx + k:03d}.wav"), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(SR)
            f.writeframes(clip.tobytes())
        print(f"  clip {start_idx + k}: {len(clip)/SR:.2f}s")
    print(f"saved {len(clips)} clips to {out}")


if __name__ == "__main__":
    main()
