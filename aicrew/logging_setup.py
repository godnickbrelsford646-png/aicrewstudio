"""Structured logging with run-context."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

_pipeline_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pipeline_run_id", default=None
)
_agent_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agent_run_id", default=None
)


def set_pipeline_run(run_id: str | None) -> None:
    _pipeline_run_id.set(run_id)


def set_agent_run(run_id: str | None) -> None:
    _agent_run_id.set(run_id)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        prid = _pipeline_run_id.get()
        if prid:
            payload["pipeline_run_id"] = prid
        arid = _agent_run_id.get()
        if arid:
            payload["agent_run_id"] = arid
        for k, v in record.__dict__.items():
            if k.startswith("ctx_"):
                payload[k[4:]] = v
        return json.dumps(payload, ensure_ascii=False)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
