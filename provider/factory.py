"""基于多服务商抽象的统一模型创建。

从 ``config.toml`` 读取模型配置，
根据 ``provider`` 字段创建相应的 ``BaseChatModel``，
并按角色（role）缓存实例以避免重复构建。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_OLLAMA_DEFAULT_API_KEY = "ollama"  # Ollama ignores this but the SDK requires it
_OLLAMA_DEFAULT_MODEL = "qwen3:32b"

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": _OLLAMA_DEFAULT_BASE_URL,
        "api_key": _OLLAMA_DEFAULT_API_KEY,
        "name": _OLLAMA_DEFAULT_MODEL,
    },
}


# ---------------------------------------------------------------------------
# Provider → ChatModel builder
# ---------------------------------------------------------------------------

def _build_openai(cfg: dict[str, Any], *, streaming: bool) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=cfg.get("base_url") or None,
        model=cfg["name"],
        api_key=cfg["api_key"],
        streaming=streaming,
    )


def _build_anthropic(cfg: dict[str, Any], *, streaming: bool) -> BaseChatModel:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ImportError(
            "服务商 'anthropic' 需要安装 langchain-anthropic 包。 "
            "请使用以下命令安装: pip install langchain-anthropic"
        ) from exc

    return ChatAnthropic(
        model=cfg["name"],
        api_key=cfg["api_key"],
        streaming=streaming,
    )


def _build_google(cfg: dict[str, Any], *, streaming: bool) -> BaseChatModel:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise ImportError(
            "服务商 'google' 需要安装 langchain-google-genai 包。 "
            "请使用以下命令安装: pip install langchain-google-genai"
        ) from exc

    return ChatGoogleGenerativeAI(
        model=cfg["name"],
        google_api_key=cfg["api_key"],
        streaming=streaming,
    )


def _build_ollama(cfg: dict[str, Any], *, streaming: bool) -> BaseChatModel:
    """Ollama 暴露了 OpenAI 兼容的 API，因此我们复用 ChatOpenAI。"""
    from langchain_openai import ChatOpenAI

    defaults = _PROVIDER_DEFAULTS["ollama"]
    return ChatOpenAI(
        base_url=cfg.get("base_url") or defaults["base_url"],
        model=cfg.get("name") or defaults["name"],
        api_key=cfg.get("api_key") or defaults["api_key"],
        streaming=streaming,
    )


_BUILDERS: dict[str, Any] = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "ollama": _build_ollama,
}


# ---------------------------------------------------------------------------
# ModelFactory
# ---------------------------------------------------------------------------

class ModelFactory:
    """基于服务商抽象的统一模型创建。

    读取一次 config 中的 ``[[providers]]`` 列表，然后构建并缓存
    以 ``(role, streaming)`` 为键的 ``BaseChatModel`` 实例。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            try:
                from cli.config import load_config
                config = load_config()
            except (ImportError, ValueError):
                config = {}

        self._config: dict[str, Any] = config
        self._instances: dict[str, BaseChatModel] = {}

    @property
    def provider(self) -> str:
        """返回当前激活的服务商名称（小写）。"""
        try:
            cfg = self._resolve_config("main")
            return cfg.get("provider", "")
        except ValueError:
            return ""

    def get_model(
        self,
        role: str = "main",
        *,
        streaming: bool = True,
    ) -> BaseChatModel:
        """通过角色获取或创建模型实例。"""
        cache_key = f"{role}:{streaming}"
        if cache_key in self._instances:
            return self._instances[cache_key]

        cfg = self._resolve_config(role)
        provider = cfg["provider"]

        builder = _BUILDERS.get(provider)
        if builder is None:
            raise ValueError(
                f"不支持的模型服务商: '{provider}'。 "
                f"支持的服务商有: {', '.join(sorted(_BUILDERS))}。"
            )

        instance = builder(cfg, streaming=streaming)
        self._instances[cache_key] = instance

        logger.info(
            "Created %s model (role=%s, provider=%s, model=%s, streaming=%s)",
            type(instance).__name__, role, provider, cfg["name"], streaming,
        )
        return instance

    def switch_provider(self, index: int) -> None:
        """切换并清除所有缓存的模型实例。"""
        try:
            from cli.config import set_active_provider, load_config
            set_active_provider(index)
            self._config = load_config()
        except ImportError:
            pass
        self._instances.clear()
        logger.info("Switched to provider index %d, cache cleared", index)

    def _resolve_config(self, role: str) -> dict[str, Any]:
        """解析给定角色的模型配置。"""
        providers = self._config.get("providers", [])
        active_idx = self._config.get("active_provider", 0)

        if not isinstance(providers, list) or len(providers) == 0:
            raise ValueError(
                "未配置任何 LLM Provider。"
                "请运行 `wit init` 或 `wit provider add` 添加 provider。"
            )

        if not isinstance(active_idx, int):
            active_idx = 0
        if active_idx < 0 or active_idx >= len(providers):
            active_idx = 0

        cfg = providers[active_idx]
        provider = (cfg.get("type") or "").strip().lower()

        if not provider:
            raise ValueError(
                "当前激活的 provider 未指定 type。"
                "请在您的 config.toml 中设置 [[providers]].type"
                "（例如 'openai'、'anthropic'、'google'、'ollama'）。"
            )

        # Get defaults for the provider (if any)
        defaults = _PROVIDER_DEFAULTS.get(provider, {})

        name = (cfg.get("name") or "").strip() or defaults.get("name", "")
        base_url = (cfg.get("base_url") or "").strip() or defaults.get("base_url", "")
        api_key = (cfg.get("api_key") or "").strip() or defaults.get("api_key", "")

        if not name and provider != "ollama":
            raise ValueError(
                f"未为服务商 '{provider}' 配置模型名称。"
                "请在您的 config.toml 中设置 [[providers]].name。"
            )

        if not api_key and provider not in ("ollama",):
            raise ValueError(
                f"未为服务商 '{provider}' 配置 API 密钥。"
                "请在您的 config.toml 中设置 [[providers]].api_key。"
            )

        return {
            "provider": provider,
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
        }


# ---------------------------------------------------------------------------
# Module-level convenience (singleton)
# ---------------------------------------------------------------------------

_default_factory: ModelFactory | None = None


def get_model_factory() -> ModelFactory:
    """返回模块级的 ModelFactory 单例。"""
    global _default_factory
    if _default_factory is None:
        _default_factory = ModelFactory()
    return _default_factory


def reset_model_factory() -> None:
    """重置单例。"""
    global _default_factory
    _default_factory = None
