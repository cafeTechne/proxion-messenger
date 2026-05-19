"""Structured logging configuration for Proxion Gateway (R13.5)."""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Per-request ID context variable
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:8]
    REQUEST_ID.set(rid)
    return rid


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = REQUEST_ID.get("")
        if rid:
            obj["request_id"] = rid
        for key in ("webid", "room_id", "msg_id", "peer", "from", "request_id"):
            val = record.__dict__.get(key)
            if val is not None:
                obj[key] = val
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def configure_logging(
    json_output: bool = False,
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = []

    # Console handler
    console = logging.StreamHandler()
    if json_output:
        console.setFormatter(_JsonFormatter())
    else:
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    handlers.append(console)

    # File handler (rotating, 10 MB × 5)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            Path(log_dir) / "proxion.log",
            maxBytes=10_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonFormatter())
        handlers.append(fh)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
