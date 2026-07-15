"""端口解析模块。

实现多通道端口解析机制：
  1. 命令行参数 -p / --port
  2. ~/.yanyu-wit/config.toml 中的 [server].port
  3. 默认保底端口 7020
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7020


def resolve_port(cli_port: Optional[int] = None) -> int:
    """解析服务端口号。

    优先级：
      1. 命令行传入的端口
      2. ~/.yanyu-wit/config.toml 中的 [server].port
      3. 默认端口 7020

    Args:
        cli_port: 从命令行 -p/--port 传入的端口号，None 表示未指定。

    Returns:
        最终确定的端口号
    """
    # 1. 命令行参数优先
    if cli_port is not None:
        logger.info("Using CLI-specified port: %d", cli_port)
        return cli_port

    # 2. 读取全局配置文件
    try:
        from cli.config import YANYU_WIT_HOME
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]

        global_config_path = YANYU_WIT_HOME / "config.toml"
        if global_config_path.exists():
            with open(global_config_path, "rb") as f:
                cfg = tomllib.load(f)
                port = cfg.get("server", {}).get("port")
                if port is not None:
                    port = int(port)
                    logger.info("Using port from global config.toml: %d", port)
                    return port
    except Exception as exc:
        logger.warning("Failed to read port from global config: %s", exc)

    # 3. 默认端口
    logger.info("Using default port: %d", DEFAULT_PORT)
    return DEFAULT_PORT


def save_port_to_global_config(port: int) -> None:
    """将端口号写入全局配置文件 ~/.yanyu-wit/config.toml 的 [server] 段。"""
    try:
        from cli.config import YANYU_WIT_HOME
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]

        YANYU_WIT_HOME.mkdir(parents=True, exist_ok=True)
        global_config_path = YANYU_WIT_HOME / "config.toml"

        cfg = {}
        existing_lines = []
        if global_config_path.exists():
            try:
                with open(global_config_path, "rb") as f:
                    cfg = tomllib.load(f)
                existing_lines = global_config_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                pass

        # 尝试使用 tomlkit 保留注释
        try:
            import tomlkit
            if global_config_path.exists():
                doc = tomlkit.parse(global_config_path.read_text(encoding="utf-8"))
            else:
                doc = tomlkit.document()

            if "server" not in doc:
                doc.add("server", tomlkit.table())
            doc["server"]["port"] = port
            global_config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        except ImportError:
            # Fallback: 简单追加或替换
            if "server" not in cfg:
                cfg["server"] = {}
            cfg["server"]["port"] = port

            # 简单 TOML 写入
            lines = []
            for key, value in cfg.items():
                if isinstance(value, dict):
                    lines.append(f"\n[{key}]")
                    for k, v in value.items():
                        if isinstance(v, str):
                            lines.append(f'{k} = "{v}"')
                        else:
                            lines.append(f"{k} = {v}")
                elif isinstance(value, str):
                    lines.append(f'{key} = "{value}"')
                else:
                    lines.append(f"{key} = {value}")
            global_config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        logger.info("Saved port %d to global config", port)
    except Exception as exc:
        logger.warning("Failed to save port to global config: %s", exc)
