"""Yanyu-Wit 命令行接口 (CLI) 主入口点。

处理参数解析、模式检测（子命令 / 非交互 / 管道），并分发给相应的处理器。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from cli import __version__
from cli.config import (
    get_account_config_path,
    YANYU_WIT_HOME,
    ensure_home_dir,
    load_config,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """构建包含所有子命令的顶级参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="wit",
        description="🚀 Yanyu-Wit — 社交信息服务代理个人助手 CLI",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"yanyu-wit {__version__}",
    )
    parser.add_argument(
        "-n", "--non-interactive",
        metavar="PROMPT",
        help="非交互模式：直接发送提示词并输出结果",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试日志",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # Register subcommands
    from cli.commands.init_cmd import register_parser as reg_init
    from cli.commands.atr_cmd import register_parser as reg_atr
    from cli.commands.provider_cmd import register_parser as reg_provider
    from cli.commands.start_cmd import register_parser as reg_start
    from cli.commands.status_cmd import register_parser as reg_status
    from cli.commands.config_cmd import register_parser as reg_config
    from cli.commands.logs_cmd import register_parser as reg_logs
    from cli.commands.bugreport_cmd import register_parser as reg_bugreport

    reg_init(subparsers)
    reg_atr(subparsers)
    reg_provider(subparsers)
    reg_start(subparsers)
    reg_status(subparsers)
    reg_config(subparsers)
    reg_logs(subparsers)
    reg_bugreport(subparsers)

    return parser


def _setup_logging(debug: bool = False) -> None:
    """为 CLI 配置统一的日志记录（含持久化文件日志）。"""
    from cli.utils.logging_config import setup_logging
    setup_logging(
        level=logging.DEBUG if debug else logging.WARNING,
        debug=debug,
        enable_file=True,
        console=True,
    )


async def _run_non_interactive(
    prompt: str,
    thread_id: str = "cli-noninteractive",
    check_sandbox: bool = True,
) -> None:
    """在非交互模式下运行 Agent：单次提示词 → 标准输出。"""
    from agent.runtime import AgentRuntime

    home_dir = YANYU_WIT_HOME
    project_dir = Path.cwd()

    runtime = AgentRuntime()
    await runtime.initialize()

    # Determine workspace dir for checkpointer
    yanyu_dir = project_dir / ".yanyu"
    if yanyu_dir.exists():
        workspace = str(yanyu_dir)
    else:
        workspace = str(home_dir)

    try:
        async for event_line in runtime.stream_chat(
            thread_id=thread_id,
            user_message=prompt,
            workspace_dir=workspace,
        ):
            if event_line.startswith("data: "):
                try:
                    data = json.loads(event_line[6:])
                    if data.get("type") == "token":
                        print(data["content"], end="", flush=True)
                    elif data.get("type") == "error":
                        print(f"\n❌ Error: {data['message']}", file=sys.stderr)
                    elif data.get("type") == "done":
                        print()  # Final newline
                except json.JSONDecodeError:
                    pass
    finally:
        await runtime.close()

        # Check sandbox changes in PC mode if check_sandbox is enabled
        if check_sandbox:
            from service.feature_flags import get_flags
            if get_flags().sandbox_type == "local":
                import os
                sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / thread_id
                if sandbox_dir.exists():
                    from agent.shell.local_sandbox import get_sandbox_diff, apply_sandbox_changes, discard_sandbox_changes
                    diffs = get_sandbox_diff(workspace, sandbox_dir)
                    if diffs:
                        if sys.stdin.isatty():
                            print("\n" + "="*50)
                            print("📢  检测到沙箱工作区有以下变更：")
                            print("="*50)
                            for d in diffs:
                                print(f"[{d['type'].upper()}] {d['path']}")
                                if d.get("diff"):
                                    print("-" * 30)
                                    print(d["diff"])
                                    print("-" * 30)
                            print("="*50)
                            try:
                                ans = input("是否将以上修改应用到真实工作区？[y/N]: ").strip().lower()
                            except (KeyboardInterrupt, EOFError):
                                ans = "n"
                            if ans in ("y", "yes"):
                                apply_sandbox_changes(workspace, sandbox_dir)
                                print("✅  变更已成功应用到真实工作区！")
                            else:
                                discard_sandbox_changes(sandbox_dir)
                                print("❌  变更已丢弃。")
                        else:
                            print("\n⚠️  非交互式执行（无TTY），沙箱变更将自动丢弃。请使用交互式界面进行确认。", file=sys.stderr)
                            discard_sandbox_changes(sandbox_dir)
                    else:
                        discard_sandbox_changes(sandbox_dir)


def main() -> None:
    """CLI 主入口点。"""
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")

    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(getattr(args, "debug", False))

    # Commands that do not require prior initialization
    no_init_required = {"init", "logs", "bugreport", "status"}

    # If it is not an exempted command, check if config.toml exists. If not, raise error and exit.
    if args.command not in no_init_required and not get_account_config_path().exists():
        print("💡 请先运行以下命令初始化配置与模型信息：", file=sys.stderr)
        print("   wit init", file=sys.stderr)
        sys.exit(1)

    # Ensure home dir exists for all commands (except init, which handles its own directory creation after login)
    if args.command not in no_init_required:
        ensure_home_dir()

    # If a subcommand was invoked, dispatch to its handler
    if hasattr(args, "func"):
        args.func(args)
        return

    project_dir = Path.cwd()

    # Non-interactive mode: -n flag or piped stdin
    if args.non_interactive:
        asyncio.run(_run_non_interactive(args.non_interactive))
        return

    if not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            asyncio.run(_run_non_interactive(prompt))
        return

    # No subcommand and no -n flag: prompt user to use web UI or start command
    print("🚀 Yanyu-Wit 服务")
    print()
    print("请通过以下方式使用：")
    print("  1. 启动服务:     wit start [-p 端口号]")
    print("  2. 非交互模式:   wit -n \"你的问题\"")
    print("  3. 管道模式:     echo \"你的问题\" | wit")
    print()
    print("可用子命令:")
    print("  wit init       — 初始化配置与登录")
    print("  wit start      — 启动后端服务")
    print("  wit status     — 查询服务运行状态")
    print("  wit atr auto   — 实体可信注册")
    print("  wit provider   — 管理 LLM 模型配置")
    print("  wit config     — 管理全局配置")
    print("  wit logs       — 查看后台运行日志")
    print("  wit bugreport  — 一键故障诊断打包")
    print()


if __name__ == "__main__":
    main()

