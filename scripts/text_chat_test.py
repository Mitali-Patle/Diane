"""Manual M3 smoke test: typed text in → streamed LLM response + state events out.

Run:  .venv/bin/python scripts/text_chat_test.py
Requires Ollama serving on localhost:11434 with the configured model pulled.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.brain.chunker import phrases
from jarvis.brain.orchestrator import Orchestrator, OllamaClient
from jarvis.bus import Bus, FinalTranscript, StateChanged


async def main() -> None:
    bus = Bus()
    events = bus.subscribe()
    orch = Orchestrator(bus, OllamaClient())
    loop_task = asyncio.create_task(orch.event_loop())

    async def print_states():
        while True:
            ev = await events.get()
            if isinstance(ev, StateChanged):
                print(f"  [state -> {ev.new_state.value}]")

    state_task = asyncio.create_task(print_states())
    turn = 0
    print("Type a message (Ctrl-D to quit):")
    try:
        while True:
            text = await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
            if not text:
                break
            turn += 1
            t0 = time.monotonic()
            first = None
            print("Diane: ", end="", flush=True)
            async for phrase in phrases(orch.run_turn(FinalTranscript(text.strip(), turn))):
                if first is None:
                    first = time.monotonic() - t0
                print(phrase.text, end=" ", flush=True)
            print(f"\n  (first phrase after {first:.2f}s)" if first else "\n  (no output)")
    finally:
        loop_task.cancel()
        state_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
