"""Frozen runtime configuration loaded once from config.yaml (§15).

The returned mapping is deeply immutable: mutating it anywhere raises,
enforcing the frozen-config rule. The sole sanctioned runtime exception is
the avatar's active pack, which lives in avatar_state.json (DL-8), not here.
"""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return MappingProxyType({k: _freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_freeze(v) for v in obj)
    return obj


_config: Any = None


def load(path: Path | None = None) -> Any:
    """Load and freeze the config. Idempotent; explicit path is for tests."""
    global _config
    if _config is None or path is not None:
        with open(path or ROOT / "config.yaml") as f:
            frozen = _freeze(yaml.safe_load(f))
        if path is None:
            _config = frozen
        else:
            return frozen
    return _config


def get() -> Any:
    """Return the already-loaded frozen config (loads on first call)."""
    return load()
