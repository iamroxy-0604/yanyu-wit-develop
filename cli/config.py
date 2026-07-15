"""Yanyu-Wit CLI 的全局配置管理。

处理 ~/.yanyu-wit/ 目录结构、config.toml 的读取/写入，并提供用于模型 kwargs 的辅助函数。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Python 3.11+ has tomllib in stdlib
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

try:
    import tomlkit
except ImportError:
    tomlkit = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dynamic Paths & Multi-Account Management
# ---------------------------------------------------------------------------

YANYU_WIT_HOME = Path.home() / ".yanyu-wit"

def get_active_account() -> str | None:
    """从全局 config.toml 读取当前活动账户，未配置时返回 None。"""
    global_config_path = YANYU_WIT_HOME / "config.toml"
    if global_config_path.exists():
        try:
            with open(global_config_path, "rb") as f:
                cfg = tomllib.load(f)
                return cfg.get("global", {}).get("active_account") or None
        except Exception:
            pass
    return None


def set_active_account(username: str) -> None:
    """在全局 config.toml 中设置活动账户。"""
    YANYU_WIT_HOME.mkdir(parents=True, exist_ok=True)
    global_config_path = YANYU_WIT_HOME / "config.toml"
    cfg = {}
    if global_config_path.exists():
        try:
            with open(global_config_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            pass
    if "global" not in cfg:
        cfg["global"] = {}
    cfg["global"]["active_account"] = username
    
    # Save using simple toml write
    lines = ["[global]", f'active_account = "{username}"']
    global_config_path.write_text("\n".join(lines) + "\n")
    logger.info("Switched active account to: %s", username)


def get_account_dir(username: str | None = None) -> Path:
    user = username or get_active_account()
    if not user:
        raise ValueError("未配置活动账户，请先运行 `wit init` 完成登录。")
    return YANYU_WIT_HOME / "accounts" / user


def get_account_config_path(username: str | None = None) -> Path:
    return get_account_dir(username) / "config.toml"


def get_account_workspace_dir(username: str | None = None) -> Path:
    return get_account_dir(username) / "workspace"


def __getattr__(name: str) -> Any:
    if name == "CONFIG_PATH":
        return get_account_config_path()
    elif name == "WORKSPACE_DIR":
        return get_account_workspace_dir()
    elif name == "CREDENTIALS_DIR":
        return get_account_dir() / "credentials"
    elif name == "CERTS_DIR":
        return get_account_dir() / "certs"
    elif name == "MEMORY_DIR":
        return get_account_workspace_dir() / "memory"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Default config template generator
# ---------------------------------------------------------------------------

def get_default_config_toml(username: str | None = None, provider_type: str = "openai", model_name: str = "", base_url: str = "", api_key: str = "") -> str:
    account_dir = get_account_dir(username)
    return f"""# Yanyu-Wit 全局配置
# 由 `yanyu-wit init` 生成，可手动编辑

# ─── 当前激活的 Provider 索引（0-based）───
active_provider = 0

# ─── LLM Provider 列表 ───
[[providers]]
type = "{provider_type}"
name = "{model_name}"
base_url = "{base_url}"
api_key = "{api_key}"

# ─── 身份标识（可信注册后自动填充）───
[identity]
user_id = "{username}"
agent_aic = ""

# ─── 外部服务地址 ───
# flux.base_url: Flux 服务根地址，登录后从 init 获取，`atr auto` 时可覆盖
[services.flux]
base_url = "http://127.0.0.1:13002"

# ca.base_url: 由 `yanyu-wit atr auto` 从 Flux 响应自动写入，无需手动填写
[services.ca]
base_url = ""

