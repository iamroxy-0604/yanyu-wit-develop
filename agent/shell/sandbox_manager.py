"""PC 模式影子沙箱生命周期管理。

提供沙箱的自动清理、超时回收和手动清理功能：
  - cleanup_session_sandbox(): 删除会话时清理对应沙箱
  - cleanup_stale_sandboxes(): 清理超时的旧沙箱目录
  - list_sandboxes(): 列出当前所有沙箱及最后活动时间
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 沙箱根目录
SANDBOX_ROOT = Path.home() / ".yanyu-wit" / "sandbox"

# 默认超时阈值（72 小时）
DEFAULT_MAX_AGE_HOURS = 72


def get_sandbox_root() -> Path:
    """返回沙箱根目录路径。"""
    return SANDBOX_ROOT


def list_sandboxes() -> list[dict]:
    """列出当前所有沙箱及其基本信息。

    Returns:
        包含 name, path, last_modified, size_mb 信息的字典列表。
    """
    if not SANDBOX_ROOT.exists():
        return []

    result = []
    for entry in SANDBOX_ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            # 获取最后修改时间（递归扫描最新的文件时间）
            last_modified = _get_dir_last_modified(entry)
            # 粗略估算目录大小（不包括软链接目标）
            size_bytes = _get_dir_size(entry)
            result.append({
                "name": entry.name,
                "path": str(entry),
                "last_modified": last_modified,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "age_hours": round((time.time() - last_modified) / 3600, 1),
            })
        except Exception as e:
            logger.debug("Failed to stat sandbox %s: %s", entry.name, e)
            result.append({
                "name": entry.name,
                "path": str(entry),
                "last_modified": 0,
                "size_mb": 0,
                "age_hours": -1,
            })

    # 按最后修改时间倒序
    result.sort(key=lambda x: x["last_modified"], reverse=True)
    return result


def cleanup_session_sandbox(session_id: str) -> bool:
    """删除指定会话 ID 关联的沙箱目录。

    PC 模式下沙箱命名规则为 `pc_<workspace_hash>`，由于无法仅通过 session_id
    精确定位对应的沙箱（同一工作区的所有会话共享一个沙箱），此函数提供基于
    session_id 前缀匹配的清理。

    Returns:
        如果找到并删除了沙箱目录则返回 True。
    """
    if not SANDBOX_ROOT.exists():
        return False

    cleaned = False
    for entry in SANDBOX_ROOT.iterdir():
        if entry.is_dir() and session_id in entry.name:
            try:
                _safe_rmtree(entry)
                logger.info("Cleaned up sandbox for session %s: %s", session_id, entry)
                cleaned = True
            except Exception as e:
                logger.error("Failed to clean up sandbox %s: %s", entry, e)
    return cleaned


def cleanup_workspace_sandbox(workspace_dir: str) -> bool:
    """删除指定工作区关联的沙箱目录。

    Args:
        workspace_dir: 工作区物理路径。

    Returns:
        如果找到并删除了沙箱目录则返回 True。
    """
    import hashlib
    ws_str = str(Path(workspace_dir).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_name = f"pc_{ws_hash}"
    sandbox_path = SANDBOX_ROOT / sandbox_name

    if sandbox_path.exists():
        try:
            _safe_rmtree(sandbox_path)
            logger.info("Cleaned up workspace sandbox: %s", sandbox_path)
            return True
        except Exception as e:
            logger.error("Failed to clean up workspace sandbox %s: %s", sandbox_path, e)
    return False


def cleanup_stale_sandboxes(max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> int:
    """清理超时的旧沙箱目录。

    Args:
        max_age_hours: 沙箱最大存活时间（小时），默认 72 小时。

    Returns:
        被清理的沙箱数量。
    """
    if not SANDBOX_ROOT.exists():
        return 0

    now = time.time()
    max_age_seconds = max_age_hours * 3600
    cleaned_count = 0

    for entry in SANDBOX_ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            last_modified = _get_dir_last_modified(entry)
            age = now - last_modified
            if age > max_age_seconds:
                age_hours = round(age / 3600, 1)
                logger.info(
                    "Cleaning stale sandbox '%s' (last active %.1f hours ago, threshold %.1f hours)",
                    entry.name, age_hours, max_age_hours,
                )
                _safe_rmtree(entry)
                cleaned_count += 1
        except Exception as e:
            logger.warning("Failed to check/clean sandbox %s: %s", entry.name, e)

    if cleaned_count > 0:
        logger.info("Cleaned up %d stale sandbox(es)", cleaned_count)
    else:
        logger.debug("No stale sandboxes found (threshold: %.1f hours)", max_age_hours)

    return cleaned_count


def cleanup_all_sandboxes() -> int:
    """清理所有沙箱目录。

    Returns:
        被清理的沙箱数量。
    """
    if not SANDBOX_ROOT.exists():
        return 0

    count = 0
    for entry in SANDBOX_ROOT.iterdir():
        if entry.is_dir():
            try:
                _safe_rmtree(entry)
                count += 1
            except Exception as e:
                logger.error("Failed to remove sandbox %s: %s", entry, e)
    return count


def get_sandbox_stats() -> dict:
    """获取沙箱的统计汇总信息。

    Returns:
        包含 count, total_size_mb, oldest_age_hours 等信息的字典。
    """
    sandboxes = list_sandboxes()
    if not sandboxes:
        return {
            "count": 0,
            "total_size_mb": 0,
            "oldest_age_hours": 0,
            "newest_age_hours": 0,
        }

    return {
        "count": len(sandboxes),
        "total_size_mb": round(sum(s["size_mb"] for s in sandboxes), 2),
        "oldest_age_hours": max(s["age_hours"] for s in sandboxes),
        "newest_age_hours": min(s["age_hours"] for s in sandboxes if s["age_hours"] >= 0),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_dir_last_modified(path: Path) -> float:
    """获取目录中最新文件的修改时间戳。"""
    latest = path.stat().st_mtime
    try:
        for root, dirs, files in os.walk(path):
            # 排除 .git 目录和软链接
            dirs[:] = [d for d in dirs if d != ".git" and not os.path.islink(os.path.join(root, d))]
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    try:
                        mtime = os.path.getmtime(fp)
                        if mtime > latest:
                            latest = mtime
                    except OSError:
                        pass
            # 只扫描前 100 个文件即可
            if len(files) > 100:
                break
    except OSError:
        pass
    return latest


def _get_dir_size(path: Path) -> int:
    """估算目录大小（不跟随软链接）。"""
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            # 排除 .git 和软链接目录
            dirs[:] = [d for d in dirs if d != ".git" and not os.path.islink(os.path.join(root, d))]
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def _safe_rmtree(path: Path) -> None:
    """安全删除目录树，处理软链接而不跟随它们。"""
    if not path.exists():
        return

    # 首先移除直接的软链接子条目（node_modules, .venv 等），避免删除真实目标
    for child in path.iterdir():
        if child.is_symlink():
            try:
                child.unlink()
                logger.debug("Unlinked symlink: %s", child)
            except Exception as e:
                logger.warning("Failed to unlink symlink %s: %s", child, e)

    # 然后安全删除剩余目录
    shutil.rmtree(path, ignore_errors=True)
