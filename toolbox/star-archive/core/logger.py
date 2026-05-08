#!/usr/bin/env python3
"""logger.py — 共享日志模块

用法:
    from logger import get_logger
    log = get_logger("cache-server")
    log.info("server started")
    log.error("something wrong", exc_info=True)

环境变量:
    LOG_DIR     日志根目录 (默认: 脚本同级 logs/)
    LOG_JSON    1=JSON 格式, 0=文本格式 (默认: 0)
"""

import os, sys, json, datetime, logging, contextvars
from logging.handlers import RotatingFileHandler

# ── Trace ID 链路追踪 ──
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def set_trace_id(tid: str) -> None:
    trace_id_ctx.set(tid)


def get_trace_id() -> str:
    return trace_id_ctx.get()

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

# 回收策略：单个日志文件 10MB，保留 5 个备份
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _ensure_log_dir(log_dir):
    """确保日志根目录存在"""
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
    """双写流：同时输出到文件和 stdout，保留行缓冲行为"""
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
    """封装标准 logging，增加 extra 字段支持和便捷方法"""
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


# ── 缓存，避免重复创建 ──
_logger_cache = {}


def get_logger(name, log_dir=None, json_format=None):
    """获取或创建命名日志器

    Args:
        name: 日志器名称，决定文件名
        log_dir: 日志根目录，默认从 LOG_DIR 环境变量或 logs/
        json_format: True=JSON, False=文本, None=从 LOG_JSON 环境变量
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

    # 清除旧 handler（防止单元测试中重复添加）
    if logger.handlers:
        logger.handlers.clear()

    # 文件 handler（按大小滚动，保留 N 个备份）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    fmt = _JsonFormatter() if json_format else _TextFormatter()
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # stdout handler（保留原有输出习惯）
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    wrapper = LoggerWrapper(logger)
    _logger_cache[cache_key] = wrapper
    return wrapper


def capture_stdout(name, log_dir=None):
    """将当前进程 stdout/stderr 重定向到日志文件（同时保留终端输出）

    用于 refresh.sh 等场景，让 print() 也进入日志体系。
    """
    log_dir = _ensure_log_dir(log_dir or os.environ.get("LOG_DIR", DEFAULT_LOG_DIR))
    log_file = os.path.join(log_dir, f"{name}.log")

    f = open(log_file, "a", encoding="utf-8")
    tee = _TeeStream(f)
    sys.stdout = tee
    sys.stderr = tee
    return f
