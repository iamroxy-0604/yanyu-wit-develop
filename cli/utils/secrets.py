"""统一密钥管理模块。

实现 '大一统查找逻辑'：
  1. 优先检查系统环境变量
  2. 检查 ~/.yanyu-wit/secrets.json
  3. 若不存在，动态生成高强度随机密钥并写入文件
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SECRETS_FILE_NAME = "secrets.json"

# 硬编码默认值
DEFAULT_APP_JWT_ALGORITHM = "HS256"
DEFAULT_APP_JWT_EXPIRE_HOURS = 2


def _get_secrets_file_path() -> Path:
    """返回 ~/.yanyu-wit/secrets.json 的路径。"""
    from cli.config import YANYU_WIT_HOME
    return YANYU_WIT_HOME / _SECRETS_FILE_NAME


def _load_secrets_file() -> dict[str, Any]:
    """加载本地 secrets.json 文件。"""
    path = _get_secrets_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read secrets file: %s", exc)
        return {}


def _save_secrets_file(data: dict[str, Any]) -> None:
    """将 secrets 数据写入本地文件，并设置受限的文件权限。"""
    path = _get_secrets_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.debug("Could not set file permissions on %s", path)
    logger.info("Saved secrets to %s", path)


def _generate_strong_secret(length: int = 64) -> str:
    """生成一个 URL 安全的高强度随机密钥。"""
    return secrets.token_urlsafe(length)


def get_secret(key: str) -> str:
    """统一密钥获取逻辑。

    优先级：
      1. 系统环境变量
      2. ~/.yanyu-wit/secrets.json
      3. 自动生成并写入 secrets.json

    Args:
        key: 密钥名称（如 'APP_JWT_SECRET', 'SESSION_SECRET'）

    Returns:
        密钥字符串
    """
    # 1. 优先检查环境变量
    env_val = os.getenv(key)
    if env_val:
        return env_val

    # 2. 检查本地 secrets.json
    file_data = _load_secrets_file()
    if key in file_data and file_data[key]:
        return file_data[key]

    # 3. 动态生成并写入
    new_secret = _generate_strong_secret()
    file_data[key] = new_secret
    _save_secrets_file(file_data)
    logger.info("Auto-generated secret '%s' and saved to secrets file.", key)
    return new_secret


def get_app_jwt_secret() -> str:
    """获取 APP_JWT_SECRET。"""
    return get_secret("APP_JWT_SECRET")


def get_session_secret() -> str:
    """获取 SESSION_SECRET。"""
    return get_secret("SESSION_SECRET")


def get_app_jwt_algorithm() -> str:
    """获取 JWT 签名算法（硬编码默认值）。"""
    return DEFAULT_APP_JWT_ALGORITHM


def get_app_jwt_expire_hours() -> int:
    """获取 JWT 过期时间（硬编码默认值）。"""
    return DEFAULT_APP_JWT_EXPIRE_HOURS
