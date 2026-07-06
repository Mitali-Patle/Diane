"""Composition root: builds the shared event loop, threads, tasks, and supervisor (§7).

M0 skeleton — stages are wired in as milestones land (M1 audio/wake, M2 STT,
M3 orchestrator, M5 TTS/playback, M8 avatar via qasync).
"""
from __future__ import annotations

import asyncio

from jarvis import config, logs
from jarvis.bus import Bus


async def run() -> None:
    cfg = config.get()
    log = logs.setup()
    _bus = Bus()  # stage tasks subscribe here as milestones land
    log.info("Diane starting (wake model=%s, llm=%s)", cfg["wake"]["model"], cfg["llm"]["model"])
    # Stage tasks are added here milestone by milestone.
    try:
        await asyncio.Event().wait()  # placeholder foreground wait; cancellable (P5)
    except asyncio.CancelledError:
        log.info("Diane shutting down")
        raise


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
