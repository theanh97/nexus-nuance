"""
Centralized logging configuration for NEXUS.

Usage in any module:
    from core.nexus_logger import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened")

Environment variables:
    NEXUS_LOG_LEVEL  – DEBUG / INFO / WARNING / ERROR  (default: INFO)
    NEXUS_LOG_FILE   – optional path to append logs (default: None)
    NEXUS_LOG_JSON   – set "1" for JSON-lines output   (default: 0)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


_CONFIGURED = False

# ---------------------------------------------------------------------------
# JSON formatter for structured logging
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Console formatter (human-readable)
# ---------------------------------------------------------------------------

class _ConsoleFormatter(logging.Formatter):
    """Compact coloured output for terminals."""

    COLORS = {
        "DEBUG": "\033[36m",      # cyan
        "INFO": "\033[32m",       # green
        "WARNING": "\033[33m",    # yellow
        "ERROR": "\033[31m",      # red
        "CRITICAL": "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.now().strftime("%H:%M:%S")
        name = record.name.rsplit(".", 1)[-1]  # short module name
        msg = record.getMessage()
        base = f"{color}[{ts}] {record.levelname:<7}{self.RESET} {name}: {msg}"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Configure the root NEXUS logger (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_name = os.getenv("NEXUS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = os.getenv("NEXUS_LOG_JSON", "0") == "1"
    log_file = os.getenv("NEXUS_LOG_FILE")

    root = logging.getLogger("nexus")
    root.setLevel(level)
    root.propagate = False

    # Console handler
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(level)
    ch.setFormatter(_JSONFormatter() if use_json else _ConsoleFormatter())
    root.addHandler(ch)

    # Optional file handler
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(_JSONFormatter())  # always JSON for files
        root.addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'nexus' namespace.

    Automatically calls setup_logging() on first use.
    """
    setup_logging()
    if not name.startswith("nexus."):
        name = f"nexus.{name}"
    return logging.getLogger(name)
