"""
Logging configuration for Cremacuadrado API.

Dev  (DEBUG=True):  human-readable lines with color-friendly format.
Prod (DEBUG=False): one JSON object per line — parseable by Vercel log drain / external tools.

Every log record carries a `request_id` injected automatically from a ContextVar
so all lines produced during a single HTTP request share the same ID.
"""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# Set by the request-logging middleware; "-" outside request context.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SLOW_REQUEST_MS = 1_000   # warn if a request takes longer than this
_VERY_SLOW_MS   = 3_000   # error if longer than this


class _RequestIdFilter(logging.Filter):
    """Stamp every log record with the current request_id from context."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")
        return True


class _JsonFormatter(logging.Formatter):
    """Single-line JSON log entry for production / log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict = {
            "ts":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":      record.levelname,
            "logger":     record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg":        record.getMessage(),
        }
        for key in ("method", "path", "status_code", "duration_ms", "client_ip", "user_agent"):
            val = getattr(record, key, None)
            if val is not None:
                data[key] = val

        if record.exc_info:
            data["traceback"] = self.formatException(record.exc_info)

        return json.dumps(data, ensure_ascii=False)


class _DevFormatter(logging.Formatter):
    """Compact human-readable format for local development."""

    _LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color  = self._LEVEL_COLORS.get(record.levelname, "")
        reset  = self._RESET
        rid    = getattr(record, "request_id", "-")
        level  = f"{color}{record.levelname:<8}{reset}"
        name   = f"\033[90m{record.name}\033[0m"
        msg    = record.getMessage()

        line = (
            f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
            f"{level} [{rid}] {name}  {msg}"
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


def setup_logging(debug: bool = False) -> None:
    """Call once at application startup."""
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_DevFormatter() if debug else _JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Third-party loggers — avoid log spam
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)   # we log requests ourselves
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.INFO)
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("stripe").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.WARNING)
