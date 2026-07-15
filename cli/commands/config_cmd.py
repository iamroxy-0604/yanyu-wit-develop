"""`wit config [set/get/list]` — 全局配置管理 CLI 子命令。

支持用户通过命令行直接查看或修改全局配置文件中的关键参数。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `config` 子命令。"""
    parser = subparsers.add_parser(
        "config",
        help="管理全局配置",
    )
    config_sub = parser.add_subparsers(dest="config_action", help="配置操作")

    # wit config list
    list_parser = config_sub.add_parser("list", help="列出所有配置项")
    list_parser.set_defaults(func=execute_list)

    # wit config get <key>
    get_parser = config_sub.add_parser("get", help="获取指定配置项")
    get_parser.add_argument("key", help="配置项键名（支持点分路径，如 server.port）")
    get_parser.set_defaults(func=execute_get)

    # wit config set <key> <value>
    set_parser = config_sub.add_parser("set", help="设置指定配置项")
    set_parser.add_argument("key", help="配置项键名（支持点分路径，如 server.port）")
    set_parser.add_argument("value", help="配置项值")
    set_parser.set_defaults(func=execute_set)

    # 无子命令时显示帮助
    parser.set_defaults(func=lambda args: parser.print_help())


def _format_value(value, indent: int = 0) -> str:
    """递归格式化配置值为可读字符串。"""
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            formatted = _format_value(v, indent + 1)
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}  {k}:")
                lines.append(formatted)
            else:
                lines.append(f"{prefix}  {k} = {formatted}")
        return "\n".join(lines)
    elif isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for i, item in enumerate(value):
            if isinstance(item, dict):
                lines.append(f"{prefix}  [{i}]:")
                lines.append(_format_value(item, indent + 2))
            else:
                lines.append(f"{prefix}  [{i}] = {item}")
        return "\n".join(lines)
    elif isinstance(value, bool):
        return "true" if value else "false"
    else:
        return str(value)


def _get_nested_value(data: dict, key: str):
    """从嵌套字典中通过点分路径获取值。"""
    parts = key.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def execute_list(args: argparse.Namespace) -> None:
    """列出所有配置项。"""
    from cli.config import load_config, get_account_config_path, YANYU_WIT_HOME

    print("📋 全局配置")
    print("=" * 50)

    # 1. 显示全局 config.toml（server.port, active_account）
    global_config_path = YANYU_WIT_HOME / "config.toml"
    if global_config_path.exists():
        print(f"\n📂 全局配置 ({global_config_path}):")
        try:
            import sys as _sys
            if _sys.version_info >= (3, 11):
                import tomllib
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib

            with open(global_config_path, "rb") as f:
                global_cfg = tomllib.load(f)
            print(_format_value(global_cfg, indent=1))
        except Exception as e:
            print(f"  ⚠️ 读取失败: {e}")
    else:
        print(f"\n📂 全局配置: 不存在 ({global_config_path})")

    # 2. 显示账户级 config.toml
    try:
        config_path = get_account_config_path()
        if config_path.exists():
            print(f"\n📂 账户配置 ({config_path}):")
            cfg = load_config()
            # 脱敏 API Key
            if "providers" in cfg and isinstance(cfg["providers"], list):
                for p in cfg["providers"]:
                    if "api_key" in p and p["api_key"]:
                        key = p["api_key"]
                        p["api_key"] = ("*" * (len(key) - 4) + key[-4:]) if len(key) > 4 else key
            print(_format_value(cfg, indent=1))
        else:
            print(f"\n📂 账户配置: 不存在 ({config_path})")
    except Exception as e:
        print(f"\n  ⚠️ 无法读取账户配置: {e}")

    print()
    print("=" * 50)
    print("💡 使用 `wit config get <key>` 查看特定配置")
    print("💡 使用 `wit config set <key> <value>` 修改配置")


def execute_get(args: argparse.Namespace) -> None:
    """获取指定配置项的值。"""
    key = args.key
    from cli.config import load_config, YANYU_WIT_HOME

    # 优先从全局配置中查找
    global_config_path = YANYU_WIT_HOME / "config.toml"
    found = False

    if global_config_path.exists():
        try:
            import sys as _sys
            if _sys.version_info >= (3, 11):
                import tomllib
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib

            with open(global_config_path, "rb") as f:
                global_cfg = tomllib.load(f)
            value = _get_nested_value(global_cfg, key)
            if value is not None:
                print(f"{key} = {_format_value(value)}")
                print(f"  (来源: 全局配置)")
                found = True
        except Exception:
            pass

    # 再从账户配置中查找
    try:
        cfg = load_config()
        value = _get_nested_value(cfg, key)
        if value is not None:
            if found:
                print()
            # 脱敏 API Key 字段
            if "api_key" in key and isinstance(value, str) and len(value) > 4:
                value = "*" * (len(value) - 4) + value[-4:]
            print(f"{key} = {_format_value(value)}")
            print(f"  (来源: 账户配置)")
            found = True
    except Exception:
        pass

    if not found:
        print(f"⚠️ 未找到配置项: {key}")
        sys.exit(1)


def execute_set(args: argparse.Namespace) -> None:
    """设置指定配置项的值。"""
    key = args.key
    value = args.value

    # 判断是全局配置还是账户配置
    global_keys = {"server.port", "global.active_account"}
    is_global = any(key.startswith(gk.split(".")[0]) for gk in global_keys)

    if is_global:
        _set_global_config(key, value)
    else:
        _set_account_config(key, value)


def _set_global_config(key: str, value: str) -> None:
    """设置全局配置项。"""
    from cli.config import YANYU_WIT_HOME
    import sys as _sys
    if _sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

    YANYU_WIT_HOME.mkdir(parents=True, exist_ok=True)
    global_config_path = YANYU_WIT_HOME / "config.toml"

    cfg = {}
    if global_config_path.exists():
        try:
            with open(global_config_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            pass

    # 使用点分路径设置值
    parts = key.split(".")
    target = cfg
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]

    # 类型推断
    target[parts[-1]] = _infer_value_type(value)

    # 使用 tomlkit 保存（保留注释）
    try:
        import tomlkit
        if global_config_path.exists():
            doc = tomlkit.parse(global_config_path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()

        # 递归设置
        current = doc
        for part in parts[:-1]:
            if part not in current:
                current.add(part, tomlkit.table())
            current = current[part]
        current[parts[-1]] = _infer_value_type(value)
        global_config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    except ImportError:
        # Fallback
        lines = []
        for k, v in cfg.items():
            if isinstance(v, dict):
                lines.append(f"\n[{k}]")
                for sk, sv in v.items():
                    lines.append(f'{sk} = {_toml_value(sv)}')
            else:
                lines.append(f"{k} = {_toml_value(v)}")
        global_config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"✅ 已设置全局配置: {key} = {value}")


def _set_account_config(key: str, value: str) -> None:
    """设置账户配置项。"""
    from cli.config import config_set
    config_set(key, value)
    print(f"✅ 已设置账户配置: {key} = {value}")


def _infer_value_type(value: str):
    """推断字符串值的实际类型。"""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _toml_value(v) -> str:
    """将值格式化为 TOML 格式字符串。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, str):
        return f'"{v}"'
    else:
        return str(v)
