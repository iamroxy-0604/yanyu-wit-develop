"""`wit bugreport` — 一键诊断打包 CLI 子命令。

自动收集运行日志和脱敏后的系统环境配置，打包为压缩包供开发者分析故障。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `bugreport` 子命令。"""
    parser = subparsers.add_parser(
        "bugreport",
        help="一键诊断打包（收集日志和脱敏配置）",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="输出 ZIP 文件路径（默认: 当前目录下的 wit-bugreport-<时间戳>.zip）",
    )
    parser.set_defaults(func=execute)


def _collect_system_info() -> dict:
    """收集脱敏后的系统环境信息。"""
    info = {
        "timestamp": datetime.now().isoformat(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "is_frozen": getattr(sys, "frozen", False),
        },
    }

    # 检查关键依赖版本
    deps = {}
    for pkg_name in [
        "fastapi", "uvicorn", "langchain", "langchain_core", "langchain_openai",
        "langgraph", "httpx", "tomlkit", "cryptography", "pydantic",
    ]:
        try:
            mod = __import__(pkg_name)
            deps[pkg_name] = getattr(mod, "__version__", "installed (version unknown)")
        except ImportError:
            deps[pkg_name] = "NOT INSTALLED"
    info["dependencies"] = deps

    # wit 版本
    try:
        from cli import __version__
        info["wit_version"] = __version__
    except Exception:
        info["wit_version"] = "unknown"

    # 磁盘空间
    try:
        import shutil
        usage = shutil.disk_usage(Path.home())
        info["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_percent": round((usage.used / usage.total) * 100, 1),
        }
    except Exception:
        pass

    return info


def _collect_sanitized_config() -> dict:
    """收集脱敏后的配置信息。"""
    config_data = {}

    # 全局配置
    try:
        from cli.config import YANYU_WIT_HOME
        global_config_path = YANYU_WIT_HOME / "config.toml"
        if global_config_path.exists():
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib
            with open(global_config_path, "rb") as f:
                config_data["global_config"] = tomllib.load(f)
    except Exception as e:
        config_data["global_config_error"] = str(e)

    # 账户配置
    try:
        from cli.config import load_config, get_active_account
        active = get_active_account()
        config_data["active_account"] = active or "none"
        if active:
            cfg = load_config()
            config_data["account_config"] = cfg
    except Exception as e:
        config_data["account_config_error"] = str(e)

    # 脱敏处理
    _sanitize_dict(config_data)

    return config_data


def _sanitize_dict(data: dict) -> None:
    """递归脱敏字典中的敏感字段。"""
    sensitive_keys = {
        "api_key", "secret", "password", "token", "access_token",
        "refresh_token", "id_token", "client_secret", "credentials",
        "entity_key", "entity_cert", "private_key",
    }

    for key in list(data.keys()):
        value = data[key]
        key_lower = key.lower()

        if any(sk in key_lower for sk in sensitive_keys):
            if isinstance(value, str) and len(value) > 4:
                data[key] = f"[REDACTED...{value[-4:]}]"
            elif isinstance(value, str):
                data[key] = "[REDACTED]"
        elif isinstance(value, dict):
            _sanitize_dict(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _sanitize_dict(item)


def _collect_sandbox_info() -> dict:
    """收集沙箱状态信息。"""
    try:
        from agent.shell.sandbox_manager import get_sandbox_stats, list_sandboxes
        stats = get_sandbox_stats()
        sandboxes = list_sandboxes()
        # 只保留名称和年龄
        summary = [{"name": s["name"], "age_hours": s["age_hours"], "size_mb": s["size_mb"]} for s in sandboxes]
        return {"stats": stats, "sandboxes": summary}
    except Exception as e:
        return {"error": str(e)}


def execute(args: argparse.Namespace) -> None:
    """执行 bugreport 命令。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"wit-bugreport-{timestamp}.zip"
    output_path = Path(output_path).resolve()

    print("🔍 Yanyu-Wit 故障诊断打包工具")
    print("=" * 50)
    print()
    print("正在收集诊断信息...")

    items_collected = []

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. 系统信息
        print("  📋 收集系统环境信息...")
        system_info = _collect_system_info()
        zf.writestr(
            "system_info.json",
            json.dumps(system_info, indent=2, ensure_ascii=False),
        )
        items_collected.append("系统环境信息")

        # 2. 脱敏配置
        print("  📋 收集脱敏配置...")
        config_data = _collect_sanitized_config()
        zf.writestr(
            "config_sanitized.json",
            json.dumps(config_data, indent=2, ensure_ascii=False, default=str),
        )
        items_collected.append("脱敏配置信息")

        # 3. 运行日志
        print("  📝 收集运行日志...")
        try:
            from cli.utils.logging_config import get_log_dir, LOG_FILE_NAME
            log_dir = get_log_dir()
            if log_dir.exists():
                for log_file in log_dir.iterdir():
                    if log_file.is_file() and log_file.name.startswith(LOG_FILE_NAME.split(".")[0]):
                        # 限制单个日志文件最大 5MB
                        if log_file.stat().st_size <= 5 * 1024 * 1024:
                            zf.write(log_file, f"logs/{log_file.name}")
                        else:
                            # 只取最后 5MB
                            with open(log_file, "rb") as f:
                                f.seek(-5 * 1024 * 1024, 2)
                                content = f.read()
                            zf.writestr(f"logs/{log_file.name}.tail", content.decode("utf-8", errors="replace"))
                        items_collected.append(f"日志: {log_file.name}")
            else:
                zf.writestr("logs/NO_LOGS.txt", "日志目录不存在\n")
        except Exception as e:
            zf.writestr("logs/ERROR.txt", f"收集日志失败: {e}\n")

        # 4. 沙箱状态
        print("  📦 收集沙箱状态...")
        sandbox_info = _collect_sandbox_info()
        zf.writestr(
            "sandbox_info.json",
            json.dumps(sandbox_info, indent=2, ensure_ascii=False),
        )
        items_collected.append("沙箱状态信息")

        # 5. pip list (已安装包列表)
        print("  📦 收集已安装包列表...")
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                zf.writestr("pip_packages.json", result.stdout)
                items_collected.append("Python 包列表")
        except Exception:
            pass

    print()
    print("=" * 50)
    print(f"✅ 诊断报告已生成: {output_path}")
    print(f"   包含 {len(items_collected)} 项内容:")
    for item in items_collected:
        print(f"     • {item}")
    print()
    print(f"   文件大小: {output_path.stat().st_size / 1024:.1f} KB")
    print()
    print("💡 请将此文件发送给开发者用以分析故障。")
    print("⚠️ 文件中的敏感信息（API Key、密钥等）已自动脱敏。")
