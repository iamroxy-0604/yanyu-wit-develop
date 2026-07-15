"""FastAPI 应用程序"""

import os
import json
import logging
import sqlite3
import threading
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user, TokenUser
from .auth_router import router as auth_router
from .db import BaseDatabase, User
from .models import (
    UserResponse,
    CreateSessionRequest,
    UpdateSessionRequest,
    SessionResponse,
    SessionListResponse,
    ChatStreamRequest,
    MessageItem,
    MessagesResponse,
    EventPayload,
    AttachmentResponse,
    AttachmentListResponse,
)

import uuid
import mimetypes
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from cli.config import (
    get_account_dir,
    get_account_workspace_dir,
    ensure_home_dir,
    get_active_account,
)
from service.context import UserContext, get_current_user_ctx, set_current_user_ctx
from service.feature_flags import get_flags, get_profile_name

import asyncio
import collections
import re

# ContextVar for request-scoped database routing (default=None prevents silent fallback)
current_username_var: ContextVar[str | None] = ContextVar("current_username", default=None)

# Thread-safe/asyncio active connections count per user_id in SaaS mode
active_connections = collections.defaultdict(int)

def mask_sse_data(chunk: str, physical_ws_root: str | None) -> str:
    if not get_flags().sse_masking:
        return chunk
        
    if not chunk.startswith("data: "):
        return chunk
        
    # Extract JSON content
    json_str = chunk[6:].strip()
    try:
        data = json.loads(json_str)
    except Exception:
        return chunk
        
    # Masking logic
    # 1. Mask physical paths
    if physical_ws_root:
        phys_root = os.path.abspath(physical_ws_root)
        
        def replace_paths(obj):
            if isinstance(obj, str):
                return obj.replace(phys_root, "/workspace")
            elif isinstance(obj, dict):
                return {k: replace_paths(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_paths(x) for x in obj]
            return obj
            
        data = replace_paths(data)
        
    # 2. Mask tool details for execute in tool_start and tool_end
    event_type = data.get("type")
    if event_type in ("tool_start", "tool_end"):
        tool_name = data.get("name")
        if tool_name == "execute":
            if event_type == "tool_start":
                data["input"] = {"status": "Executing container shell command"}
            elif event_type == "tool_end":
                data["output"] = "[Command output hidden for security in SaaS mode]"
                
    # 3. Mask sensitive keys (API keys for all providers, private keys, env vars, internal IPs)
    sensitive_patterns = [
        (re.compile(r"sk-[a-zA-Z0-9]{32,}"), "[MASKED-KEY]"),
        (re.compile(r"sk-ant-[a-zA-Z0-9\-]{32,}"), "[MASKED-KEY]"),
        (re.compile(r"AIza[a-zA-Z0-9\-_]{32,}"), "[MASKED-KEY]"),
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[MASKED-PRIVATE-KEY]"),
        (re.compile(r"DATABASE_URL=[^\s\"]+"), "DATABASE_URL=[MASKED]"),
        (re.compile(r"SESSION_SECRET=[^\s\"]+"), "SESSION_SECRET=[MASKED]"),
        (re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"), "[MASKED-IP]"),
    ]
    
    def mask_sensitive_strings(obj):
        if isinstance(obj, str):
            res = obj
            for pat, repl in sensitive_patterns:
                res = pat.sub(repl, res)
            return res
        elif isinstance(obj, dict):
            return {k: mask_sensitive_strings(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [mask_sensitive_strings(x) for x in obj]
        return obj
        
    data = mask_sensitive_strings(data)
    
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

async def sse_wrapper(gen, user_id: str, physical_ws_root: str | None):
    # We wrap the generator to check for timeouts
    try:
        iterator = gen.__aiter__()
        while True:
            try:
                # Wait for next item with a 15-second timeout
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=15.0)
                # Apply masking filter
                chunk = mask_sse_data(chunk, physical_ws_root)
                yield chunk
            except asyncio.TimeoutError:
                # Timeout reached, send keepalive comment
                yield ": keepalive\n\n"
            except StopAsyncIteration:
                break
    finally:
        if get_flags().sse_masking:
            active_connections[user_id] = max(0, active_connections[user_id] - 1)

class DynamicDatabase:
    """动态解析并将调用路由到正确的 Database 实例的代理。"""
    
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._pg_db = None
        
    def get_db(self, username: str | None = None) -> BaseDatabase:
        if get_flags().storage_engine == "postgresql":
            with self._lock:
                if not self._pg_db:
                    dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yanyu_wit")
                    from .db import PostgreSQLDatabase
                    self._pg_db = PostgreSQLDatabase(dsn)
                return self._pg_db

        # PC mode: resolve username from context chain
        if not username:
            ctx = get_current_user_ctx()
            if ctx:
                username = ctx.user_id
            else:
                username = current_username_var.get()
            # PC mode only: fall back to CLI active account (never 'default_user' in SaaS)
            if not username:
                from cli.config import get_active_account
                username = get_active_account()
            
        with self._lock:
            if username not in self._cache:
                ensure_home_dir(username)
                workspace = get_account_workspace_dir(username)
                db_path = workspace / "yanyu-wit.db"
                from .db import SQLiteDatabase
                self._cache[username] = SQLiteDatabase(db_path)
            
            return self._cache[username]

    def find_username_for_session(self, session_id: str) -> str | None:
        """SaaS 模式下通过数据库查询，PC 模式下扫描所有本地账户数据库。"""
        if get_flags().storage_engine == "postgresql":
            db_instance = self.get_db()
            session = db_instance.get_session(session_id)
            if session:
                user = db_instance.get_user_by_id(session.user_id)
                return user.oidc_sub if user else None
            return None

        from cli.config import YANYU_WIT_HOME
        accounts_dir = YANYU_WIT_HOME / "accounts"
        if not accounts_dir.exists():
            return None
            
        for user_dir in accounts_dir.iterdir():
            if user_dir.is_dir():
                username = user_dir.name
                db_path = user_dir / "workspace" / "yanyu-wit.db"
                if db_path.exists():
                    try:
                        conn = sqlite3.connect(str(db_path))
                        cursor = conn.cursor()
                        cursor.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,))
                        row = cursor.fetchone()
                        conn.close()
                        if row:
                            return username
                    except Exception:
                        pass
        return None

    def get_or_create_user(self, *args, **kwargs):
        return self.get_db().get_or_create_user(*args, **kwargs)

    def get_user_by_id(self, *args, **kwargs):
        return self.get_db().get_user_by_id(*args, **kwargs)

    def create_session(self, *args, **kwargs):
        return self.get_db().create_session(*args, **kwargs)

    def get_session(self, *args, **kwargs):
        return self.get_db().get_session(*args, **kwargs)

    def list_user_sessions(self, *args, **kwargs):
        return self.get_db().list_user_sessions(*args, **kwargs)

    def update_session(self, *args, **kwargs):
        return self.get_db().update_session(*args, **kwargs)

    def touch_session(self, *args, **kwargs):
        return self.get_db().touch_session(*args, **kwargs)

    def delete_session(self, *args, **kwargs):
        return self.get_db().delete_session(*args, **kwargs)

    def list_providers(self, *args, **kwargs):
        return self.get_db().list_providers(*args, **kwargs)

    def add_provider(self, *args, **kwargs):
        return self.get_db().add_provider(*args, **kwargs)

    def update_provider(self, *args, **kwargs):
        return self.get_db().update_provider(*args, **kwargs)

    def remove_provider(self, *args, **kwargs):
        return self.get_db().remove_provider(*args, **kwargs)

    def set_active_provider(self, *args, **kwargs):
        return self.get_db().set_active_provider(*args, **kwargs)

    def write_audit_log(self, *args, **kwargs):
        return self.get_db().write_audit_log(*args, **kwargs)

    def record_token_usage(self, *args, **kwargs):
        return self.get_db().record_token_usage(*args, **kwargs)

    def get_user_entity(self, *args, **kwargs):
        return self.get_db().get_user_entity(*args, **kwargs)

    def save_user_entity(self, *args, **kwargs):
        return self.get_db().save_user_entity(*args, **kwargs)

    def add_heartbeat_job(self, *args, **kwargs):
        return self.get_db().add_heartbeat_job(*args, **kwargs)

    def remove_heartbeat_job(self, *args, **kwargs):
        return self.get_db().remove_heartbeat_job(*args, **kwargs)

    def update_heartbeat_job(self, *args, **kwargs):
        return self.get_db().update_heartbeat_job(*args, **kwargs)

    def get_heartbeat_job(self, *args, **kwargs):
        return self.get_db().get_heartbeat_job(*args, **kwargs)

    def list_heartbeat_jobs(self, *args, **kwargs):
        return self.get_db().list_heartbeat_jobs(*args, **kwargs)

    def list_all_heartbeat_jobs(self, *args, **kwargs):
        return self.get_db().list_all_heartbeat_jobs(*args, **kwargs)

    def save_heartbeat_job_state(self, *args, **kwargs):
        return self.get_db().save_heartbeat_job_state(*args, **kwargs)

    def add_heartbeat_run_log(self, *args, **kwargs):
        return self.get_db().add_heartbeat_run_log(*args, **kwargs)

    def read_heartbeat_run_logs(self, *args, **kwargs):
        return self.get_db().read_heartbeat_run_logs(*args, **kwargs)


# Active account dynamic root paths — 使用统一端口解析与密钥管理模块
from cli.utils.port import resolve_port
from cli.utils.secrets import get_session_secret

WIT_PORT = int(os.getenv("WIT_PORT", resolve_port()))
SESSION_SECRET = get_session_secret()

# Attachment constraints
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_ATTACHMENTS_PER_SESSION = 20
ALLOWED_MIME_PREFIXES = ("image/", "application/zip", "application/gzip", "application/x-zip",
                          "application/pdf", "text/", "application/octet-stream")


db = DynamicDatabase()
agent_runtime = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在启动时初始化共享资源。"""
    global db, agent_runtime
    
    # Determine the boot-time user identity
    if get_flags().auth_mode != "oidc_pkce":
        # SaaS: use a dedicated system account for lifespan context (never 'default_user')
        active_user = os.getenv("WIT_SYSTEM_USER", "_system")
    else:
        # PC: use the CLI active account
        active_user = get_active_account()
        if not active_user:
            raise RuntimeError(
                "未配置活动账户，请先运行 `wit init` 完成初始化。"
            )
    ensure_home_dir(active_user)
    
    # Setup UserContext for lifespan scope
    from service.context import UserContext, set_current_user_ctx
    deploy_mode = get_profile_name()
    workspace_dir = str(get_account_workspace_dir(active_user))
    if get_flags().storage_engine == "postgresql":
        db_path = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yanyu_wit")
    else:
        db_path = str(Path(workspace_dir) / "yanyu-wit.db")
        
    ctx = UserContext(
        user_id=active_user,
        entity_id="",
        workspace_dir=workspace_dir,
        db_path=db_path,
        deploy_mode=deploy_mode,
        physical_workspace_dir=workspace_dir if get_flags().sandbox_type == "docker" else None,
        container_id=None,
        provider_config=None
    )
    set_current_user_ctx(ctx)
    
    logger.info("Service started (port=%s) on active account: %s (mode=%s)", WIT_PORT, active_user, deploy_mode)

    # Initialize agent runtime
    from agent.runtime import AgentRuntime
    agent_runtime = AgentRuntime()
    await agent_runtime.initialize()
    logger.info("AgentRuntime initialized")

    if get_flags().cert_renewal_daemon:
        from service.entity_manager import start_cert_renewal_daemon
        import asyncio
        asyncio.create_task(start_cert_renewal_daemon())
        logger.info("SaaS certificate renewal daemon started")

    if get_flags().heartbeat_mode != "disabled":
        # Start scheduled tasks for the active account (PC mode only)
        try:
            _ensure_user_workspace(active_user)
            active_workspace = get_account_workspace_dir(active_user)
            logger.info("Pre-building agent for active user workspace to boot scheduler: %s", active_workspace)
            await agent_runtime._get_agent(str(active_workspace))
        except Exception as e:
            logger.exception("Failed to pre-build agent or boot scheduler at startup: %s", e)

        # Auto-cleanup stale sandboxes at startup (PC mode only)
        try:
            from agent.shell.sandbox_manager import cleanup_stale_sandboxes
            cleaned = cleanup_stale_sandboxes(max_age_hours=72)
            if cleaned > 0:
                logger.info("Cleaned up %d stale sandbox(es) at startup", cleaned)
        except Exception as e:
            logger.warning("Failed to cleanup stale sandboxes at startup: %s", e)

    yield

    if agent_runtime:
        await agent_runtime.close()
    logger.info("Service shutting down")


app = FastAPI(
    title="YanYu-Wit Agent Service",
    version="0.1.0",
    description="OIDC-authenticated agent service with per-user workspaces.",
    lifespan=lifespan,
)

# Session middleware (for storing OIDC state/nonce/code_verifier during login flow)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False,  # Set to True in production with HTTPS
)

# SaaS production: set WIT_CORS_ORIGINS to restrict allowed origins (e.g. "https://app.example.com")
_cors_origins = [o.strip() for o in os.getenv("WIT_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include auth router
app.include_router(auth_router)


def _resolve_user(token_user: TokenUser) -> User:
    """
    给定已验证的 Token 用户，确保存在本地数据库记录，并返回该用户记录。
    """
    username = token_user.preferred_username or token_user.sub
    # Ensure the DB proxy routes to the correct user's database
    current_username_var.set(username)
    
    from service.context import UserContext, set_current_user_ctx
    deploy_mode = get_profile_name()
    workspace_dir = str(get_account_workspace_dir(username))
    if get_flags().storage_engine == "postgresql":
        db_path = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yanyu_wit")
    else:
        db_path = str(Path(workspace_dir) / "yanyu-wit.db")

    user = db.get_or_create_user(
        oidc_sub=username,
        oidc_issuer=token_user.issuer,
        display_name=token_user.display_name,
        email=token_user.email,
        roles=token_user.roles,
    )

    # Resolve entity_id and restore certificate files
    entity_id = ""
    if get_flags().storage_engine == "postgresql":
        entity = db.get_user_entity(username)
        if entity:
            entity_id = entity.get("entity_id") or ""
            try:
                from service.entity_manager import restore_entity_files_if_needed
                restore_entity_files_if_needed(username, entity)
            except Exception as e:
                logger.error(f"Failed to restore entity files for user {username}: {e}")
        else:
            try:
                from service.entity_manager import register_entity_if_needed
                threading.Thread(target=register_entity_if_needed, args=(username,), daemon=True).start()
            except Exception as e:
                logger.error(f"Failed to trigger background entity registration for user {username}: {e}")
    else:
        try:
            from cli.config import load_config
            cfg = load_config(username)
            entity_id = cfg.get("identity", {}).get("agent_aic", "")
        except Exception:
            pass

    # Read user's active provider config
    providers, active_idx = db.list_providers(username)
    provider_config = None
    if providers and 0 <= active_idx < len(providers):
        provider_config = providers[active_idx]

    # Inject context
    ctx = UserContext(
        user_id=username,
        entity_id=entity_id,
        workspace_dir=workspace_dir,
        db_path=db_path,
        deploy_mode=deploy_mode,
        physical_workspace_dir=workspace_dir if get_flags().sandbox_type == "docker" else None,
        container_id=None,
        provider_config=provider_config
    )
    set_current_user_ctx(ctx)
    try:
        db.write_audit_log(username, "login", f"User identity resolved from OIDC issuer: {token_user.issuer}")
    except Exception as e:
        logger.warning(f"Failed to write login audit log: {e}")
    return user


def _ensure_user_workspace(username: str) -> Path:
    """创建并返回用户的工作区目录。
    
    注意：这里特意不调用 set_active_account()，以避免产生修改全局状态的副作用。
    调用者应在需要时显式设置活动账户。
    """
    ensure_home_dir(username)
    
    workspace = get_account_workspace_dir(username)
    
    # Workspace should only contain memory and attachments
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    (workspace / "attachments").mkdir(parents=True, exist_ok=True)
    
    return workspace


async def _ensure_agent_and_scheduler(workspace: Path) -> None:
    """确保指定工作区的 agent 和定时任务调度器已初始化。"""
    if agent_runtime:
        workspace_str = str(workspace.resolve())
        # 这将触发 agent 编译，进而调用 heartbeat middleware 的 _ensure_initialized() 启动调度器
        await agent_runtime._get_agent(workspace_str)


def _session_to_response(s) -> SessionResponse:
    return SessionResponse(**s.to_dict())


def _assert_session_owner(session, user_id: str):
    """如果会话不属于该用户，则抛出 404 错误。"""
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------


def _get_attachment_dir(workspace: Path, session_id: str) -> Path:
    """返回会话的附件目录。"""
    att_dir = workspace / "attachments" / session_id
    att_dir.mkdir(parents=True, exist_ok=True)
    return att_dir


def _load_manifest(att_dir: Path) -> dict:
    """加载或初始化会话的附件清单。"""
    manifest_path = att_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"attachments": []}


def _save_manifest(att_dir: Path, manifest: dict):
    """持久化附件清单。"""
    with open(att_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _resolve_attachments(workspace: Path, session_id: str, attachment_ids: list[str]) -> list[dict]:
    """从清单中解析附件 ID 以获取其元数据。"""
    att_dir = workspace / "attachments" / session_id
    manifest = _load_manifest(att_dir)
    id_map = {a["id"]: a for a in manifest["attachments"]}
    result = []
    for aid in attachment_ids:
        if aid in id_map:
            result.append(id_map[aid])
    return result




@app.get("/api/me", response_model=UserResponse, tags=["User"])
async def get_me(token_user: TokenUser = Depends(get_current_user)):
    """获取当前已认证用户的个人资料。"""
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    return UserResponse(
        id=user.id,
        oidc_sub=user.oidc_sub,
        display_name=user.display_name,
        email=user.email,
        roles=user.roles_list,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        deploy_mode=get_profile_name(),
    )


# ---------------------------------------------------------------------------
# ATR (Trusted Registration) Endpoints
# ---------------------------------------------------------------------------

import threading

registration_in_progress = False
registration_error = None

def run_atr_registration(username: str, endpoint_url: str):
    global registration_in_progress, registration_error
    registration_in_progress = True
    registration_error = None
    try:
        from cli.config import ensure_home_dir
        ensure_home_dir(username)
        
        from cli.commands.atr_cmd import execute_auto
        import argparse
        dummy_args = argparse.Namespace(
            username=None,
            password=None,
            ontology_aic=None,
            ontology_cert=None,
            ontology_key=None,
            endpoint=endpoint_url,
            flux_url=None,
        )
        execute_auto(dummy_args)
    except Exception as e:
        logger.exception("ATR registration failed")
        registration_error = str(e)
    finally:
        registration_in_progress = False


@app.get("/api/atr/status", tags=["ATR"])
async def get_atr_status(token_user: TokenUser = Depends(get_current_user)):
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)
    
    # Explicitly read the specific user's config, not the global active account
    from cli.config import get_account_config_path
    import tomllib
    user_config_path = get_account_config_path(user.oidc_sub)
    cfg = {}
    if user_config_path.exists():
        try:
            with open(user_config_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception as e:
            logger.warning("Failed to load user config for ATR status: %s", e)
    
    identity = cfg.get("identity", {})
    certs = cfg.get("certs", {})
    
    agent_aic = identity.get("agent_aic", "")
    entity_cert = certs.get("entity_cert", "")
    entity_key = certs.get("entity_key", "")
    
    registered = bool(
        agent_aic and 
        entity_cert and os.path.exists(entity_cert) and 
        entity_key and os.path.exists(entity_key)
    )
    
    global registration_in_progress, registration_error
    return {
        "registered": registered,
        "agent_aic": agent_aic,
        "registering": registration_in_progress,
        "error": registration_error,
    }


from pydantic import BaseModel as PydanticBaseModel  # noqa: E402

class AtrRegisterRequest(PydanticBaseModel):
    endpoint: str

@app.post("/api/atr/register", tags=["ATR"])
async def register_atr(body: AtrRegisterRequest, token_user: TokenUser = Depends(get_current_user)):
    user = _resolve_user(token_user)
    username = user.oidc_sub
    _ensure_user_workspace(username)
    
    endpoint_url = body.endpoint.strip()
    if not endpoint_url:
        raise HTTPException(status_code=400, detail="请提供实体回调 URL (endpoint)")
    
    # Ensure the user's account config exists before ATR registration
    from cli.config import get_account_config_path, get_default_config_toml
    config_path = get_account_config_path(username)
    if not config_path.exists():
        config_path.write_text(get_default_config_toml(username))
    
    global registration_in_progress, registration_error
    if registration_in_progress:
        raise HTTPException(status_code=400, detail="注册已在进行中")
        
    # Start registration in a background thread
    threading.Thread(target=run_atr_registration, args=(username, endpoint_url), daemon=True).start()
    
    return {"status": "started"}


# ---------------------------------------------------------------------------
# Provider Management REST API
# ---------------------------------------------------------------------------


class ProviderCreateRequest(PydanticBaseModel):
    type: str
    name: str
    base_url: str = ""
    api_key: str = ""


class ProviderUpdateRequest(PydanticBaseModel):
    type: str | None = None
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


def _read_user_providers(username: str) -> tuple[list[dict], int]:
    """Read providers and active index from database."""
    return db.list_providers(username)


def _mask_key(api_key: str) -> str:
    """Mask API key, showing only last 4 chars."""
    if not api_key or len(api_key) <= 4:
        return api_key
    return "*" * (len(api_key) - 4) + api_key[-4:]


def _invalidate_agent_cache():
    """Invalidate agent runtime cache after provider switch."""
    global agent_runtime
    if agent_runtime:
        from provider.factory import reset_model_factory, ModelFactory
        reset_model_factory()
        # Re-create the ModelFactory with fresh config
        agent_runtime._model_factory = ModelFactory()
        # Clear cached agents so they rebuild with new model
        agent_runtime._agents.clear()
        logger.info("Agent cache invalidated after provider switch")


@app.get("/api/providers", tags=["Providers"])
async def list_providers_api(token_user: TokenUser = Depends(get_current_user)):
    """列出所有已配置的 Provider（API Key 脱敏）。"""
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)
    providers, active_idx = _read_user_providers(user.oidc_sub)

    masked = []
    for p in providers:
        masked.append({
            "type": p.get("type", ""),
            "name": p.get("name", ""),
            "base_url": p.get("base_url", ""),
            "api_key": _mask_key(p.get("api_key", "")),
        })
    return {"providers": masked, "active_index": active_idx}


@app.post("/api/providers", status_code=201, tags=["Providers"])
async def create_provider_api(
    body: ProviderCreateRequest,
    token_user: TokenUser = Depends(get_current_user),
):
    """新增一个 Provider。"""
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    try:
        idx = db.add_provider(user.oidc_sub, {
            "type": body.type,
            "name": body.name,
            "base_url": body.base_url,
            "api_key": body.api_key,
        })
        try:
            db.write_audit_log(user.oidc_sub, "add_provider", f"Added provider '{body.name}' of type {body.type}")
        except Exception as ae:
            logger.warning(f"Failed to write audit log: {ae}")
        return {"index": idx, "type": body.type, "name": body.name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/providers/{index}", tags=["Providers"])
async def update_provider_api(
    index: int,
    body: ProviderUpdateRequest,
    token_user: TokenUser = Depends(get_current_user),
):
    """更新指定索引的 Provider。"""
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    patch = {}
    if body.type is not None:
        patch["type"] = body.type
    if body.name is not None:
        patch["name"] = body.name
    if body.base_url is not None:
        patch["base_url"] = body.base_url
    if body.api_key is not None:
        patch["api_key"] = body.api_key

    try:
        updated = db.update_provider(user.oidc_sub, index, patch)
        try:
            db.write_audit_log(user.oidc_sub, "update_provider", f"Updated provider at index {index}: name='{updated.get('name')}'")
        except Exception as ae:
            logger.warning(f"Failed to write audit log: {ae}")
        # If updating the active provider, invalidate cache
        providers, active_idx = db.list_providers(user.oidc_sub)
        if index == active_idx:
            _invalidate_agent_cache()
        return {
            "type": updated.get("type", ""),
            "name": updated.get("name", ""),
            "base_url": updated.get("base_url", ""),
            "api_key": _mask_key(updated.get("api_key", "")),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/providers/{index}", status_code=204, tags=["Providers"])
async def delete_provider_api(
    index: int,
    token_user: TokenUser = Depends(get_current_user),
):
    """删除指定索引的 Provider。"""
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    if not db.remove_provider(user.oidc_sub, index):
        raise HTTPException(status_code=404, detail="Provider 不存在")

    try:
        db.write_audit_log(user.oidc_sub, "delete_provider", f"Deleted provider at index {index}")
    except Exception as ae:
        logger.warning(f"Failed to write audit log: {ae}")
    _invalidate_agent_cache()


@app.post("/api/providers/{index}/activate", tags=["Providers"])
async def activate_provider_api(
    index: int,
    token_user: TokenUser = Depends(get_current_user),
):
    """激活指定索引的 Provider。"""
    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    try:
        db.set_active_provider(user.oidc_sub, index)
        try:
            db.write_audit_log(user.oidc_sub, "activate_provider", f"Activated provider at index {index}")
        except Exception as ae:
            logger.warning(f"Failed to write audit log: {ae}")
        _invalidate_agent_cache()

        providers, _ = db.list_providers(user.oidc_sub)
        p = providers[index] if index < len(providers) else {}
        return {
            "active_index": index,
            "type": p.get("type", ""),
            "name": p.get("name", ""),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Scenes (Flux Platform Catalog)
# ---------------------------------------------------------------------------


def _get_flux_config(username: str) -> dict:
    """Read flux_url and auth token from the user's account config & workspace."""
    flux_url = os.getenv("FLUX_ENDPOINT") or os.getenv("FLUX_URL") or "http://127.0.0.1:13002"
    auth_headers = {}

    try:
        from cli.config import get_account_config_path, get_account_workspace_dir
        config_path = get_account_config_path(username)
        if config_path.exists():
            import tomllib
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
                if "services" in toml_data and "flux" in toml_data["services"]:
                    flux_url = toml_data["services"]["flux"].get("base_url", flux_url)

        # Read access token from credentials store (unified approach)
        from cli.utils.credentials import get_access_token as _get_at
        at = _get_at("flux")
        if at:
            auth_headers["Authorization"] = f"Bearer {at}"
    except Exception as e:
        logger.warning("Failed to load flux config for user %s: %s", username, e)

    return {"flux_url": flux_url.rstrip("/"), "headers": auth_headers}



# ---------------------------------------------------------------------------
# Flux Overview (Platform Info + Skills snapshot for frontend)
# ---------------------------------------------------------------------------


@app.get("/api/flux/overview", tags=["Flux"])
async def get_flux_overview(token_user: TokenUser = Depends(get_current_user)):
    """
    Fetch a snapshot of the Flux platform's latest infos and skills
    for the frontend overview display.
    
    Returns { infos: [...], skills: [...] }
    """
    import asyncio

    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    flux_cfg = _get_flux_config(user.oidc_sub)
    headers = flux_cfg["headers"]
    flux_url = flux_cfg["flux_url"]

    infos = []
    skills = []

    # Fetch latest infos (search with empty query for active items)
    try:
        def _fetch_infos():
            import requests as req
            payload = {
                "query": "",
                "filters": {"status": "active"},
                "top_k": 10,
                "page": 1,
                "page_size": 10,
            }
            return req.post(f"{flux_url}/search", json=payload, headers=headers, timeout=15)

        resp = await asyncio.to_thread(_fetch_infos)
        if resp.status_code == 200:
            data = resp.json()
            infos = data.get("hits", data.get("items", []))[:10]
    except Exception as e:
        logger.warning("Failed to fetch flux infos for overview: %s", e)

    # Fetch skill packages
    try:
        def _fetch_skills():
            import requests as req
            return req.get(f"{flux_url}/skill-packages", headers=headers, timeout=15)

        resp = await asyncio.to_thread(_fetch_skills)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            skills = items[:10] if isinstance(items, list) else []
    except Exception as e:
        logger.warning("Failed to fetch flux skills for overview: %s", e)

    return {"infos": infos, "skills": skills}


@app.get("/api/acps/overview", tags=["ACPs"])
async def get_acps_overview(token_user: TokenUser = Depends(get_current_user)):
    """
    Fetch a snapshot of recommended agents from the discovery server
    for the frontend overview display.
    
    Returns { "agents": [...] }
    """
    import asyncio
    import requests as req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    user = _resolve_user(token_user)
    _ensure_user_workspace(user.oidc_sub)

    discovery_url = os.getenv("DISCOVERY_URL", "https://ioa.pub/discovery/acps-adp-v2/discover")

    def _fetch_trending():
        try:
            payload = {
                "type": "trending",
                "limit": 6
            }
            resp = req.post(
                discovery_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
                verify=False
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result") or {}
                acs_map = result.get("acsMap") or {}
                
                agents_list = []
                agent_skills = []
                for group in result.get("agents") or []:
                    for skill in group.get("agentSkills") or []:
                        agent_skills.append(skill)
                
                seen_aics = set()
                for skill_entry in agent_skills:
                    aic = skill_entry.get("aic")
                    if not aic or aic in seen_aics:
                        continue
                    seen_aics.add(aic)
                    acs = acs_map.get(aic)
                    if isinstance(acs, dict):
                        core_info = {
                            "aic": aic,
                            "name": acs.get("name") or "未命名智能体",
                            "description": acs.get("description") or "无描述",
                            "active": acs.get("active", True),
                            "provider": acs.get("provider", {}).get("organization") or "未知提供商",
                            "skills": [s.get("name") for s in acs.get("skills", []) if s.get("name")][:3]
                        }
                        agents_list.append(core_info)
                return agents_list
        except Exception as e:
            logger.warning("Failed to fetch acps trending agents for overview: %s", e)
        return []

    agents = await asyncio.to_thread(_fetch_trending)
    return {"agents": agents}


@app.post("/api/sessions", response_model=SessionResponse, status_code=201, tags=["Sessions"])
async def create_session(
    body: CreateSessionRequest,
    token_user: TokenUser = Depends(get_current_user),
):
    """创建一个新的聊天会话。"""
    user = _resolve_user(token_user)
    session = db.create_session(user_id=user.id, title=body.title)
    return _session_to_response(session)


@app.get("/api/sessions", response_model=SessionListResponse, tags=["Sessions"])
async def list_sessions(
    active_only: bool = Query(True, description="只返回活跃会话"),
    limit: int = Query(50, ge=1, le=200),
    token_user: TokenUser = Depends(get_current_user),
):
    """列出当前用户的会话。"""
    user = _resolve_user(token_user)
    sessions = db.list_user_sessions(user.id, active_only=active_only, limit=limit)
    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        total=len(sessions),
    )


@app.get("/api/sessions/{session_id}", response_model=SessionResponse, tags=["Sessions"])
async def get_session(
    session_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """获取特定会话。"""
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    return _session_to_response(session)


@app.patch("/api/sessions/{session_id}", response_model=SessionResponse, tags=["Sessions"])
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    token_user: TokenUser = Depends(get_current_user),
):
    """更新会话（例如重命名）。"""
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    updated = db.update_session(session_id, title=body.title)
    return _session_to_response(updated)


@app.delete("/api/sessions/{session_id}", status_code=204, tags=["Sessions"])
async def delete_session(
    session_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """删除会话及其消息历史记录。"""
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    db.delete_session(session_id)
    # Note: checkpointer state for this thread_id will remain
    # but is harmless since the session record is deleted

    # PC mode: clean up associated sandbox directory
    if get_flags().sandbox_type == "local":
        try:
            workspace = _ensure_user_workspace(user.oidc_sub)
            from agent.shell.sandbox_manager import cleanup_workspace_sandbox
            cleanup_workspace_sandbox(str(workspace))
            logger.info("Cleaned up sandbox for deleted session %s", session_id)
        except Exception as e:
            logger.warning("Failed to cleanup sandbox for session %s: %s", session_id, e)


# ---------------------------------------------------------------------------
# Sandbox Management (PC Mode only)
# ---------------------------------------------------------------------------


@app.get("/api/sandbox/diff", tags=["Sandbox"])
async def get_sandbox_diff_endpoint(
    session_id: str | None = None,
    token_user: TokenUser = Depends(get_current_user),
):
    """获取指定会话沙箱与物理工作区的代码 diff (仅限 PC 模式)"""
    if get_flags().sandbox_type != "local":
        raise HTTPException(status_code=400, detail="沙箱 Diff 仅在 PC 本地模式下受支持")
        
    user = _resolve_user(token_user)
    if session_id:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)
    
    workspace = _ensure_user_workspace(user.oidc_sub)
    import os
    import hashlib
    ws_str = str(Path(workspace).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
    
    if not sandbox_dir.exists():
        return {"diffs": [], "message": "Sandbox does not exist"}
        
    from agent.shell.local_sandbox import get_sandbox_diff
    diffs = get_sandbox_diff(workspace, sandbox_dir)
    return {"diffs": diffs}


@app.post("/api/sandbox/apply", tags=["Sandbox"])
async def apply_sandbox_endpoint(
    session_id: str | None = None,
    token_user: TokenUser = Depends(get_current_user),
):
    """应用指定会话的沙箱变更到物理工作区，并清理沙箱 (仅限 PC 模式)"""
    if get_flags().sandbox_type != "local":
        raise HTTPException(status_code=400, detail="应用沙箱变更仅在 PC 本地模式下受支持")
        
    user = _resolve_user(token_user)
    if session_id:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)
    
    workspace = _ensure_user_workspace(user.oidc_sub)
    import os
    import hashlib
    ws_str = str(Path(workspace).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
    
    if not sandbox_dir.exists():
        raise HTTPException(status_code=404, detail="沙箱不存在或已被清理")
        
    from agent.shell.local_sandbox import apply_sandbox_changes
    apply_sandbox_changes(workspace, sandbox_dir)
    return {"status": "success", "message": "Changes applied successfully"}


@app.post("/api/sandbox/discard", tags=["Sandbox"])
async def discard_sandbox_endpoint(
    session_id: str | None = None,
    token_user: TokenUser = Depends(get_current_user),
):
    """丢弃指定会话的沙箱变更，并清理沙箱 (仅限 PC 模式)"""
    if get_flags().sandbox_type != "local":
        raise HTTPException(status_code=400, detail="丢弃沙箱变更仅在 PC 本地模式下受支持")
        
    user = _resolve_user(token_user)
    if session_id:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)
    
    workspace = _ensure_user_workspace(user.oidc_sub)
    import os
    import hashlib
    ws_str = str(Path(workspace).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
    
    if not sandbox_dir.exists():
        raise HTTPException(status_code=404, detail="沙箱不存在或已被清理")
        
    from agent.shell.local_sandbox import discard_sandbox_changes
    discard_sandbox_changes(sandbox_dir)
    return {"status": "success", "message": "Changes discarded successfully"}


@app.get("/api/sandbox/versions", tags=["Sandbox"])
async def list_sandbox_versions_endpoint(
    session_id: str | None = None,
    token_user: TokenUser = Depends(get_current_user),
):
    """获取指定会话沙箱的 git 版本历史列表 (仅限 PC 模式)"""
    if get_flags().sandbox_type != "local":
        raise HTTPException(status_code=400, detail="沙箱版本管理仅在 PC 本地模式下受支持")

    user = _resolve_user(token_user)
    if session_id:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)

    workspace = _ensure_user_workspace(user.oidc_sub)
    import os
    import hashlib
    ws_str = str(Path(workspace).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"

    if not sandbox_dir.exists():
        return {"versions": [], "message": "Sandbox does not exist"}

    from agent.shell.local_sandbox import list_versions
    versions = list_versions(sandbox_dir)
    return {"versions": versions}


@app.post("/api/sandbox/revert", tags=["Sandbox"])
async def revert_sandbox_endpoint(
    commit_hash: str,
    session_id: str | None = None,
    token_user: TokenUser = Depends(get_current_user),
):
    """将指定会话的沙箱回滚到指定 git 版本 (仅限 PC 模式)"""
    if get_flags().sandbox_type != "local":
        raise HTTPException(status_code=400, detail="沙箱版本回滚仅在 PC 本地模式下受支持")

    user = _resolve_user(token_user)
    if session_id:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)

    workspace = _ensure_user_workspace(user.oidc_sub)
    import os
    import hashlib
    ws_str = str(Path(workspace).resolve())
    ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
    sandbox_dir = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"

    if not sandbox_dir.exists():
        raise HTTPException(status_code=404, detail="沙箱不存在或已被清理")

    from agent.shell.local_sandbox import revert_to_version
    success = revert_to_version(sandbox_dir, commit_hash)
    if not success:
        raise HTTPException(status_code=400, detail="回滚失败，请检查版本哈希是否正确")

    return {"status": "success", "message": f"Successfully reverted to {commit_hash}"}


# ---------------------------------------------------------------------------
# Messages (read from checkpointer)
# ---------------------------------------------------------------------------


@app.get(
    "/api/sessions/{session_id}/messages",
    response_model=MessagesResponse,
    tags=["Messages"],
)
async def list_messages(
    session_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """从检查点状态中检索会话的消息。"""
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)

    if session_id.startswith("heartbeat:"):
        parts = session_id.split(":")
        if len(parts) >= 2:
            job_id = parts[1]
            mgr = await _get_heartbeat_manager(workspace)
            job = mgr.store.get_job(job_id)
            if not job:
                raise HTTPException(status_code=403, detail="没有访问该定时任务的权限")
        else:
            raise HTTPException(status_code=400, detail="无效的定时任务会话 ID")
    else:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)

    messages = await agent_runtime.get_messages(session_id, str(workspace))
    return MessagesResponse(
        messages=[MessageItem(**m) for m in messages],
    )


