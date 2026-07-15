"""统一日志配置模块。

实现多模式日志持久化存储：
  - 开发态：日志写入项目根目录下的 logs/yanyu-wit.log
  - 运行/打包态：日志写入 ~/.yanyu-wit/logs/yanyu-wit.log
  - 支持日志轮转（RotatingFileHandler），最大 10MB 保留 5 个文件
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 日志文件名
LOG_FILE_NAME = "yanyu-wit.log"

# 轮转配置
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5

# 日志格式
CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_dir() -> Path:
    """根据运行环境确定日志目录。

    开发态（项目根目录存在 pyproject.toml）：项目根目录/logs/
    运行/打包态：~/.yanyu-wit/logs/
    """
    # 检查是否处于 PyInstaller 打包环境
    if getattr(sys, 'frozen', False):
        # 打包态：使用用户家目录
        return Path.home() / ".yanyu-wit" / "logs"

    # 检查当前工作目录或文件所在目录是否有 pyproject.toml（开发态）
    project_root = _find_project_root()
    if project_root:
        return project_root / "logs"

    # 默认使用家目录
    return Path.home() / ".yanyu-wit" / "logs"


def get_log_file_path() -> Path:
    """返回日志文件的完整路径。"""
    return get_log_dir() / LOG_FILE_NAME


def _find_project_root() -> Path | None:
    """向上搜索项目根目录（通过 pyproject.toml 标识）。"""
    # 首先检查 CLI 模块所在目录的上级
    try:
        cli_dir = Path(__file__).resolve().parent.parent.parent
        if (cli_dir / "pyproject.toml").exists():
            return cli_dir
    except Exception:
        pass

    # 然后检查当前工作目录
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists():
        return cwd

    # 向上搜索最多 5 层
    current = cwd
    for _ in range(5):
        parent = current.parent
        if parent == current:
            break
        if (parent / "pyproject.toml").exists():
            return parent
        current = parent

    return None


def setup_logging(
    level: int | str = logging.INFO,
    debug: bool = False,
    enable_file: bool = True,
    console: bool = True,
) -> None:
    """配置统一的日志系统。

    Args:
        level: 日志级别，传入 logging.DEBUG/INFO/WARNING 或字符串。
        debug: 如果为 True，强制设置为 DEBUG 级别。
        enable_file: 是否启用文件日志（持久化）。
        console: 是否启用控制台日志输出。
    """
    if debug:
        level = logging.DEBUG
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # 获取根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 handler，避免重复
    root_logger.handlers.clear()

    # 控制台 handler
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
        root_logger.addHandler(console_handler)

    # 文件 handler（带轮转）
    if enable_file:
        try:
            log_dir = get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / LOG_FILE_NAME

            file_handler = RotatingFileHandler(
                str(log_file),
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)  # 文件始终记录 DEBUG 以上所有级别
            file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
            root_logger.addHandler(file_handler)

            logging.getLogger(__name__).debug(
                "File logging initialized at %s (max %d MB, %d backups)",
                log_file, MAX_LOG_BYTES // (1024 * 1024), BACKUP_COUNT,
            )
        except Exception as e:
            # 文件日志初始化失败不应阻止程序运行
            logging.getLogger(__name__).warning(
                "Failed to initialize file logging: %s", e,
            )

    # 降低第三方库的日志噪音
    for noisy_logger in (
        "httpx", "httpcore", "urllib3", "asyncio",
        "watchfiles", "uvicorn.access",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def setup_service_logging(
    debug: bool = False,
) -> None:
    """为后端服务（wit start 拉起的 uvicorn 进程）配置日志。

    与 CLI 日志共享同一个日志文件，但增加 JSON 结构化日志 handler。
    """
    setup_logging(
        level=logging.DEBUG if debug else logging.INFO,
        debug=debug,
        enable_file=True,
        console=True,
    )

    # 为 uvicorn 配置文件日志
    for uv_logger_name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(uv_logger_name)
        # 让 uvicorn 日志也输出到文件
        uv_logger.propagate = True
