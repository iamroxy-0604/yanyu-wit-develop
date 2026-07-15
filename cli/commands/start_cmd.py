"""`wit start` — 启动后端 FastAPI 服务的 CLI 子命令。

以子进程形式拉起 FastAPI 服务，并自动在系统浏览器中打开对应的页面。
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import webbrowser
import time
from pathlib import Path

from cli.utils.port import resolve_port, DEFAULT_PORT

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `start` 子命令。"""
    parser = subparsers.add_parser(
        "start",
        help="启动 Yanyu-Wit 后端服务",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=None,
        help=f"指定服务监听端口（默认: {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="指定网卡绑定地址（默认: 0.0.0.0）",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="禁止自动打开浏览器",
    )
    parser.set_defaults(func=execute)


def execute(args: argparse.Namespace) -> None:
    """启动后端 FastAPI 服务。"""
    port = resolve_port(args.port)
    host = args.host
    no_open = args.no_open

    print("🚀 Yanyu-Wit 服务启动中...")
    print(f"   端口: {port}")
    print(f"   绑定: {host}")
    print()

    # 设置端口环境变量供 service.app 读取
    os.environ["WIT_PORT_OVERRIDE"] = str(port)

    # 确定项目根目录
    # 优先使用 pyproject.toml 所在的目录
    project_root = Path(__file__).parent.parent.parent
    service_module = "service.app"

    # 自动打开浏览器（延迟 1.5 秒等待服务启动）
    if not no_open:
        import threading

        def _open_browser():
            time.sleep(2.0)
            url = f"http://localhost:{port}"
            print(f"   🌐 正在打开浏览器: {url}")
            webbrowser.open(url)

        threading.Thread(target=_open_browser, daemon=True).start()

    # 如果是打包后的二进制环境，sys.executable 指向二进制程序自身而非 python 解释器
    # 因此无法使用 sys.executable -c 启动子进程，需要直接在当前进程中内联启动 uvicorn
    if getattr(sys, "frozen", False):
        try:
            import uvicorn
            from cli.utils.logging_config import setup_service_logging
            setup_service_logging(debug=False)
            
            # 动态导入以支持 pyarmor 混淆和 pyinstaller 动态装载
            from service.app import app
            
            uvicorn.run(
                app,
                host=host,
                port=port,
                reload=False,
                log_level="info",
            )
        except KeyboardInterrupt:
            print("\n👋 服务已停止。")
        except Exception as e:
            print(f"❌ 启动服务失败: {e}")
            sys.exit(1)
        return

    # 以子进程方式拉起 uvicorn (开发态支持 reload/独立生命周期进程)
    try:
        cmd = [
            sys.executable, "-c",
            f"""
import os
os.environ["WIT_PORT_OVERRIDE"] = "{port}"
import uvicorn
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path("{project_root}") / ".env")

# 使用统一持久化日志系统
from cli.utils.logging_config import setup_service_logging
setup_service_logging(debug=False)

uvicorn.run(
    "{service_module}:app",
    host="{host}",
    port={port},
    reload=False,
    log_level="info",
)
"""
        ]
        subprocess.run(cmd, cwd=str(project_root))
    except KeyboardInterrupt:
        print("\n👋 服务已停止。")
    except Exception as e:
        print(f"❌ 启动服务失败: {e}")
        sys.exit(1)

