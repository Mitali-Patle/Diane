"""Two log streams (§17): app log and the append-only audit log (P7).

The audit logger writes one line per executed command and can never truncate:
the file is opened in append mode and no handler here ever rotates or rewrites.
The app log rotates (size-capped) so a months-long always-on session can't
fill the disk (SC6). Prohibited at every level: cursor coordinates, window
positions, transcripts (NFR1, D-2).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jarvis import config

APP_LOG_MAX_BYTES = 5 * 1024 * 1024
APP_LOG_BACKUPS = 3


def _path(key: str) -> Path:
    path = config.data_dir() / config.get()["logging"][key]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def setup() -> logging.Logger:
    """Configure the app logger; returns it. Safe to call more than once."""
    cfg = config.get()
    logger = logging.getLogger("jarvis")
    if not logger.handlers:
        handler = RotatingFileHandler(
            _path("app_log"), mode="a", maxBytes=APP_LOG_MAX_BYTES, backupCount=APP_LOG_BACKUPS
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(cfg["logging"]["level"])
    return logger


def audit() -> logging.Logger:
    """The append-only audit logger (P7). One record per executed command."""
    logger = logging.getLogger("jarvis.audit")
    if not logger.handlers:
        handler = logging.FileHandler(_path("audit_log"), mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def audit_path() -> Path:
    return _path("audit_log")
