"""actions package: tool registry (validation) + executor (the only subprocess spawner)."""
from __future__ import annotations

import asyncio

from jarvis.actions import registry
from jarvis.actions.executor import Executor
from jarvis.bus import AvatarCommand, ToolCall


class Actions:
    """ToolRunner facade for the orchestrator: resolve (P1/P2) then execute.

    A rejected call never executes anything; its spoken refusal is returned as
    the tool output so the model relays it to the user (§13).
    """

    def __init__(
        self,
        avatar_out: asyncio.Queue[AvatarCommand] | None = None,
        installed_packs: tuple[str, ...] = (),
    ) -> None:
        self._executor = Executor(avatar_out)
        self.installed_packs = installed_packs

    def specs(self) -> list[dict]:
        return registry.tool_specs()

    async def run(self, call: ToolCall, turn_id: int) -> str:
        try:
            action = registry.resolve(call, self.installed_packs)
        except registry.ToolRejected as rej:
            return f"REJECTED: {rej.spoken}"
        result = await self._executor.execute(action, turn_id)
        status = "ok" if result.ok else "failed"
        return f"{result.name} {status}:\n{result.output}"
