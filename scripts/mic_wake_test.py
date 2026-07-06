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
from jarvis.perception.wake_vad import OpenWakeWord, SileroVad, WakeVadStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    audio = AudioInput()
    audio.start()
    wake = OpenWakeWord("hey_jarvis")
    vad = SileroVad("models/silero_vad.onnx")
    stage = WakeVadStage(Bus(), wake, vad)
    print("Listening... say 'hey jarvis' then a sentence. Ctrl-C to quit.")
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
