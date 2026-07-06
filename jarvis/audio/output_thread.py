"""Audio-out OS thread: PortAudio callback pulls PCM from a shared buffer (§7 domain 3).

The callback only copies bytes out of a lock-guarded buffer (P6 mirror-image:
pull-only, no processing). flush() empties the buffer in O(1) — that plus the
small blocksize is what makes barge-in stop audible output in <100 ms (SC3).
"""
from __future__ import annotations

import asyncio
import threading

import sounddevice as sd

from jarvis import config
from jarvis.bus import PcmChunk

_BLOCK = 512  # frames per callback: 23-32 ms granularity for barge-in stop (SC3)


class AudioOutput:
    def __init__(self) -> None:
        self._device = config.get()["audio"]["output_device"]
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream: sd.RawOutputStream | None = None
        self._sample_rate: int | None = None

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        # Pull-only (P6): copy from the buffer, zero-fill when empty.
        n = len(outdata)
        with self._lock:
            take = self._buf[:n]
            del self._buf[:n]
        outdata[: len(take)] = take
        if len(take) < n:
            outdata[len(take):] = bytes(n - len(take))

    def _ensure_stream(self, sample_rate: int) -> None:
        if self._stream is not None and self._sample_rate == sample_rate:
            return
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._sample_rate = sample_rate
        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            blocksize=_BLOCK,
            device=self._device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def put(self, chunk: PcmChunk) -> None:
        self._ensure_stream(chunk.sample_rate)
        with self._lock:
            self._buf.extend(chunk.pcm)

    def flush(self) -> None:
        """Barge-in: drop everything queued, output falls silent within one block."""
        with self._lock:
            self._buf.clear()

    def pending_bytes(self) -> int:
        with self._lock:
            return len(self._buf)

    async def drain(self) -> None:
        """Await until queued audio has been played out (cancellable, P5)."""
        while self.pending_bytes() > 0 and self._stream is not None and self._stream.active:
            await asyncio.sleep(0.05)

    async def wait_below(self, seconds: float) -> None:
        """Backpressure: block the producer while >seconds of audio is queued (SC6)."""
        if self._sample_rate is None:
            return
        cap = int(seconds * self._sample_rate) * 2
        while self.pending_bytes() > cap and self._stream is not None and self._stream.active:
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self.flush()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
