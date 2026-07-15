"""外部平台 Token 的凭据管理。

在 ~/.yanyu-wit/credentials/<provider>.json 中存储和检索 Token。
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_credentials_dir() -> Path:
    from cli.config import get_account_dir
    return get_account_dir() / "credentials"


def get_credential(provider: str) -> dict[str, Any] | None:
    """加载给定服务商的凭据。

    参数:
        provider: 服务商名称（例如 'flux'、'registry'）。

    返回:
        凭据字典，如果未找到则返回 None。
    """
    path = _get_credentials_dir() / f"{provider}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read credential for %s: %s", provider, exc)
        return None


def save_credential(provider: str, data: dict[str, Any]) -> None:
    """以受限制的文件权限保存服务商的凭据。

    参数:
        provider: 服务商名称。
        data: 要持久化的 Token 数据字典。
    """
    _get_credentials_dir().mkdir(parents=True, exist_ok=True)
    path = _get_credentials_dir() / f"{provider}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.debug("Could not set file permissions on %s", path)
    logger.info("Saved credential for '%s' to %s", provider, path)


def clear_credential(provider: str) -> bool:
    """删除已存储的服务商凭据。

    返回:
        如果凭据被成功删除则返回 True，如果凭据不存在则返回 False。
    """
    path = _get_credentials_dir() / f"{provider}.json"
    if path.exists():
        path.unlink()
        logger.info("Cleared credential for '%s'", provider)
        return True
    return False


def get_access_token(provider: str) -> str | None:
    """便利函数：从已存储的凭据中提取 access_token。"""
    cred = get_credential(provider)
    if cred:
        return cred.get("access_token")
    return None


def list_credentials() -> list[str]:
    """列出所有已存储凭据的服务商名称。"""
    if not _get_credentials_dir().exists():
        return []
    return [p.stem for p in _get_credentials_dir().glob("*.json")]
