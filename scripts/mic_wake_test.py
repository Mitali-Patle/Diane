"""Manual M1 smoke test: live mic → wake word → utterance segmentation.

Run:  .venv/bin/python scripts/mic_wake_test.py
Say "hey jarvis", then speak a sentence, then pause. Ctrl-C to quit.
Prints wake detections and captured utterance durations; measures SC1 timing.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.input_thread import AudioInput
from jarvis.bus import Bus
from jarvis.perception.wake_vad import SileroVad, WakeVadStage, make_wake_scorer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "hey_jarvis"
    audio = AudioInput()
    audio.start()
    wake = make_wake_scorer(model)  # pass 'hi_diane' to test the custom model
    vad = SileroVad("models/silero_vad.onnx")
    stage = WakeVadStage(Bus(), wake, vad)
    print(f"Listening for '{model}'... speak the wake word then a sentence. Ctrl-C quits.")
    try:
        async for utt in stage.utterances(audio.frames()):
            dur = len(utt.pcm) / 2 / 16000
            latency = time.monotonic() - utt.ended_at
            print(f"UTTERANCE: {dur:.2f}s of audio, yielded {latency*1000:.0f} ms after end")
            if audio.dropped_frames:
                print(f"  (dropped frames: {audio.dropped_frames})")
    finally:
        audio.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
