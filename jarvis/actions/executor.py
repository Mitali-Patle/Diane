"""Executor: the ONLY module that spawns command subprocesses (§9, P1).

Executes a ResolvedAction produced by the registry — never a raw ToolCall,
never a model-supplied string. Non-privileged (P4: runs as the session user,
argv list only, shell=False by construction), time-limited and cancellable
(P5), and every execution is appended to the audit log before *and* after
running (P7): a crash mid-command still leaves the attempt on record.
"""
from __future__ import annotations

import asyncio
import logging

from jarvis import config, logs
from jarvis.actions.registry import ResolvedAction
from jarvis.bus import AvatarCommand, ToolResult

log = logging.getLogger("jarvis.executor")

_OUTPUT_CAP = 4000  # chars of stdout fed back to the LLM (FR8) — keep turns light


class Executor:
    def __init__(self, avatar_out: asyncio.Queue[AvatarCommand] | None = None) -> None:
        self._timeout: float = config.get()["tools"]["timeout_s"]
        self._audit = logs.audit()
        self._avatar_out = avatar_out

    async def execute(self, action: ResolvedAction, turn_id: int) -> ToolResult:
        if action.avatar_command is not None:
            # Not a command subprocess: app log per NFR5, not the audit log.
            act, arg = action.avatar_command
            if self._avatar_out is not None:
                self._avatar_out.put_nowait(AvatarCommand(action=act, arg=arg))
            log.info("avatar command %s(%s) turn=%d", act, arg, turn_id)
            return ToolResult(name=action.tool, ok=True, output=f"avatar {act} -> {arg}",
                              turn_id=turn_id)

        if action.argv is None:  # security boundary: never trust a half-built action
            raise ValueError(f"ResolvedAction for {action.tool} has no argv")
        argv = list(action.argv)
        self._audit.info("EXEC tool=%s argv=%s turn=%d", action.tool, argv, turn_id)

        if action.tool == "open_app":
            # Detached GUI app: don't wait for it, don't tie it to our lifetime.
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            self._audit.info("DONE tool=%s pid=%d detached turn=%d", action.tool, proc.pid,
                             turn_id)
            return ToolResult(name=action.tool, ok=True, output=f"launched {argv[0]}",
                              turn_id=turn_id)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            self._audit.info("TIMEOUT tool=%s argv=%s turn=%d", action.tool, argv, turn_id)
            return ToolResult(name=action.tool, ok=False,
                              output=f"timed out after {self._timeout:.0f}s", turn_id=turn_id)
        except asyncio.CancelledError:
            proc.kill()          # P5: cancellation must not leak the subprocess
            await proc.wait()
            self._audit.info("CANCELLED tool=%s argv=%s turn=%d", action.tool, argv, turn_id)
            raise

        text = out.decode(errors="replace")[:_OUTPUT_CAP]
        self._audit.info("DONE tool=%s exit=%d turn=%d", action.tool, proc.returncode, turn_id)
        return ToolResult(name=action.tool, ok=proc.returncode == 0, output=text,
                          turn_id=turn_id)
