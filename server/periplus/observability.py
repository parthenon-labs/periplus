"""Structured logging: one line per thing that happened, machine-readable by default.

This exists to answer one question that nothing else in the codebase could: *a run is
stuck — what is it doing right now?* Every stage attempt, every retry, every gate
rejection, every backward pass and every failed background write already produces a
precise, structured record inside the process; before this, all of it was reachable only
by polling ``/runs/{id}`` afterwards, and a write that failed on its way to Postgres was
reachable nowhere at all.

Deliberately not a metrics or tracing stack. OpenTelemetry, a Prometheus exporter and a
span-per-stage would all be defensible on a deployed service; on a service that is not
deployed they are configuration to maintain in exchange for dashboards nobody reads. What
does earn its keep is that the events are *structured* — ``run_id``, ``stage``,
``attempt``, ``duration_ms`` as fields rather than interpolated into prose — so grepping
one run out of interleaved concurrent runs is `jq`, not a regex.

Format is chosen by configuration, not guessed: ``json`` (the default) emits one JSON
object per line for a log collector; ``text`` emits a readable line for a terminal.
Nothing here ever logs a prompt, a page body, an API key or a claim's text — the audit
trail on the ``Run`` itself is where content belongs, and it is already inspectable.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

#: Root of this application's logger tree. Everything logs through a child of this, so a
#: host application can silence or re-route Periplus without touching the root logger.
LOGGER_NAME = "periplus"

#: The key structured fields travel under. One reserved name, rather than splatting
#: fields onto the record, because ``LogRecord`` already owns ``name``, ``msg``, ``args``,
#: ``module``, ``process`` and a dozen more — an ``extra`` that collides with any of them
#: raises at call time, and "logging blew up while reporting a failure" is a bad trade.
CONTEXT_KEY = "context"


def get_logger(name: str) -> logging.Logger:
    """A child logger under :data:`LOGGER_NAME`. ``name`` is a dotted suffix."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def log_context(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields attached to ``record``, or an empty dict."""
    context = getattr(record, CONTEXT_KEY, None)
    return dict(context) if isinstance(context, dict) else {}


class JSONFormatter(logging.Formatter):
    """One JSON object per line: the fixed envelope, then the event's own fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            **log_context(record),
        }
        if record.exc_info:
            # The traceback is one field rather than trailing lines, so an exception
            # cannot split a run's log into records a collector reads as separate events.
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable: the message, then ``key=value`` for whatever came with it."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        context = log_context(record)
        if context:
            line = f"{line} " + " ".join(f"{key}={value}" for key, value in context.items())
        return line


def configure_logging(*, level: str = "INFO", log_format: str = "json") -> None:
    """Attach one handler to the Periplus logger tree. Safe to call more than once.

    Idempotent on purpose: an application that builds its app object twice — every test
    that calls ``create_app`` does — must not end up with the same line emitted twice per
    event. ``propagate`` is turned off so these records never reach the root logger's
    handlers as well, which is how uvicorn's own configuration would otherwise duplicate
    them a third time.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False

    formatter = JSONFormatter() if log_format.lower() == "json" else TextFormatter()
    for handler in logger.handlers:
        if getattr(handler, "_periplus", False):
            handler.setFormatter(formatter)
            return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    handler._periplus = True  # type: ignore[attr-defined]
    logger.addHandler(handler)


__all__ = [
    "CONTEXT_KEY",
    "LOGGER_NAME",
    "JSONFormatter",
    "TextFormatter",
    "configure_logging",
    "get_logger",
    "log_context",
]
