"""`wit logs` — 查看后台运行日志的 CLI 子命令。

允许用户在终端分页查看最新的运行日志，支持过滤与行数参数。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `logs` 子命令。"""
    parser = subparsers.add_parser(
        "logs",
        help="查看后台运行日志",
    )
    parser.add_argument(
        "--lines", "-n",
        type=int,
        default=50,
        help="显示最近的行数（默认: 50）",
    )
    parser.add_argument(
        "--follow", "-f",
        action="store_true",
        help="实时追踪日志输出（类似 tail -f）",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="按关键词过滤日志行",
    )
    parser.add_argument(
        "--level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="按日志级别过滤",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="显示所有轮转的日志文件（不仅是当前活动日志）",
    )
    parser.set_defaults(func=execute)


def _find_log_files() -> list[Path]:
    """查找所有日志文件（包括轮转的备份文件）。"""
    from cli.utils.logging_config import get_log_dir, LOG_FILE_NAME

    log_dir = get_log_dir()
    if not log_dir.exists():
        return []

    files = []
    # 主日志文件
    main_log = log_dir / LOG_FILE_NAME
    if main_log.exists():
        files.append(main_log)

    # 轮转的备份日志 (yanyu-wit.log.1, yanyu-wit.log.2, ...)
    for i in range(1, 10):
        backup = log_dir / f"{LOG_FILE_NAME}.{i}"
        if backup.exists():
            files.append(backup)

    return files


def _tail_lines(filepath: Path, n: int, filter_str: str | None = None, level: str | None = None) -> list[str]:
    """读取文件的最后 N 行，支持过滤。"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return [f"⚠️ 无法读取日志文件 {filepath}: {e}"]

    # 过滤
    filtered = all_lines
    if filter_str:
        filtered = [line for line in filtered if filter_str in line]
    if level:
        filtered = [line for line in filtered if f"[{level}]" in line]

    # 取最后 N 行
    return filtered[-n:]


def _follow_log(filepath: Path, filter_str: str | None = None, level: str | None = None) -> None:
    """实时追踪日志文件输出（类似 tail -f）。"""
    print(f"📝 正在追踪日志: {filepath}")
    print("   按 Ctrl+C 退出")
    print("-" * 60)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            # 先定位到文件末尾
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    # 过滤
                    if filter_str and filter_str not in line:
                        continue
                    if level and f"[{level}]" not in line:
                        continue
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n\n👋 已停止追踪日志。")


def execute(args: argparse.Namespace) -> None:
    """执行 logs 命令。"""
    log_files = _find_log_files()

    if not log_files:
        from cli.utils.logging_config import get_log_dir
        print(f"⚠️ 未找到日志文件。")
        print(f"   日志目录: {get_log_dir()}")
        print(f"   💡 启动服务后将自动生成日志文件。")
        return

    main_log = log_files[0]

    # Follow 模式
    if args.follow:
        _follow_log(main_log, filter_str=args.filter, level=args.level)
        return

    # 显示日志文件信息
    print(f"📝 日志文件: {main_log}")
    size_kb = main_log.stat().st_size / 1024
    print(f"   大小: {size_kb:.1f} KB")

    if args.all_files and len(log_files) > 1:
        print(f"   轮转文件: {len(log_files) - 1} 个")
        for f in log_files[1:]:
            print(f"     - {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    print("-" * 60)

    # 如果需要查看所有文件，从旧到新合并
    if args.all_files and len(log_files) > 1:
        all_lines = []
        for f in reversed(log_files):
            all_lines.extend(_tail_lines(f, args.lines * 2, args.filter, args.level))
        lines = all_lines[-args.lines:]
    else:
        lines = _tail_lines(main_log, args.lines, args.filter, args.level)

    if not lines:
        if args.filter or args.level:
            print("  (无匹配的日志条目)")
        else:
            print("  (日志文件为空)")
        return

    for line in lines:
        # 简单的颜色高亮
        line_stripped = line.rstrip()
        if "[ERROR]" in line_stripped or "[CRITICAL]" in line_stripped:
            print(f"\033[91m{line_stripped}\033[0m")  # Red
        elif "[WARNING]" in line_stripped:
            print(f"\033[93m{line_stripped}\033[0m")  # Yellow
        elif "[DEBUG]" in line_stripped:
            print(f"\033[90m{line_stripped}\033[0m")  # Gray
        else:
            print(line_stripped)

    print("-" * 60)
    print(f"  显示最近 {len(lines)} 行")
    if not args.follow:
        print("  💡 使用 `wit logs -f` 实时追踪日志")
