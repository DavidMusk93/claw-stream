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

import os, sys, json, datetime, logging
from logging.handlers import TimedRotatingFileHandler

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _ensure_day_dir(log_dir):
    """按日期创建子目录，返回当天目录路径"""
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(log_dir, day)
    os.makedirs(path, exist_ok=True)
    return path


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "ts": datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat(),
            "name": record.name,
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            obj.update(record.extra)
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record):
        base = f"{self.formatTime(record)} [{record.name}] {record.levelname} {record.getMessage()}"
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

    def _log_with_extra(self, level, msg, extra=None, **kwargs):
        if extra is None:
            extra = {}
        if kwargs:
            extra.update(kwargs)
        self._log.log(level, msg, extra={"extra": extra} if extra else None)

    def debug(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.DEBUG, msg, extra, **kwargs)

    def info(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.INFO, msg, extra, **kwargs)

    def warning(self, msg, extra=None, **kwargs):
        self._log_with_extra(logging.WARNING, msg, extra, **kwargs)

    def error(self, msg, extra=None, exc_info=False, **kwargs):
        if extra is None:
            extra = {}
        if kwargs:
            extra.update(kwargs)
        self._log.log(logging.ERROR, msg, extra={"extra": extra} if extra else None, exc_info=exc_info)

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

    day_dir = _ensure_day_dir(log_dir)
    log_file = os.path.join(day_dir, f"{name}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 清除旧 handler（防止单元测试中重复添加）
    if logger.handlers:
        logger.handlers.clear()

    # 文件 handler（按天轮转，保留 7 天）
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=7,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.suffix = "%Y-%m-%d"
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
    log = get_logger(name, log_dir=log_dir)
    day_dir = _ensure_day_dir(log_dir or os.environ.get("LOG_DIR", DEFAULT_LOG_DIR))
    log_file = os.path.join(day_dir, f"{name}.log")

    f = open(log_file, "a", encoding="utf-8")
    tee = _TeeStream(f)
    sys.stdout = tee
    sys.stderr = tee
    return f
