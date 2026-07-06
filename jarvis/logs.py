"""Two log streams (§17): app log and the append-only audit log (P7).

The audit logger writes one line per executed command and can never truncate:
the file is opened in append mode and no handler here ever rotates or rewrites.
Prohibited at every level: cursor coordinates, window positions, transcripts (NFR1, D-2).
"""
from __future__ import annotations

import logging
from pathlib import Path

from jarvis import config


def setup() -> logging.Logger:
    """Configure the app logger; returns it. Safe to call more than once."""
    cfg = config.get()
    app_path = Path(config.ROOT / cfg["logging"]["app_log"])
    app_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("jarvis")
    if not logger.handlers:
        handler = logging.FileHandler(app_path, mode="a")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(cfg["logging"]["level"])
    return logger


def audit() -> logging.Logger:
    """The append-only audit logger (P7). One record per executed command."""
    cfg = config.get()
    audit_path = Path(config.ROOT / cfg["logging"]["audit_log"])
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("jarvis.audit")
    if not logger.handlers:
        handler = logging.FileHandler(audit_path, mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
