"""Audio-in OS thread: PortAudio callback → thread-safe janus queue (§7 domain 1).

The PortAudio callback does a queue-put ONLY (P6): one bytes copy, one
put_nowait. No logging, no processing, no allocation-heavy work. If the
asyncio side stalls, frames are dropped (counted, reported outside the
callback) rather than letting the queue grow without bound (SC6).
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

import janus
import sounddevice as sd

from jarvis import config
from jarvis.bus import AudioFrame

QUEUE_MAX_FRAMES = 256  # ~8 s of audio at 512 samples/frame; beyond that we drop


class AudioInput:
    """Owns the input stream and exposes frames as an async iterator."""

    def __init__(self) -> None:
        cfg = config.get()["audio"]
        self._sample_rate: int = cfg["sample_rate"]
        self._blocksize: int = cfg["blocksize"]
        self._device = cfg["input_device"]
        self._queue: janus.Queue[AudioFrame] | None = None
        self._stream: sd.RawInputStream | None = None
        self.dropped_frames = 0

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        # P6: queue-put only. Runs on the PortAudio thread.
        try:
            self._queue.sync_q.put_nowait(AudioFrame(bytes(indata), time.monotonic()))
        except janus.SyncQueueFull:
            self.dropped_frames += 1

    def start(self) -> None:
        """Create the queue and open the stream. Must run inside the event loop."""
        self._queue = janus.Queue(maxsize=QUEUE_MAX_FRAMES)
        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._blocksize,
            device=self._device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._queue is not None:
            self._queue.close()

    async def frames(self) -> AsyncIterator[AudioFrame]:
        """Yield frames until stop(); cancellable at every await (P5)."""
        assert self._queue is not None, "start() first"
        while True:
            try:
                yield await self._queue.async_q.get()
            except janus.AsyncQueueShutDown:
                return