# ─── 证书路径（由 `yanyu-wit atr auto` 自动填充）───
[certs]
dir = "{account_dir}/certs"
entity_cert = ""
entity_key = ""
"""


def ensure_home_dir(username: str | None = None) -> None:
    """如果 ~/.yanyu-wit/ 及其子目录不存在，则创建它们。"""
    YANYU_WIT_HOME.mkdir(parents=True, exist_ok=True)
    
    account_dir = get_account_dir(username)
    workspace = get_account_workspace_dir(username)
    
    subdirs = (
        account_dir,
        account_dir / "credentials",
        account_dir / "certs",
        workspace,
        workspace / "attachments",
        workspace / "memory",
        workspace / "heartbeat",
        workspace / "heartbeat" / "runs",
    )
    for d in subdirs:
        d.mkdir(parents=True, exist_ok=True)
    logger.debug("Ensured home and account directory at %s", account_dir)


def load_config(username: str | None = None) -> dict[str, Any]:
    """加载并以字典形式返回全局 config.toml。"""
    config_path = get_account_config_path(username)
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except Exception as exc:
        logger.warning("Failed to load config.toml: %s", exc)
        return {}


def save_config(data: dict[str, Any], username: str | None = None) -> None:
    """将配置数据写入 config.toml，如果可能的话保留注释。"""
    ensure_home_dir(username)
    config_path = get_account_config_path(username)
    if tomlkit is not None:
        if config_path.exists():
            try:
                doc = tomlkit.parse(config_path.read_text())
                _deep_update_tomlkit(doc, data)
                config_path.write_text(tomlkit.dumps(doc))
                return
            except Exception:
                pass
        config_path.write_text(tomlkit.dumps(data))
    else:
        # Fallback to simple TOML writer
        lines = []
        _write_toml_section(lines, data, [])
        config_path.write_text("\n".join(lines) + "\n")


def _deep_update_tomlkit(doc: Any, updates: dict) -> None:
    """递归地使用新值更新 tomlkit 文档。"""
    for key, value in updates.items():
        if isinstance(value, dict) and key in doc and isinstance(doc[key], dict):
            _deep_update_tomlkit(doc[key], value)
        else:
            doc[key] = value


def _write_toml_section(lines: list[str], data: dict, path: list[str]) -> None:
    """简单的递归 TOML 写入器。"""
    for key, value in data.items():
        if isinstance(value, dict):
            section = path + [key]
            lines.append(f"\n[{'.'.join(section)}]")
            _write_toml_section(lines, value, section)
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")


def write_default_config() -> None:
    """如果默认 config.toml 不存在，则写入它。"""
    ensure_home_dir()
    config_path = get_account_config_path()
    if not config_path.exists():
        config_path.write_text(get_default_config_toml())
        logger.info("Created default config at %s", config_path)


def get_model_kwargs() -> dict[str, Any]:
    """从 config.toml 返回适用于 ChatOpenAI() 的 kwargs（兼容旧调用方）。"""
    providers = list_providers()
    active_idx = get_active_provider_index()
    if providers and 0 <= active_idx < len(providers):
        p = providers[active_idx]
        return {
            "base_url": p.get("base_url", ""),
            "model": p.get("name", ""),
            "api_key": p.get("api_key", ""),
            "streaming": True,
        }
    return {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3.5:35b",
        "api_key": "sk-local",
        "streaming": True,
    }


def get_identity(username: str | None = None) -> dict[str, str]:
    """从配置中返回 [identity] 部分。"""
    return load_config(username).get("identity", {})


def get_services(username: str | None = None) -> dict[str, Any]:
    """从配置中返回 [services] 部分。"""
    return load_config(username).get("services", {})


def config_set(key: str, value: Any, username: str | None = None) -> None: 
    """在 config.toml 中设置以点分隔的键。例如：'model.name' = 'gpt-4o'。"""
    if value is None:
        value = ""
    elif not isinstance(value, str):
        value = str(value)
    cfg = load_config(username)
    parts = key.split(".")
    target = cfg
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]
    if value.lower() in ("true", "false"):
        target[parts[-1]] = value.lower() == "true"
    else:
        try:
            target[parts[-1]] = int(value)
        except ValueError:
            try:
                target[parts[-1]] = float(value)
            except ValueError:
                target[parts[-1]] = value
    save_config(cfg, username)


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------

def list_providers() -> list[dict[str, Any]]:
    """读取 config.toml 中所有 [[providers]] 配置。"""
    cfg = load_config()
    providers = cfg.get("providers", [])
    if isinstance(providers, list):
        return providers
    return []


def get_active_provider_index() -> int:
    """获取当前激活的 provider 索引（0-based）。"""
    cfg = load_config()
    idx = cfg.get("active_provider", 0)
    return int(idx) if isinstance(idx, (int, float)) else 0


def set_active_provider(index: int) -> None:
    """设置激活的 provider 索引。"""
    cfg = load_config()
    providers = cfg.get("providers", [])
    if not isinstance(providers, list) or index < 0 or index >= len(providers):
        raise ValueError(f"无效的 provider 索引: {index}，当前共有 {len(providers)} 个 provider")
    cfg["active_provider"] = index
    save_config(cfg)
    logger.info("Switched active provider to index %d", index)


def add_provider(provider: dict[str, Any]) -> int:
    """新增一个 provider 配置，返回其索引。"""
    required_keys = ("type", "name")
    for key in required_keys:
        if not provider.get(key):
            raise ValueError(f"Provider 缺少必填字段: {key}")
    cfg = load_config()
    if "providers" not in cfg or not isinstance(cfg["providers"], list):
        cfg["providers"] = []
    entry = {
        "type": provider["type"].strip().lower(),
        "name": provider["name"].strip(),
        "base_url": provider.get("base_url", "").strip(),
        "api_key": provider.get("api_key", "").strip(),
    }
    cfg["providers"].append(entry)
    # If this is the first provider, auto-activate it
    if len(cfg["providers"]) == 1:
        cfg["active_provider"] = 0
    save_config(cfg)
    idx = len(cfg["providers"]) - 1
    logger.info("Added provider at index %d: type=%s, name=%s", idx, entry["type"], entry["name"])
    return idx


def update_provider(index: int, patch: dict[str, Any]) -> dict[str, Any]:
    """更新指定索引的 provider 配置。"""
    cfg = load_config()
    providers = cfg.get("providers", [])
    if not isinstance(providers, list) or index < 0 or index >= len(providers):
        raise ValueError(f"无效的 provider 索引: {index}")
    for key in ("type", "name", "base_url", "api_key"):
        if key in patch:
            providers[index][key] = str(patch[key]).strip()
    cfg["providers"] = providers
    save_config(cfg)
    logger.info("Updated provider at index %d", index)
    return providers[index]


def remove_provider(index: int) -> bool:
    """删除指定索引的 provider 配置。"""
    cfg = load_config()
    providers = cfg.get("providers", [])
    if not isinstance(providers, list) or index < 0 or index >= len(providers):
        return False
    removed = providers.pop(index)
    cfg["providers"] = providers
    # Adjust active_provider index
    active = cfg.get("active_provider", 0)
    if isinstance(active, (int, float)):
        active = int(active)
        if active == index:
            cfg["active_provider"] = 0 if providers else 0
        elif active > index:
            cfg["active_provider"] = active - 1
    save_config(cfg)
    logger.info("Removed provider at index %d: type=%s, name=%s", index, removed.get("type"), removed.get("name"))
    return True


def mask_api_key(api_key: str) -> str:
    """脱敏 API Key，只显示后 4 位。"""
    if not api_key or len(api_key) <= 4:
        return api_key
    return "*" * (len(api_key) - 4) + api_key[-4:]
