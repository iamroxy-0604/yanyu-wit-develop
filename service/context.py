"""Request-scoped execution context manager using ContextVar.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional, Dict, Any

def is_saas_mode() -> bool:
    """Check if the system is running in SaaS mode.

    .. deprecated::
        此函数保留为向后兼容桥接。请迁移至
        ``from service.feature_flags import get_flags`` 的语义化属性查询。
    """
    from service.feature_flags import is_saas_mode as _ff_is_saas
    return _ff_is_saas()

@dataclass(frozen=True)
class UserContext:
    """Immutable execution context details for a user request session."""
    user_id: str                      # User identifier (OIDC preferred_username or sub in SaaS, active_account in PC)
    entity_id: str                    # Entity AIC number
    workspace_dir: str                # User logic workspace directory
    db_path: str                      # User database path (PostgreSQL connection string or local SQLite path)
    deploy_mode: str                  # Deployment mode: "pc" | "saas"
    physical_workspace_dir: Optional[str] = None  # Host physical workspace path (for SaaS mounting)
    container_id: Optional[str] = None # SaaS Sandbox Container ID
    provider_config: Optional[Dict[str, Any]] = None  # User's active LLM configuration dict


# ContextVar binding current request context to the coroutine/thread pipeline
_current_user_ctx: ContextVar[Optional[UserContext]] = ContextVar(
    "_current_user_ctx", default=None
)


def get_current_user_ctx() -> Optional[UserContext]:
    """Retrieve the current coroutine-scoped UserContext."""
    return _current_user_ctx.get()


def set_current_user_ctx(ctx: UserContext) -> Any:
    """Set the current coroutine-scoped UserContext, returning a token to reset."""
    return _current_user_ctx.set(ctx)


def reset_current_user_ctx(token: Any) -> None:
    """Reset the context to the previous state using the provided token."""
    _current_user_ctx.reset(token)