# ---------------------------------------------------------------------------
# Chat (SSE Streaming)
# ---------------------------------------------------------------------------


@app.post(
    "/api/sessions/{session_id}/chat",
    tags=["Chat"],
)
async def chat_stream(
    session_id: str,
    body: ChatStreamRequest,
    token_user: TokenUser = Depends(get_current_user),
):
    """
    向 Agent 发送消息并通过 SSE 流式传输响应。

    返回带有以下事件的 text/event-stream:
      - data: {"type": "token", "content": "..."}
      - data: {"type": "tool_start", "name": "...", "input": {...}}
      - data: {"type": "tool_end", "name": "...", "output": "..."}
      - data: {"type": "error", "message": "..."}
      - data: {"type": "done"}
    """
    user = _resolve_user(token_user)
    
    # Connection limit check (SaaS mode)
    if get_flags().sse_masking:
        user_id = user.oidc_sub
        if active_connections[user_id] >= 3:
            raise HTTPException(status_code=429, detail="同时进行的活跃会话连线已达到上限（最多 3 个）")
        active_connections[user_id] += 1

    try:
        session = db.get_session(session_id)
        _assert_session_owner(session, user.id)
        workspace = _ensure_user_workspace(user.oidc_sub)

        if not session.is_active:
            raise HTTPException(status_code=400, detail="会话已关闭")

        # Auto-generate session title from first message using LLM
        if session.title is None:
            try:
                title = await agent_runtime.generate_title(body.content)
                db.update_session(session_id, title=title)
            except Exception:
                # Fallback: use truncated message
                auto_title = body.content[:50] + ("..." if len(body.content) > 50 else "")
                db.update_session(session_id, title=auto_title)

        # Touch session timestamp
        db.touch_session(session_id)

        # Resolve attachment metadata if any
        attachment_infos = []
        if body.attachment_ids:
            attachment_infos = _resolve_attachments(workspace, session_id, body.attachment_ids)

        # Validate capability value
        capability = body.capability
        if capability and capability not in ('flux', 'acps'):
            raise HTTPException(status_code=400, detail="capability 必须是 'flux'、'acps' 或 null")

        # Get stream chat generator
        chat_gen = agent_runtime.stream_chat(
            thread_id=session_id,
            user_message=body.content,
            workspace_dir=str(workspace),
            attachment_infos=attachment_infos,
            capability=capability,
        )

        return StreamingResponse(
            sse_wrapper(chat_gen, user.oidc_sub, str(workspace)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        # If any exception occurs during startup, decrement connection count
        if get_flags().sse_masking:
            active_connections[user.oidc_sub] = max(0, active_connections[user.oidc_sub] - 1)
        raise e


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@app.post(
    "/api/sessions/{session_id}/attachments",
    response_model=AttachmentResponse,
    status_code=201,
    tags=["Attachments"],
)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    token_user: TokenUser = Depends(get_current_user),
):
    """
    上传文件附件到当前会话。

    限制条件:
      - 最大文件大小: 50 MB
      - 每个会话最大附件数: 20
      - 允许的 MIME 类型: images, zip, gzip, pdf, text
    """
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    workspace = _ensure_user_workspace(user.oidc_sub)

    att_dir = _get_attachment_dir(workspace, session_id)
    manifest = _load_manifest(att_dir)

    # Check attachment count limit
    if len(manifest["attachments"]) >= MAX_ATTACHMENTS_PER_SESSION:
        raise HTTPException(
            status_code=400,
            detail=f"每个会话最多 {MAX_ATTACHMENTS_PER_SESSION} 个附件"
        )

    # Validate MIME type
    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    if not any(mime_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {mime_type}"
        )

    # Read file content and check size
    content = await file.read()
    if len(content) > MAX_ATTACHMENT_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，最大 {MAX_ATTACHMENT_SIZE // (1024*1024)} MB"
        )

    # Generate unique ID and stored filename
    att_id = f"att_{uuid.uuid4().hex[:12]}"
    original_name = file.filename or "unnamed"
    # Sanitize filename
    safe_name = "".join(c for c in original_name if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "file"
    stored_name = f"{att_id}_{safe_name}"

    # Write file to disk
    file_path = att_dir / stored_name
    with open(file_path, "wb") as f:
        f.write(content)

    now = datetime.now(timezone.utc).isoformat()

    # Update manifest
    entry = {
        "id": att_id,
        "original_name": original_name,
        "stored_name": stored_name,
        "mime_type": mime_type,
        "size_bytes": len(content),
        "uploaded_at": now,
        "path": str(file_path),
    }
    manifest["attachments"].append(entry)
    _save_manifest(att_dir, manifest)

    return AttachmentResponse(
        id=att_id,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=len(content),
        uploaded_at=now,
    )


@app.get(
    "/api/sessions/{session_id}/attachments",
    response_model=AttachmentListResponse,
    tags=["Attachments"],
)
async def list_attachments(
    session_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """列出会话的所有附件。"""
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    workspace = _ensure_user_workspace(user.oidc_sub)

    att_dir = _get_attachment_dir(workspace, session_id)
    manifest = _load_manifest(att_dir)

    attachments = [
        AttachmentResponse(
            id=a["id"],
            original_name=a["original_name"],
            mime_type=a["mime_type"],
            size_bytes=a["size_bytes"],
            uploaded_at=a["uploaded_at"],
        )
        for a in manifest["attachments"]
    ]
    return AttachmentListResponse(attachments=attachments, total=len(attachments))


@app.delete(
    "/api/sessions/{session_id}/attachments/{attachment_id}",
    status_code=204,
    tags=["Attachments"],
)
async def delete_attachment(
    session_id: str,
    attachment_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """删除特定附件。"""
    user = _resolve_user(token_user)
    session = db.get_session(session_id)
    _assert_session_owner(session, user.id)
    workspace = _ensure_user_workspace(user.oidc_sub)

    att_dir = _get_attachment_dir(workspace, session_id)
    manifest = _load_manifest(att_dir)

    found = None
    for i, a in enumerate(manifest["attachments"]):
        if a["id"] == attachment_id:
            found = i
            break

    if found is None:
        raise HTTPException(status_code=404, detail="附件不存在")

    entry = manifest["attachments"].pop(found)
    _save_manifest(att_dir, manifest)

    # Delete file from disk
    file_path = Path(entry["path"])
    if file_path.exists():
        file_path.unlink()


# ---------------------------------------------------------------------------
# Webhook / Heartbeat
# ---------------------------------------------------------------------------


@app.post(
    "/api/webhook/event/{thread_id}",
    tags=["Webhook"],
)
async def receive_event(
    thread_id: str,
    body: EventPayload,
):
    """
    外部事件回调 —— 唤醒指定线程的 Agent。

    无需认证（用于调试）。如果需要，稍后添加认证。
    将事件作为 HumanMessage 注入并重新调用 Agent。
    """
    if agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent runtime not initialized")

    # Find which username owns this session
    username = db.find_username_for_session(thread_id)
    if username is None:
        raise HTTPException(status_code=404, detail="Session not found")

    current_username_var.set(username)

    # Find a workspace that owns this session
    session = db.get_session(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    user = db.get_user_by_id(session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    workspace = _ensure_user_workspace(user.oidc_sub)

    # Build the event message
    event_message = (
        f"[事件: {body.event_type}]\n\n"
        f"{body.details}\n\n"
        "请妥善响应此事件。"
    )

    # Stream the response (fire-and-forget style)
    # For webhook, we just invoke without streaming
    try:
        async with agent_runtime._session_context(thread_id):
            async with agent_runtime._workspace_context(str(workspace)):
                agent = await agent_runtime._get_agent(str(workspace))
                config = agent_runtime._make_config(thread_id)
                from langchain_core.messages import HumanMessage
                await agent.ainvoke(
                    {"messages": [HumanMessage(content=event_message)]},
                    config=config,
                )
    except Exception as e:
        logger.exception("Webhook event processing failed for thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "thread_id": thread_id, "event_type": body.event_type}


# ---------------------------------------------------------------------------
# AIP RPC Partner Endpoint (AIP v2 Protocol)
# ---------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Optional, Any
from acps_sdk.aip.aip_base_model import TaskResult, TaskCommand, TaskStatus, TaskState, Product, TextDataItem, TaskCommandType
from acps_sdk.aip.aip_rpc_model import RpcRequest, RpcResponse, JSONRPCError

@dataclass
class AipTaskContext:
    task: TaskResult
    running_future: Optional[asyncio.Task] = None

# In-memory store for active AIP tasks
_aip_tasks: dict[str, AipTaskContext] = {}
_aip_tasks_lock = asyncio.Lock()


def _resolve_aip_user_context() -> UserContext:
    """解析当前运行实例下的系统用户和工作区环境"""
    from service.context import get_current_user_ctx, set_current_user_ctx, UserContext
    ctx = get_current_user_ctx()
    if ctx and ctx.user_id:
        return ctx

    if get_flags().auth_mode != "oidc_pkce":
        username = os.getenv("WIT_SYSTEM_USER", "_system")
    else:
        username = get_active_account()
        if not username:
            raise HTTPException(status_code=500, detail="本地未配置活动账户，请运行 `wit init`。")

    current_username_var.set(username)
    workspace_dir = str(get_account_workspace_dir(username))
    if get_flags().storage_engine == "postgresql":
        db_path = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yanyu_wit")
    else:
        db_path = str(Path(workspace_dir) / "yanyu-wit.db")

    providers, active_idx = db.list_providers(username)
    provider_config = None
    if providers and 0 <= active_idx < len(providers):
        provider_config = providers[active_idx]

    entity_id = ""
    if get_flags().storage_engine == "postgresql":
        entity = db.get_user_entity(username)
        if entity:
            entity_id = entity.get("entity_id") or ""
    else:
        try:
            from cli.config import load_config
            cfg = load_config(username)
            entity_id = cfg.get("identity", {}).get("agent_aic", "")
        except Exception:
            pass

    ctx = UserContext(
        user_id=username,
        entity_id=entity_id,
        workspace_dir=workspace_dir,
        db_path=db_path,
        deploy_mode=get_profile_name(),
        physical_workspace_dir=workspace_dir if get_flags().sandbox_type == "docker" else None,
        container_id=None,
        provider_config=provider_config
    )
    set_current_user_ctx(ctx)
    return ctx


def _update_aip_task_status(task_id: str, new_state: TaskState, data_items: list = None) -> TaskResult:
    ctx = _aip_tasks.get(task_id)
    if not ctx:
        raise ValueError(f"AIP Task {task_id} not found")
    
    new_status = TaskStatus(
        state=new_state,
        stateChangedAt=datetime.now(timezone.utc).isoformat(),
        dataItems=data_items or [],
    )
    ctx.task.status = new_status
    if ctx.task.statusHistory:
        ctx.task.statusHistory.append(new_status)
    else:
        ctx.task.statusHistory = [new_status]
        
    return ctx.task


def _add_aip_command(task_id: str, command: TaskCommand):
    ctx = _aip_tasks.get(task_id)
    if ctx:
        if ctx.task.commandHistory:
            ctx.task.commandHistory.append(command)
        else:
            ctx.task.commandHistory = [command]


async def _run_aip_agent_task(workspace_dir: str, session_id: str, task_id: str, prompt: str):
    """在后台执行 LangGraph Agent 以处理 AIP 任务"""
    from langchain_core.messages import HumanMessage, AIMessage
    
    # 1. 切换状态为 Working
    _update_aip_task_status(task_id, TaskState.Working)
    
    try:
        global agent_runtime
        if agent_runtime is None:
            from agent.runtime import AgentRuntime
            agent_runtime = AgentRuntime()
            await agent_runtime.initialize()
            
        # 构建并运行 Agent
        agent = await agent_runtime._get_agent(workspace_dir, session_id=session_id)
        config = agent_runtime._make_config(session_id)
        
        res = await agent.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=config,
        )
        
        # 2. 提取最终的助理回答
        final_output = ""
        if res and isinstance(res, dict):
            messages = res.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    final_output = str(msg.content)
                    break
                    
        # 3. 构建产出物 Product
        product = Product(
            id=f"prod-{uuid.uuid4().hex[:12]}",
            dataItems=[TextDataItem(text=final_output)],
        )
        
        async with _aip_tasks_lock:
            ctx = _aip_tasks.get(task_id)
            if ctx:
                ctx.task.products = [product]
                
        # 4. 更新状态为 AwaitingCompletion
        _update_aip_task_status(
            task_id,
            TaskState.AwaitingCompletion,
            [TextDataItem(text="AIP task processing finished, awaiting completion.")]
        )
        
    except asyncio.CancelledError:
        logger.info("AIP Task %s background processing cancelled", task_id)
        _update_aip_task_status(task_id, TaskState.Canceled, [TextDataItem(text="Task execution was cancelled.")])
    except Exception as e:
        logger.exception("Failed to run AIP task %s: %s", task_id, e)
        _update_aip_task_status(task_id, TaskState.Failed, [TextDataItem(text=f"Internal execution error: {str(e)}")])


@app.post(
    "/aip/rpc",
    response_model=RpcResponse,
    tags=["AIP"],
)
async def aip_rpc_endpoint(request: RpcRequest):
    """
    AIP (Agent Interaction Protocol) 远程调用接口（RPC Style）。
    支持 start, get, continue, cancel, complete 命令。
    """
    # 1. 确保用户上下文和工作区存在
    try:
        user_ctx = _resolve_aip_user_context()
        workspace_dir = user_ctx.workspace_dir
        entity_id = user_ctx.entity_id
    except Exception as e:
        logger.error("AIP endpoint failed to resolve user context: %s", e)
        return RpcResponse(
            id=request.id,
            error=JSONRPCError(code=-32603, message="Internal context resolution error", data=str(e)),
        )

    command = request.params.command
    task_id = getattr(command, "taskId", None)
    session_id = getattr(command, "sessionId", None)
    command_type = getattr(command, "command", None)

    if not task_id:
        return RpcResponse(
            id=request.id,
            error=JSONRPCError(code=-32602, message="taskId is required in command parameters"),
        )

    async with _aip_tasks_lock:
        ctx = _aip_tasks.get(task_id)
        task = ctx.task if ctx else None

    # ---- 2. 处理 Start 命令 ----
    if command_type == TaskCommandType.Start:
        if task:
            # 任务已存在，直接返回
            _add_aip_command(task_id, command)
            return RpcResponse(id=request.id, result=task)

        # 提取 Leader 发来的指令内容
        texts = []
        if command.dataItems:
            for item in command.dataItems:
                if hasattr(item, "text") and item.text:
                    texts.append(item.text)
                elif isinstance(item, dict) and item.get("text"):
                    texts.append(item["text"])
        input_text = "\n".join(texts)

        # 用 XML 标签包裹指令以标识这是一次外部 Agent 协作调用
        collaboration_tag = (
            "<collaboration>\n"
            "  <context>这是另一个智能体（Leader）通过协作协议（AIP）发起的任务委托调用。</context>\n"
            "  <collaboration_info>\n"
            f"    <leader_id>{command.senderId}</leader_id>\n"
            f"    <session_id>{session_id}</session_id>\n"
            f"    <task_id>{task_id}</task_id>\n"
            "  </collaboration_info>\n"
            "</collaboration>\n\n"
        )
        prompt = collaboration_tag + input_text

        # 初始化 TaskResult 并设置为 Accepted 状态
        new_task = TaskResult(
            id=f"result-{uuid.uuid4().hex[:12]}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="partner",
            senderId=entity_id or "default-aic",
            taskId=task_id,
            sessionId=session_id,
            status=TaskStatus(
                state=TaskState.Accepted,
                stateChangedAt=datetime.now(timezone.utc).isoformat(),
            ),
            commandHistory=[command],
            statusHistory=[
                TaskStatus(
                    state=TaskState.Accepted,
                    stateChangedAt=datetime.now(timezone.utc).isoformat(),
                )
            ],
        )

        # 开启后台异步任务运行 LangGraph agent
        future = asyncio.create_task(
            _run_aip_agent_task(workspace_dir, session_id, task_id, prompt)
        )
        
        async with _aip_tasks_lock:
            _aip_tasks[task_id] = AipTaskContext(task=new_task, running_future=future)
            
        return RpcResponse(id=request.id, result=new_task)

    # ---- 3. 处理其他任务生命周期命令 ----
    if not task:
        return RpcResponse(
            id=request.id,
            error=JSONRPCError(code=-32001, message=f"Task {task_id} not found"),
        )

    if command_type == TaskCommandType.Get:
        # 直接返回当前的 TaskResult，以便 Leader 轮询最新状态
        return RpcResponse(id=request.id, result=task)

    elif command_type == TaskCommandType.Continue:
        # 验证当前状态是否允许 Continue
        if task.status.state not in (TaskState.AwaitingInput, TaskState.AwaitingCompletion):
            return RpcResponse(
                id=request.id,
                error=JSONRPCError(
                    code=-32602,
                    message=f"Task {task_id} is in state {task.status.state}, cannot continue."
                ),
            )
            
        # 提取追加的输入内容
        texts = []
        if command.dataItems:
            for item in command.dataItems:
                if hasattr(item, "text") and item.text:
                    texts.append(item.text)
                elif isinstance(item, dict) and item.get("text"):
                    texts.append(item["text"])
        input_text = "\n".join(texts)

        # XML 标签包裹追加指令
        collaboration_tag = (
            "<collaboration>\n"
            "  <context>这是针对上述协作任务的追加指令/输入。</context>\n"
            "  <collaboration_info>\n"
            f"    <leader_id>{command.senderId}</leader_id>\n"
            f"    <session_id>{session_id}</session_id>\n"
            f"    <task_id>{task_id}</task_id>\n"
            "  </collaboration_info>\n"
            "</collaboration>\n\n"
        )
        prompt = collaboration_tag + input_text

        _add_aip_command(task_id, command)
        _update_aip_task_status(task_id, TaskState.Working)

        # 取消已有的正在执行的 Future（如有）
        if ctx.running_future and not ctx.running_future.done():
            ctx.running_future.cancel()

        # 启动新的后台异步任务继续执行
        future = asyncio.create_task(
            _run_aip_agent_task(workspace_dir, session_id, task_id, prompt)
        )
        ctx.running_future = future
        return RpcResponse(id=request.id, result=task)

    elif command_type == TaskCommandType.Cancel:
        _add_aip_command(task_id, command)
        if ctx.running_future and not ctx.running_future.done():
            ctx.running_future.cancel()
            
        terminal_states = {TaskState.Completed, TaskState.Failed, TaskState.Rejected, TaskState.Canceled}
        if task.status.state in terminal_states:
            return RpcResponse(id=request.id, result=task)
            
        updated_task = _update_aip_task_status(task_id, TaskState.Canceled)
        return RpcResponse(id=request.id, result=updated_task)

    elif command_type == TaskCommandType.Complete:
        _add_aip_command(task_id, command)
        if task.status.state == TaskState.AwaitingCompletion:
            updated_task = _update_aip_task_status(task_id, TaskState.Completed)
            return RpcResponse(id=request.id, result=updated_task)
        return RpcResponse(id=request.id, result=task)

    else:
        return RpcResponse(
            id=request.id,
            error=JSONRPCError(code=-32602, message=f"Unsupported command type: {command_type}"),
        )


# ---------------------------------------------------------------------------
# Heartbeat (Scheduled Tasks) REST API
# ---------------------------------------------------------------------------


async def _get_heartbeat_manager(workspace: Path):
    """获取指定工作区的 HeartbeatManager 实例。"""
    workspace_str = str(workspace.resolve())
    if agent_runtime:
        return await agent_runtime._get_heartbeat_manager(workspace_str)
    # Fallback: 创建临时实例
    from heartbeat import HeartbeatManager
    mgr = HeartbeatManager(workspace_root=workspace_str, db_instance=db)
    await mgr.initialize()
    return mgr


def _job_to_response(job) -> dict:
    """将 HeartbeatJob 转换为 API 响应字典。"""
    from dataclasses import asdict
    return {
        "id": job.id,
        "user_id": job.user_id,
        "name": job.name,
        "description": job.description,
        "enabled": job.enabled,
        "type": job.type,
        "instruction": job.instruction,
        "script_path": job.script_path,
        "schedule": asdict(job.schedule),
        "created_at_ms": job.created_at_ms,
        "updated_at_ms": job.updated_at_ms,
        "state": asdict(job.state),
    }


@app.get("/api/heartbeat/jobs", tags=["Heartbeat"])
async def list_heartbeat_jobs(
    include_disabled: bool = Query(True),
    token_user: TokenUser = Depends(get_current_user),
):
    """列出所有定时任务。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    jobs = mgr.store.list_jobs(include_disabled=include_disabled)
    return {
        "jobs": [_job_to_response(j) for j in jobs],
        "total": len(jobs),
    }


@app.post("/api/heartbeat/jobs", status_code=201, tags=["Heartbeat"])
async def create_heartbeat_job(
    body: dict,
    token_user: TokenUser = Depends(get_current_user),
):
    """创建定时任务。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    from heartbeat.models import HeartbeatJob, ScheduleConfig, generate_job_id, now_ms
    from heartbeat.scheduler import compute_next_run_at_ms

    user = _resolve_user(token_user)

    task_type = body.get("type", "agent")
    if task_type not in ("agent", "script"):
        raise HTTPException(status_code=400, detail="type 必须是 'agent' 或 'script'")

    frequency = body.get("frequency", "daily")
    if frequency not in ("daily", "weekly", "monthly", "once"):
        raise HTTPException(status_code=400, detail="frequency 必须是 'daily'、'weekly'、'monthly' 或 'once'")

    schedule = ScheduleConfig(
        frequency=frequency,
        time=body.get("time", "09:00"),
        weekdays=body.get("weekdays", []),
        monthdays=body.get("monthdays", []),
        once_at=body.get("once_at") or None,
        timezone=body.get("timezone", "Asia/Shanghai"),
    )

    now = now_ms()
    job = HeartbeatJob(
        id=generate_job_id(),
        user_id=user.oidc_sub,
        name=body.get("name", "未命名任务"),
        description=body.get("description", ""),
        enabled=True,
        type=task_type,
        instruction=body.get("instruction", ""),
        script_path=body.get("script_path", ""),
        schedule=schedule,
        created_at_ms=now,
        updated_at_ms=now,
    )
    job.state.next_run_at_ms = compute_next_run_at_ms(schedule, now)

    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    await mgr.store.add_job(job)
    mgr.scheduler.reschedule()
    return _job_to_response(job)


@app.patch("/api/heartbeat/jobs/{job_id}", tags=["Heartbeat"])
async def update_heartbeat_job(
    job_id: str,
    body: dict,
    token_user: TokenUser = Depends(get_current_user),
):
    """更新定时任务。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)

    patch = {}
    if "name" in body:
        patch["name"] = body["name"]
    if "description" in body:
        patch["description"] = body["description"]
    if "enabled" in body:
        patch["enabled"] = body["enabled"]
    if "type" in body:
        patch["type"] = body["type"]
    if "instruction" in body:
        patch["instruction"] = body["instruction"]
    if "script_path" in body:
        patch["script_path"] = body["script_path"]

    # 构建 schedule patch
    sched_keys = {"frequency", "time", "weekdays", "monthdays", "once_at", "timezone"}
    sched_patch = {k: body[k] for k in sched_keys if k in body}
    if sched_patch:
        job = mgr.store.get_job(job_id)
        if job:
            from dataclasses import asdict
            current = asdict(job.schedule)
            current.update(sched_patch)
            patch["schedule"] = current

    job = await mgr.store.update_job(job_id, patch)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 重新计算 next_run
    if job.enabled:
        from heartbeat.scheduler import compute_next_run_at_ms
        from heartbeat.models import now_ms
        job.state.next_run_at_ms = compute_next_run_at_ms(job.schedule, now_ms())
        await mgr.store.save()

    mgr.scheduler.reschedule()
    return _job_to_response(job)


@app.delete("/api/heartbeat/jobs/{job_id}", status_code=204, tags=["Heartbeat"])
async def delete_heartbeat_job(
    job_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """删除定时任务。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    removed = await mgr.store.remove_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail="任务不存在")
    mgr.scheduler.reschedule()


@app.get("/api/heartbeat/jobs/{job_id}/runs", tags=["Heartbeat"])
async def get_heartbeat_runs(
    job_id: str,
    limit: int = Query(20, ge=1, le=100),
    token_user: TokenUser = Depends(get_current_user),
):
    """查询任务的执行日志。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    entries = await mgr.store.read_run_log(job_id, limit=limit)
    return {"entries": entries, "total": len(entries)}


@app.post("/api/heartbeat/jobs/{job_id}/reveal", tags=["Heartbeat"])
async def reveal_heartbeat_job_folder(
    job_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """在操作系统文件资源管理器中打开该任务的产物文件夹。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=400, detail="当前部署模式下不支持此操作")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)

    job = mgr.store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    folder_path = workspace / "heartbeat" / "artifacts" / job_id
    if not folder_path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)

    import subprocess
    import sys

    path_str = str(folder_path.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(path_str)
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=True)
        else:
            subprocess.run(["xdg-open", path_str], check=True)
        return {"status": "success"}
    except Exception as e:
        logger.exception("Failed to open folder: %s", e)
        raise HTTPException(status_code=500, detail=f"无法打开文件夹: {e}")


@app.post("/api/heartbeat/jobs/{job_id}/run", tags=["Heartbeat"])
async def trigger_heartbeat_job(
    job_id: str,
    token_user: TokenUser = Depends(get_current_user),
):
    """手动触发一次任务执行。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    from heartbeat.models import now_ms

    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    job = mgr.store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    job.state.next_run_at_ms = now_ms()
    await mgr.store.save()
    mgr.scheduler.reschedule()
    return {"status": "triggered", "job_id": job_id}


@app.get("/api/heartbeat/status", tags=["Heartbeat"])
async def get_heartbeat_status(
    token_user: TokenUser = Depends(get_current_user),
):
    """获取调度器状态。"""
    if get_flags().heartbeat_mode == "disabled":
        raise HTTPException(status_code=403, detail="当前部署模式下不支持定时任务功能")
    user = _resolve_user(token_user)
    workspace = _ensure_user_workspace(user.oidc_sub)
    await _ensure_agent_and_scheduler(workspace)
    mgr = await _get_heartbeat_manager(workspace)
    all_jobs = mgr.store.list_jobs(include_disabled=True)

    active = [j for j in all_jobs if j.enabled]
    next_run = None
    for j in active:
        nxt = j.state.next_run_at_ms
        if nxt is not None and (next_run is None or nxt < next_run):
            next_run = nxt
    return {
        "enabled": True,
        "total_jobs": len(all_jobs),
        "active_jobs": len(active),
        "next_run_at_ms": next_run,
    }

# ---------------------------------------------------------------------------
# React Frontend Static Files Hosting
# ---------------------------------------------------------------------------

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import sys

# Resolve web/dist static directory
if getattr(sys, 'frozen', False):
    static_dir = Path(sys._MEIPASS) / "web" / "dist"
else:
    static_dir = Path(__file__).parent.parent / "web" / "dist"

if static_dir.exists():
    logger.info("Serving frontend static assets from: %s", static_dir)
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon():
        return FileResponse(str(static_dir / "favicon.svg"))

    @app.get("/icons.svg", include_in_schema=False)
    async def icons():
        return FileResponse(str(static_dir / "icons.svg"))

    @app.get("/{catchall:path}", include_in_schema=False)
    async def serve_react_app(catchall: str):
        # Do not catch requests starting with api/, auth/ or health
        if catchall.startswith("api/") or catchall.startswith("auth/") or catchall == "health":
            raise HTTPException(status_code=404, detail="Not Found")
        
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="React frontend index.html not found.")
else:
    logger.warning("Frontend static directory %s not found. Web UI will be unavailable.", static_dir)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start():
    """使用 uvicorn 启动服务。"""
    import uvicorn
    import logging
    from service.feature_flags import get_flags as _get_flags

    if _get_flags().json_logging:
        from service.logger_formatter import JSONLogFormatter
        handler = logging.StreamHandler()
        handler.setFormatter(JSONLogFormatter())
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
        root_logger.addHandler(handler)
        
        logger.info("Structured JSON logging configured for SaaS mode.")
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    uvicorn.run(
        "service.app:app",
        host="0.0.0.0",
        port=WIT_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    start()
