"""On-demand screen understanding: mss screenshot → VLM → description (FR5).

P8 is absolute: capture happens only inside describe(), which is only reached
via an explicit describe_screen tool call in a user turn. There are no timers,
no loops, no caching of frames. Screen content never touches any log (NFR1);
the description text is a tool result fed back into the turn (FR8) and lives
only in the rolling context.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Protocol

from jarvis import config

log = logging.getLogger("jarvis.vision")

_MAX_SIDE = 1024  # moondream ingests small images; downscale keeps CPU latency sane (SC7)

DEFAULT_PROMPT = (
    "Describe what is on this computer screen: the application in focus, "
    "what the user appears to be doing, and any prominent text or dialogs."
)


class Vlm(Protocol):
    """Injectable VLM slice for tests."""

    async def describe_image(self, png: bytes, prompt: str) -> str: ...


class OllamaVlm:
    """moondream via the sanctioned localhost Ollama endpoint (§11)."""

    def __init__(self) -> None:
        import ollama

        cfg = config.get()
        self._client = ollama.AsyncClient(host=cfg["llm"]["host"])
        self._model = cfg["vlm"]["model"]

    async def describe_image(self, png: bytes, prompt: str) -> str:
        resp = await self._client.generate(
            model=self._model, prompt=prompt, images=[png]
        )
        return resp["response"].strip()


def _grab_png() -> bytes:
    """Capture the primary monitor and encode a downscaled PNG. Runs in a thread."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])  # 1 = primary (0 = all combined)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    img.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Vision:
    def __init__(self, vlm: Vlm | None = None) -> None:
        self._vlm = vlm if vlm is not None else OllamaVlm()

    async def describe(self, prompt: str = DEFAULT_PROMPT) -> str:
        """One capture, one VLM call, nothing persisted (P8)."""
        loop = asyncio.get_running_loop()
        png = await loop.run_in_executor(None, _grab_png)
        log.info("screen captured on demand (%d KB png)", len(png) // 1024)
        return await self._vlm.describe_image(png, prompt)
