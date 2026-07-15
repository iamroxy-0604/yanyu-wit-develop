"""
Feature Flags 功能开关引擎
===========================
将原先 `is_saas_mode()` 的粗粒度二元判断拆解为独立可组合的功能开关。

系统通过 ``WIT_DEPLOY_MODE`` 环境变量选择一个预设 Profile（pc / saas / enterprise），
也可通过 ``WIT_FEATURE_*`` 环境变量覆盖单个开关实现灵活组合。

使用示例::

    from service.feature_flags import get_flags

    if get_flags().storage_engine == "postgresql":
        ...
    if get_flags().sandbox_type == "docker":
        ...
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, fields

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature Flags 数据定义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureFlags:
    """独立的功能开关集合，每个维度独立可配。

    Attributes:
        storage_engine: 数据存储引擎 — ``"sqlite"`` | ``"postgresql"``
        sandbox_type: 沙箱隔离方案 — ``"local"`` | ``"docker"``
        auth_mode: 认证方式 — ``"oidc_pkce"`` | ``"oidc_confidential"`` | ``"none"``
        heartbeat_mode: 任务调度策略 — ``"local"`` | ``"multi_tenant"`` | ``"disabled"``
        sse_masking: SSE 数据流脱敏（隐藏物理路径、敏感 key 等）
        distributed_locking: 是否启用 Redis 分布式会话锁
        tool_call_limit: 单会话工具调用次数上限（0 表示不限制）
        cert_renewal_daemon: 是否启动实体证书自动续签守护
        json_logging: 是否使用结构化 JSON 日志格式
    """
    storage_engine: str = "sqlite"
    sandbox_type: str = "local"
    auth_mode: str = "oidc_pkce"
    heartbeat_mode: str = "local"
    sse_masking: bool = False
    distributed_locking: bool = False
    tool_call_limit: int = 0
    cert_renewal_daemon: bool = False
    json_logging: bool = False


# ---------------------------------------------------------------------------
# 预设 Profiles
# ---------------------------------------------------------------------------

PROFILES: dict[str, FeatureFlags] = {
    "pc": FeatureFlags(
        storage_engine="sqlite",
        sandbox_type="local",
        auth_mode="oidc_pkce",
        heartbeat_mode="local",
        sse_masking=False,
        distributed_locking=False,
        tool_call_limit=0,
        cert_renewal_daemon=False,
        json_logging=False,
    ),
    "saas": FeatureFlags(
        storage_engine="postgresql",
        sandbox_type="docker",
        auth_mode="oidc_confidential",
        heartbeat_mode="disabled",
        sse_masking=True,
        distributed_locking=True,
        tool_call_limit=100,
        cert_renewal_daemon=True,
        json_logging=True,
    ),
}


# ---------------------------------------------------------------------------
# 全局单例管理
# ---------------------------------------------------------------------------

_flags: FeatureFlags | None = None


def init_feature_flags(profile: str | None = None) -> FeatureFlags:
    """根据 Profile 名称或 ``WIT_DEPLOY_MODE`` 环境变量初始化功能开关。

    支持通过 ``WIT_FEATURE_<FLAG_NAME>`` 环境变量覆盖单个开关，例如：
    - ``WIT_FEATURE_STORAGE_ENGINE=postgresql`` 覆盖存储引擎
    - ``WIT_FEATURE_SANDBOX_TYPE=docker`` 覆盖沙箱类型

    Args:
        profile: 预设 Profile 名称。若为 None，则从 ``WIT_DEPLOY_MODE`` 读取。

    Returns:
        初始化后的 FeatureFlags 实例。
    """
    global _flags

    import sys
    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        profile = "pc"
    elif profile is None:
        profile = os.getenv("WIT_DEPLOY_MODE", "").strip().lower() or "pc"

    base = PROFILES.get(profile)
    if base is None:
        logger.warning(
            "Unknown deploy profile '%s', falling back to 'pc'. "
            "Available profiles: %s",
            profile,
            ", ".join(PROFILES.keys()),
        )
        base = PROFILES["pc"]

    # 环境变量覆盖：WIT_FEATURE_<UPPER_FIELD_NAME>
    overrides: dict[str, object] = {}
    if not is_frozen:
        for f in fields(FeatureFlags):
            env_key = f"WIT_FEATURE_{f.name.upper()}"
            env_val = os.getenv(env_key)
            if env_val is not None:
                env_val = env_val.strip()
                # 类型转换
                if f.type == "bool":
                    overrides[f.name] = env_val.lower() in ("true", "1", "yes")
                elif f.type == "int":
                    try:
                        overrides[f.name] = int(env_val)
                    except ValueError:
                        logger.warning("Invalid integer for %s: %s", env_key, env_val)
                else:
                    overrides[f.name] = env_val

    if overrides:
        from dataclasses import asdict
        merged = {**asdict(base), **overrides}
        _flags = FeatureFlags(**merged)
        logger.info(
            "Feature flags initialized (profile=%s, overrides=%s): %s",
            profile, list(overrides.keys()), _flags,
        )
    else:
        _flags = base
        logger.info("Feature flags initialized (profile=%s): %s", profile, _flags)

    return _flags


def get_flags() -> FeatureFlags:
    """获取当前功能开关配置（只读）。

    若尚未初始化，则自动根据环境变量执行延迟初始化。
    """
    global _flags
    if _flags is None:
        init_feature_flags()
    return _flags  # type: ignore[return-value]


def get_profile_name() -> str:
    """返回当前生效的 Profile 名称。"""
    import sys
    if getattr(sys, "frozen", False):
        return "pc"
    return os.getenv("WIT_DEPLOY_MODE", "").strip().lower() or "pc"


# ---------------------------------------------------------------------------
# 向后兼容桥接
# ---------------------------------------------------------------------------

def is_saas_mode() -> bool:
    """向后兼容的 ``is_saas_mode()`` 桥接函数。

    .. deprecated::
        请迁移至 ``get_flags()`` 的语义化属性查询。
        此函数仅用于过渡期，当所有调用点完成迁移后将被移除。
    """
    return get_profile_name() == "saas"
