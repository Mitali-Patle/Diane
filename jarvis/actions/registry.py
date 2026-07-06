"""Tool registry: the ONE reviewable list of everything the assistant can do (P1, DL-5).

The LLM proposes a ToolCall; the registry validates it against whitelists
(P2 — whitelist by construction, unknown anything is rejected) and produces a
fully resolved, non-ambiguous spec for the executor. The model never supplies
a string that reaches a shell: run_command maps a whitelisted *name* to a
fixed argv; open_app maps an app name to a fixed argv; list_dir resolves and
containment-checks the path. Destructive tools (none in v1's builtin set)
carry destructive=True and require spoken confirmation of the resolved argv
before execution (P3).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from jarvis import config
from jarvis.bus import ToolCall


class ToolRejected(Exception):
    """Validation failed; .spoken is what Diane says instead of executing."""

    def __init__(self, spoken: str) -> None:
        super().__init__(spoken)
        self.spoken = spoken


@dataclass(frozen=True, slots=True)
class ResolvedAction:
    """A validated, fully resolved action ready for the executor / avatar layer."""

    tool: str
    argv: tuple[str, ...] | None = None      # subprocess actions
    avatar_command: tuple[str, str] | None = None  # (action, arg) bus publications
    screen_query: str | None = None          # on-demand vision (P8), no subprocess
    destructive: bool = False


# Fixed argv per whitelisted command name: the model picks a NAME, never args (P2).
_COMMAND_ARGV: dict[str, tuple[str, ...]] = {
    "ls": ("ls", "-la"),
    "df": ("df", "-h"),
    "free": ("free", "-h"),
    "date": ("date",),
    "uptime": ("uptime",),
    "whoami": ("whoami",),
}


def tool_specs() -> list[dict]:
    """Tool schema injected into the LLM prompt (Ollama tools format)."""
    cfg = config.get()["tools"]
    return [
        {
            "type": "function",
            "function": {
                "name": "open_app",
                "description": "Open a desktop application.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": list(cfg["app_whitelist"]),
                        }
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List the contents of a directory under the home folder.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_disk",
                "description": "Report free disk space.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run one of the whitelisted read-only system commands.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": sorted(set(cfg["command_whitelist"]) & set(_COMMAND_ARGV)),
                        }
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "describe_screen",
                "description": "Take one screenshot and describe what is currently on "
                "the user's screen. Use when asked what's on the screen.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Optional specific question about the screen.",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_avatar",
                "description": "Switch the desktop avatar to an installed pack.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    ]


def _resolve_dir(raw: str) -> Path:
    """Resolve a directory request, containing it under the configured roots (P2)."""
    cfg = config.get()["tools"]
    roots = [Path(r).expanduser().resolve() for r in cfg["dir_roots"]]
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve()
    for root in roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    raise ToolRejected("I can only browse folders inside your home directory.")


def resolve(call: ToolCall, installed_packs: tuple[str, ...] = ()) -> ResolvedAction:
    """Validate a proposed ToolCall against the whitelists; raise ToolRejected otherwise."""
    cfg = config.get()["tools"]

    if call.name == "open_app":
        name = str(call.args.get("name", "")).strip().lower()
        if name not in cfg["app_whitelist"]:
            raise ToolRejected(
                f"{name or 'that app'} isn't on my app list. I can open: "
                + ", ".join(cfg["app_whitelist"]) + "."
            )
        if shutil.which(name) is None:
            raise ToolRejected(f"{name} is allowed but doesn't seem to be installed.")
        return ResolvedAction(tool="open_app", argv=(name,))

    if call.name == "list_dir":
        path = _resolve_dir(str(call.args.get("path", "~")))
        return ResolvedAction(tool="list_dir", argv=("ls", "-la", str(path)))

    if call.name == "check_disk":
        return ResolvedAction(tool="check_disk", argv=("df", "-h", "/"))

    if call.name == "run_command":
        name = str(call.args.get("name", "")).strip()
        allowed = set(cfg["command_whitelist"]) & set(_COMMAND_ARGV)
        if name not in allowed:
            raise ToolRejected(
                f"{name or 'that command'} isn't whitelisted. I can run: "
                + ", ".join(sorted(allowed)) + "."
            )
        return ResolvedAction(tool="run_command", argv=_COMMAND_ARGV[name])

    if call.name == "describe_screen":
        question = str(call.args.get("question", "")).strip()
        return ResolvedAction(tool="describe_screen", screen_query=question or None)

    if call.name == "set_avatar":
        name = str(call.args.get("name", "")).strip().lower()
        if name not in installed_packs:
            have = ", ".join(installed_packs) if installed_packs else "none installed"
            raise ToolRejected(f"I don't have an avatar called {name or 'that'}; I have: {have}.")
        return ResolvedAction(tool="set_avatar", avatar_command=("set_pack", name))

    raise ToolRejected(f"I don't have a tool called {call.name}.")
