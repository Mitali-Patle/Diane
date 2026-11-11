"""Manual M2 smoke test: live mic → wake → utterance → streaming transcription.

Run:  .venv/bin/python scripts/mic_stt_test.py
Say "hey jarvis", speak, pause; partial and final transcripts print live.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.input_thread import AudioInput
from jarvis.bus import Bus, FinalTranscript
from jarvis.perception.stt import SpeechToText
from jarvis.perception.wake_vad import OpenWakeWord, SileroVad, WakeVadStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    print("Loading whisper-small (first run downloads ~500 MB)...")
    stt = SpeechToText()
    audio = AudioInput()
    audio.start()
    stage = WakeVadStage(Bus(), OpenWakeWord("hey_jarvis"), SileroVad("models/silero_vad.onnx"))
    print("Listening... say 'hey jarvis' then a sentence.")
    turn = 0
    try:
        async for utt in stage.utterances(audio.frames()):
            turn += 1
            t0 = time.monotonic()
            async for ev in stt.transcribe(utt, turn_id=turn):
                kind = "FINAL " if isinstance(ev, FinalTranscript) else "partial"
                print(f"  [{kind} +{time.monotonic()-t0:.2f}s] {ev.text}")
    finally:
        audio.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
