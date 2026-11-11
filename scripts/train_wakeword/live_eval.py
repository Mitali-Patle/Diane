"""Score wake models against LIVE room audio — the false-wake number that matters.

Records N seconds from the default mic into memory (never written to disk),
then reports how many times each model would have fired at each patience.
Run it with the room in its normal state (nobody saying "Hi Diane").

Run:  .venv/bin/python scripts/train_wakeword/live_eval.py [seconds] [model ...]
e.g.  .venv/bin/python scripts/train_wakeword/live_eval.py 120 hi_diane hi_diane_new
"""
import sys
from pathlib import Path

import sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.perception.wake_vad import make_wake_scorer  # noqa: E402

SR = 16000
THRESHOLD = 0.6


def main() -> None:
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    models = sys.argv[2:] or ["hi_diane"]
    print(f"recording {secs}s of room audio (memory only)...", flush=True)
    rec = sd.rec(secs * SR, samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    pcm = rec.ravel()
    for name in models:
        scorer = make_wake_scorer(name)
        scores = [scorer(pcm[i : i + 1280]) for i in range(0, len(pcm) - 1280, 1280)]
        for patience in (1, 2, 3):
            fires = streak = 0
            for s in scores:
                streak = streak + 1 if s >= THRESHOLD else 0
                fires += streak == patience
            print(f"{name:>16} patience={patience}: {fires} false wakes "
                  f"({fires * 3600 / secs:.0f}/hour)")


if __name__ == "__main__":
    main()
