"""Executor tests: subprocess execution, audit trail (P7), timeout, avatar path (NFR5)."""
import asyncio

from jarvis import config, logs
from jarvis.actions import Actions
from jarvis.actions.executor import Executor
from jarvis.actions.registry import ResolvedAction
from jarvis.bus import AvatarCommand, ToolCall


def _audit_lines() -> list[str]:
    path = config.ROOT / config.get()["logging"]["audit_log"]
    return path.read_text().splitlines() if path.exists() else []


async def test_run_command_executes_and_audits():
    before = len(_audit_lines())
    ex = Executor()
    result = await ex.execute(ResolvedAction(tool="run_command", argv=("date",)), turn_id=42)
    assert result.ok
    assert result.output.strip()
    lines = _audit_lines()
    assert len(lines) >= before + 2  # EXEC + DONE
    assert "EXEC tool=run_command" in lines[-2]
    assert "turn=42" in lines[-2]


async def test_timeout_kills_and_audits(monkeypatch):
    ex = Executor()
    monkeypatch.setattr(ex, "_timeout", 0.1)
    result = await ex.execute(ResolvedAction(tool="run_command", argv=("sleep", "5")), turn_id=1)
    assert not result.ok
    assert "timed out" in result.output
    assert "TIMEOUT" in _audit_lines()[-1]


async def test_cancellation_kills_subprocess():
    ex = Executor()
    task = asyncio.create_task(
        ex.execute(ResolvedAction(tool="run_command", argv=("sleep", "5")), turn_id=1)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert "CANCELLED" in _audit_lines()[-1]


async def test_avatar_command_goes_to_queue_not_subprocess_not_audit():
    before = len(_audit_lines())
    q: asyncio.Queue[AvatarCommand] = asyncio.Queue()
    ex = Executor(avatar_out=q)
    result = await ex.execute(
        ResolvedAction(tool="set_avatar", avatar_command=("set_pack", "cat")), turn_id=1
    )
    assert result.ok
    assert q.get_nowait() == AvatarCommand(action="set_pack", arg="cat")
    assert len(_audit_lines()) == before  # NFR5: app log, never the audit log


async def test_actions_facade_rejection_never_executes():
    before = len(_audit_lines())
    actions = Actions()
    out = await actions.run(ToolCall(name="run_command", args={"name": "rm -rf /"}), turn_id=9)
    assert out.startswith("REJECTED:")
    assert len(_audit_lines()) == before  # nothing reached the executor


async def test_audit_log_is_append_only_handler():
    audit_logger = logs.audit()
    for h in audit_logger.handlers:
        assert getattr(h, "mode", "a") == "a"  # P7
