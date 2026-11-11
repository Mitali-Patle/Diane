"""Frozen runtime configuration loaded once from config.yaml (§15).

The returned mapping is deeply immutable: mutating it anywhere raises,
enforcing the frozen-config rule. The sole sanctioned runtime exception is
the avatar's active pack, which lives in avatar_state.json (DL-8), not here.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where mutable runtime files live (logs, avatar_state.json).

    Defaults to the repo root. DIANE_DATA_DIR overrides it — the test suite
    points it at a temp dir so test runs never append to the real audit log
    (P7/SC4: the audit log must only ever hold the owner's real executions).
    """
    override = os.environ.get("DIANE_DATA_DIR")
    return Path(override) if override else ROOT


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
