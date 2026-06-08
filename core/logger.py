#!/usr/bin/env python3
"""logger.py — Shared logging module

Usage:
    from logger import get_logger
    log = get_logger("cache-server")
    log.info("server started")
    log.error("something wrong", exc_info=True)

Environment variables:
    LOG_DIR     Log root directory (default: logs/ next to script)
    LOG_JSON    1=JSON format, 0=text format (default: 0)
"""

from __future__ import annotations

import os, sys, json, datetime, logging, contextvars
from logging.handlers import RotatingFileHandler

# ── Trace ID chain tracking ──
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def set_trace_id(tid: str) -> None:
    trace_id_ctx.set(tid)


def get_trace_id() -> str:
    return trace_id_ctx.get()

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

# Rollover policy: 10MB per log file, keep 5 backups
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _ensure_log_dir(log_dir):
    """Ensure log root directory exists"""
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "ts": datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat(),
            "trace_id": trace_id_ctx.get(),
            "name": record.name,
            "level": record.levelname,
            "file": record.filename,
            "line": record.lineno,
            "func": record.funcName,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            obj.update(record.extra)
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record):
        tid = trace_id_ctx.get()
        tid_str = f"[{tid}] " if tid != "-" else ""
        base = f"{self.formatTime(record)} {tid_str}[{record.name}] {record.levelname} {record.filename}:{record.lineno} {record.getMessage()}"
        # Append extra fields so AI agents can grep/awk structured data
        extra = getattr(record, "extra", None)
        if extra:
            pairs = " ".join(f"{k}={v}" for k, v in extra.items())
            base += " | " + pairs
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base

    def formatTime(self, record, datefmt=None):
        return datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]


class _TeeStream:
    """Dual-write stream: outputs to both file and stdout, preserving line-buffering behavior"""
    def __init__(self, file_stream, stdout=sys.stdout):
        self.file = file_stream
        self.stdout = stdout

    def write(self, data):
        self.file.write(data)
        self.file.flush()
        if self.stdout:
            self.stdout.write(data)
            self.stdout.flush()

    def flush(self):
        self.file.flush()
        if self.stdout:
            self.stdout.flush()


class LoggerWrapper:
    """Wraps standard logging with extra field support and convenience methods"""
    def __init__(self, logger):
        self._log = logger

    def _log_with_extra(self, level, msg, extra=None, exc_info=False, **kwargs):
        if extra is None:
            extra = {}
        if kwargs:
            extra.update(kwargs)
        self._log.log(
            level, msg,
            extra={"extra": extra} if extra else None,
            exc_info=exc_info,
            stacklevel=3,
        )

    def debug(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.DEBUG, msg, extra, **kwargs)

    def info(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.INFO, msg, extra, **kwargs)

    def warning(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.WARNING, msg, extra, **kwargs)

    def error(self, msg, extra=None, exc_info=False, **kwargs):
        self._log_with_extra(logging.ERROR, msg, extra, exc_info=exc_info, **kwargs)

    def exception(self, msg, extra=None, **kwargs):
        self.error(msg, extra=extra, exc_info=True, **kwargs)

    def __getattr__(self, name):
        return getattr(self._log, name)


# ── Cache to avoid duplicate creation ──
_logger_cache = {}


def get_logger(name, log_dir=None, json_format=None):
    """Get or create a named logger

    Args:
        name: Logger name, determines file name
        log_dir: Log root directory, defaults to LOG_DIR env var or logs/
        json_format: True=JSON, False=text, None=from LOG_JSON env var
    """
    cache_key = (name, log_dir, json_format)
    if cache_key in _logger_cache:
        return _logger_cache[cache_key]

    if log_dir is None:
        log_dir = os.environ.get("LOG_DIR", DEFAULT_LOG_DIR)
    if json_format is None:
        json_format = os.environ.get("LOG_JSON", "0") == "1"

    log_dir = _ensure_log_dir(log_dir)
    log_file = os.path.join(log_dir, f"{name}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Clear old handlers (prevent duplicate adds in unit tests)
    if logger.handlers:
        logger.handlers.clear()

    # File handler (size-based rollover, keep N backups)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    fmt = _JsonFormatter() if json_format else _TextFormatter()
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # stdout handler (preserve existing output habits)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    wrapper = LoggerWrapper(logger)
    _logger_cache[cache_key] = wrapper
    return wrapper


def capture_stdout(name, log_dir=None):
    """Redirect current process stdout/stderr to log file (while preserving terminal output)

    Used in refresh.sh and similar scenarios so print() also enters the logging system.
    """
    log_dir = _ensure_log_dir(log_dir or os.environ.get("LOG_DIR", DEFAULT_LOG_DIR))
    log_file = os.path.join(log_dir, f"{name}.log")

    f = open(log_file, "a", encoding="utf-8")
    tee = _TeeStream(f)
    sys.stdout = tee
    sys.stderr = tee
    return f
