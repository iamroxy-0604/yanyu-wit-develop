"""`wit status` — 查询当前 Yanyu-Wit 后端服务运行状态的 CLI 子命令。

支持在终端快速查询当前是否有正在运行的 wit 后端服务、占用端口以及活动账户信息。
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `status` 子命令。"""
    parser = subparsers.add_parser(
        "status",
        help="查询 Yanyu-Wit 服务运行状态",
    )
    parser.set_defaults(func=execute)


def _find_wit_processes() -> list[dict]:
    """查找正在运行的 wit/uvicorn 相关进程。

    Returns:
        包含 pid, port, cmd 信息的字典列表。
    """
    results = []
    try:
        # 使用 ps + grep 查找 uvicorn service.app 相关进程
        proc = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return results

        for line in proc.stdout.splitlines():
            # 匹配 uvicorn 运行 service.app 的进程，或者 wit start 启动的子进程
            if "service.app" in line and ("uvicorn" in line or "python" in line):
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    # 尝试从命令行中解析端口
                    port = _extract_port_from_cmdline(line)
                    results.append({
                        "pid": pid,
                        "port": port,
                        "cmd": " ".join(parts[10:]) if len(parts) > 10 else line,
                    })
    except Exception as e:
        logger.debug("Failed to find wit processes: %s", e)
    return results


def _extract_port_from_cmdline(cmdline: str) -> str | None:
    """从进程命令行中提取端口号。"""
    import re
    # 匹配 port=数字 或 --port 数字 或 -p 数字
    patterns = [
        r'port[=:](\d+)',
        r'--port\s+(\d+)',
        r'-p\s+(\d+)',
        r'WIT_PORT_OVERRIDE.*?["\'](\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, cmdline)
        if match:
            return match.group(1)
    return None


def execute(args: argparse.Namespace) -> None:
    """执行 status 命令。"""
    print("🔍 Yanyu-Wit 服务状态")
    print("=" * 50)

    # 1. 查询活动账户
    try:
        from cli.config import get_active_account, YANYU_WIT_HOME
        active_account = get_active_account()
        if active_account:
            print(f"  👤 活动账户: {active_account}")
        else:
            print("  👤 活动账户: 未配置 (请运行 `wit init`)")
    except Exception:
        print("  👤 活动账户: 未知")

    # 2. 查询配置的端口
    try:
        from cli.utils.port import resolve_port
        configured_port = resolve_port()
        print(f"  🔌 配置端口: {configured_port}")
    except Exception:
        print("  🔌 配置端口: 7020 (默认)")

    # 3. 查找运行中的服务进程
    print()
    processes = _find_wit_processes()
    if processes:
        print("  🟢 服务状态: 运行中")
        print()
        for p in processes:
            port_str = f"端口 {p['port']}" if p["port"] else "端口未知"
            print(f"    PID: {p['pid']}  |  {port_str}")
    else:
        print("  🔴 服务状态: 未运行")
        print()
        print("  💡 启动服务: wit start [-p 端口号]")

    # 4. 显示配置文件位置
    print()
    try:
        from cli.config import get_account_config_path, YANYU_WIT_HOME
        config_path = get_account_config_path()
        global_config = YANYU_WIT_HOME / "config.toml"
        print(f"  📂 全局配置: {global_config}")
        print(f"  📂 账户配置: {config_path}")
    except Exception:
        pass

    # 5. 显示沙箱状态
    try:
        sandbox_dir = Path.home() / ".yanyu-wit" / "sandbox"
        if sandbox_dir.exists():
            sandbox_count = sum(1 for d in sandbox_dir.iterdir() if d.is_dir())
            print(f"  📦 活动沙箱: {sandbox_count} 个")
        else:
            print("  📦 活动沙箱: 无")
    except Exception:
        pass

    # 6. 显示日志文件位置
    try:
        log_dir = Path.home() / ".yanyu-wit" / "logs"
        log_file = log_dir / "yanyu-wit.log"
        if log_file.exists():
            size_kb = log_file.stat().st_size / 1024
            print(f"  📝 日志文件: {log_file} ({size_kb:.1f} KB)")
        else:
            print(f"  📝 日志文件: {log_file} (尚未创建)")
    except Exception:
        pass

    print()
    print("=" * 50)
