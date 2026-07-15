"""
Agent 运行时 (Agent Runtime)
==========================
为服务层提供与 LangChain Agent 交互的高级异步 API。管理用于消息持久化的 SqliteSaver 检查点，
并暴露流式传输 + 消息检索功能。

配置从 ~/.yanyu-wit/<account>/config.toml（而非 .env）中加载。
"""

import json
import logging
import collections
import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite

from provider.factory import ModelFactory

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是用户的个人助手，已集成并启用了 雁羽-鸿络 (Yanyu-Flux) 公告板平台与 ACPs (Agent Collaboration Protocols) 智能体协作协议能力。\n"
    "1. 雁羽-鸿络 (Yanyu-Flux) 能力：你可以帮助用户在公告板上发布信息、搜索活动、关注/取消关注活动，以及管理技能包（上传、下载、列表）。\n"
    "2. ACPs 智能体协作能力：你可以通过发现其他智能体（ADP），并与其进行任务委派和协作（AIP）。\n"
    "请根据用户的实际需求，积极利用这些工具和能力来协助用户完成任务。\n"
    "注意：将 agent 翻译成智能体而不是 代理。\n"
    "\n"
    "## 指令标签规范\n"
    "\n"
    "你的输入消息中可能包含以下 XML 指令标签，它们提供了调用上下文信息：\n"
    "\n"
    "- `<instruction>`: 能力模式切换。表示用户选择了某项特定能力，你必须使用对应的工具来完成任务。\n"
    "- `<scheduled_task>`: 定时任务上下文。表示本次调用由系统调度器自动发起，不是用户实时对话。标签内包含任务名称、ID、上次执行状态和日志路径等信息。你需要执行任务描述中的指令，完成后调用 report_result 工具汇报结果。\n"
    "- `<collaboration>`: 协作协议上下文。表示你被另一个智能体通过协作协议调用。\n"
    "\n"
    "没有这些标签时，表示用户在输入框中直接发送的消息，正常响应即可。"
)


from langchain.agents.middleware.types import AgentMiddleware, AgentState

class ToolCallLimiterMiddleware(AgentMiddleware[AgentState, Any, Any]):
    """限制单个 Agent 会话执行的工具调用次数上限。"""
    
    state_schema = AgentState
    
    def __init__(self, max_calls: int = 100) -> None:
        from contextvars import ContextVar
        self.max_calls = max_calls
        self.count_var = ContextVar("tool_calls_count", default=0)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        cnt = self.count_var.get()
        if cnt >= self.max_calls:
            raise RuntimeError(f"已达到工具调用次数上限（最大 {self.max_calls} 次）")
        self.count_var.set(cnt + 1)
        return await handler(request)


class AgentRuntime:
    """管理 Agent 生命周期并为服务层提供流式传输 API。"""

    def __init__(
        self,
        workspace_root: str | None = None,
        **kwargs,
    ):
        try:
            from cli.config import get_account_workspace_dir
            self._workspace_root = workspace_root or str(get_account_workspace_dir())
        except Exception:
            self._workspace_root = workspace_root or ""
        from agent.shell.container_manager import ContainerManager
        self._conns = {}
        self._checkpointers = {}
        self._agents = {}
        self._heartbeat_managers = {}  # workspace_dir -> HeartbeatManager
        self._model_factory = ModelFactory()
        self._container_manager = ContainerManager()
        self._redis_client = None  # Initialized in SaaS mode for distributed locking
        
        # Session locks for preventing concurrency conflicts on same thread_id
        self._session_locks = {}  # maps thread_id -> [asyncio.Lock, ref_count]
        self._lock_registry_mutex = asyncio.Lock()

        # Workspace cache eviction tracking
        self._active_workspaces = collections.defaultdict(int)  # maps workspace_dir -> active_count
        self._workspace_history = []  # list of workspace_dirs in MRU order

    async def initialize(self):
        """初始化异步资源。"""
        await self._container_manager.initialize()

        # SaaS: initialize Redis client for distributed session locking
        import os as _os
        from service.feature_flags import get_flags
        if get_flags().distributed_locking:
            redis_url = _os.getenv("REDIS_URL", "")
            if redis_url:
                try:
                    import redis.asyncio as aioredis
                    self._redis_client = aioredis.from_url(redis_url)
                    await self._redis_client.ping()
                    logger.info("Redis distributed lock client initialized (%s)", redis_url)
                except ImportError:
                    logger.info("redis.asyncio not installed, using local asyncio locks")
                except Exception as e:
                    logger.warning("Redis unavailable (%s), falling back to local locks", e)
                    self._redis_client = None

        logger.info("AgentRuntime initialized")

    @asynccontextmanager
    async def _session_context(self, thread_id: str):
        """获取特定于会话的锁并在未使用时将其清理的上下文管理器。
        SaaS 模式下优先使用 Redis 分布式锁（适配多实例水平扩展），
        不可用时回退到本地 asyncio.Lock。
        """
        if self._redis_client:
            # --- Redis 分布式锁分支 ---
            lock = self._redis_client.lock(
                f"wit:session:{thread_id}", timeout=1800, blocking_timeout=60
            )
            try:
                await lock.acquire()
                yield
            finally:
                try:
                    await lock.release()
                except Exception as e:
                    logger.warning("Failed to release Redis lock for %s: %s", thread_id, e)
        else:
            # --- 本地 asyncio.Lock 分支 ---
            async with self._lock_registry_mutex:
                if thread_id not in self._session_locks:
                    self._session_locks[thread_id] = [asyncio.Lock(), 0]
                self._session_locks[thread_id][1] += 1
                lock = self._session_locks[thread_id][0]
            try:
                async with lock:
                    yield
            finally:
                async with self._lock_registry_mutex:
                    self._session_locks[thread_id][1] -= 1
                    if self._session_locks[thread_id][1] == 0:
                        self._session_locks.pop(thread_id, None)

    @asynccontextmanager
    async def _workspace_context(self, workspace_dir: str):
        """用于跟踪工作区中活动请求的上下文管理器。"""
        async with self._lock_registry_mutex:
            self._active_workspaces[workspace_dir] += 1
        try:
            yield
        finally:
            async with self._lock_registry_mutex:
                self._active_workspaces[workspace_dir] -= 1

    async def _evict_workspaces_if_needed_unlocked(self):
        """从缓存中驱逐最近最少使用的非活动工作区。
        必须在 _lock_registry_mutex 下调用。
        """
        MAX_CACHED_WORKSPACES = 10
        while len(self._agents) >= MAX_CACHED_WORKSPACES:
            evicted_key = None
            for key in self._workspace_history:
                ws_dir = key[0] if isinstance(key, tuple) else key
                if self._active_workspaces.get(ws_dir, 0) == 0:
                    evicted_key = key
                    break
            
            if evicted_key is None:
                logger.warning(
                    "All %d cached workspaces are active. Cannot evict any. Temporarily exceeding cache limit.",
                    len(self._agents)
                )
                break
                
            logger.info("Evicting workspace from cache: %s", evicted_key)
            if evicted_key in self._workspace_history:
                self._workspace_history.remove(evicted_key)
            self._agents.pop(evicted_key, None)
            
            # Extract workspace directory to check checkpointer eviction
            ws_dir = evicted_key[0] if isinstance(evicted_key, tuple) else evicted_key
            
            # Check if any other agent uses the same ws_dir
            ws_in_use = False
            for k in self._agents:
                k_dir = k[0] if isinstance(k, tuple) else k
                if k_dir == ws_dir:
                    ws_in_use = True
                    break
            
            if not ws_in_use:
                self._checkpointers.pop(ws_dir, None)
                mgr = self._heartbeat_managers.pop(ws_dir, None)
                if mgr:
                    asyncio.create_task(mgr.shutdown())
                conn = self._conns.pop(ws_dir, None)
                if conn:
                    try:
                        await conn.close()
                    except Exception as e:
                        logger.warning("Failed to close SQLite connection for evicted workspace %s: %s", ws_dir, e)

    async def _get_checkpointer(self, workspace_dir: str):
        """为特定工作区获取或创建检查点。"""
        import os
        from service.feature_flags import get_flags
        if get_flags().storage_engine == "postgresql":
            if "postgres_saver" not in self._checkpointers:
                dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/yanyu_wit")
                from psycopg_pool import AsyncConnectionPool
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                pool = AsyncConnectionPool(conninfo=dsn, max_size=20)
                await pool.open()
                checkpointer = AsyncPostgresSaver(pool)
                await checkpointer.setup()
                self._checkpointers["postgres_saver"] = checkpointer
                self._conns["postgres_pool"] = pool
            return self._checkpointers["postgres_saver"]

        if workspace_dir not in self._checkpointers:
            db_path = Path(workspace_dir) / "yanyu-wit.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(
                str(db_path), check_same_thread=False
            )
            # Configure high-concurrency WAL mode and busy timeout
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            checkpointer = AsyncSqliteSaver(conn)
            await checkpointer.setup()
            self._conns[workspace_dir] = conn
            self._checkpointers[workspace_dir] = checkpointer
        return self._checkpointers[workspace_dir]

    async def _get_heartbeat_manager(self, workspace_dir: str):
        """获取或创建指定工作区的 HeartbeatManager。"""
        if workspace_dir not in self._heartbeat_managers:
            from heartbeat import HeartbeatManager
            mgr = HeartbeatManager(workspace_root=workspace_dir)

            # 设置 agent builder 回调
            async def _build_and_run_agent(session_id: str, job, report_tool) -> Any:
                """为定时任务构建精简版 agent 并执行。"""
                return await self._run_scheduled_agent(
                    workspace_dir=workspace_dir,
                    session_id=session_id,
                    job=job,
                    report_tool=report_tool,
                )

            mgr.set_agent_builder(_build_and_run_agent)
            await mgr.initialize()
            self._heartbeat_managers[workspace_dir] = mgr

        return self._heartbeat_managers[workspace_dir]

    async def _run_scheduled_agent(
        self, workspace_dir: str, session_id: str, job, report_tool
    ) -> Any:
        """为定时任务构建精简版 agent 并执行。"""
        from provider import DynamicChatModel
        model = DynamicChatModel(role="main", streaming=False)

        container_id = None
        try:
            # In SaaS mode, resolve / create container and update UserContext
            from service.context import get_current_user_ctx, set_current_user_ctx
            from service.feature_flags import get_flags
            from dataclasses import replace
            
            user_ctx = get_current_user_ctx()
            if get_flags().sandbox_type == "docker" and user_ctx:
                container_id = await self._container_manager.get_or_create(
                    user_id=user_ctx.user_id,
                    workspace_path=user_ctx.physical_workspace_dir or workspace_dir
                )
                user_ctx = replace(user_ctx, container_id=container_id)
                set_current_user_ctx(user_ctx)

            # --- Shell engine & Logical workspace ---
            if get_flags().sandbox_type == "docker":
                from agent.shell.docker_engine import DockerShellEngine
                engine = DockerShellEngine(
                    physical_workspace_dir=user_ctx.physical_workspace_dir if user_ctx else workspace_dir,
                )
                logical_workspace_dir = "/workspace"
            else:
                from agent.shell.local_engine import LocalShellEngine
                engine = LocalShellEngine()
                engine.cwd = workspace_dir
                logical_workspace_dir = workspace_dir

            from agent.middlewares.compression import CompressionMiddleware
            from agent.middlewares.memory import MemoryMiddleware
            from agent.middlewares.skills import SkillsMiddleware
            from agent.middlewares.filesystem import FilesystemMiddleware
            from agent.middlewares.yanyu import YanyuMiddleware
            from agent.middlewares.acps import AcpsMiddleware

            middlewares = []
            middlewares.append(CompressionMiddleware(workspace_root=logical_workspace_dir))

            middlewares.append(MemoryMiddleware(
                engine=engine,
                workspace_root=logical_workspace_dir,
            ))
            middlewares.append(FilesystemMiddleware(engine=engine, workspace_root=logical_workspace_dir))
            middlewares.append(SkillsMiddleware(engine=engine, workspace_root=logical_workspace_dir))

            middlewares.append(YanyuMiddleware(workspace_root=logical_workspace_dir))
            middlewares.append(AcpsMiddleware(engine=engine, workspace_root=logical_workspace_dir))

            if get_flags().tool_call_limit > 0:
                middlewares.append(ToolCallLimiterMiddleware(max_calls=get_flags().tool_call_limit))

            checkpointer = await self._get_checkpointer(workspace_dir)

            agent = create_agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
                middleware=middlewares,
                tools=[report_tool],  # 额外注入 report_result 工具
                checkpointer=checkpointer,
            )

            # 构建带 <scheduled_task> 标签的 prompt
            mgr = await self._get_heartbeat_manager(workspace_dir)
            prompt = mgr.executor.build_scheduled_prompt(job) if mgr else job.instruction

            config = self._make_config(session_id)

            async with self._session_context(session_id):
                async with self._workspace_context(workspace_dir):
                    if get_flags().tool_call_limit > 0:
                        return await asyncio.wait_for(
                            agent.ainvoke(
                                {"messages": [HumanMessage(content=prompt)]},
                                config=config,
                            ),
                            timeout=1800
                        )
                    else:
                        return await agent.ainvoke(
                            {"messages": [HumanMessage(content=prompt)]},
                            config=config,
                        )
        finally:
            if container_id:
                await self._container_manager.release(container_id)

    async def _build_agent(self, workspace_dir: str, session_id: str | None = None):
        """结合模型、工具、中间件和检查点构建 Agent。

        参数:
            workspace_dir: 用户的工作区目录。
            session_id: 当前会话的唯一标识，用于支持沙箱。
        """

        # --- Model: Dynamic proxy ---
        from provider import DynamicChatModel
        model = DynamicChatModel(role="main", streaming=True)

        # --- Unified system prompt ---
        system_prompt = SYSTEM_PROMPT

        # --- Shell engine & Logical workspace ---
        from service.feature_flags import get_flags
        from service.context import get_current_user_ctx
        user_ctx = get_current_user_ctx()
        if get_flags().sandbox_type == "docker":
            from agent.shell.docker_engine import DockerShellEngine
            engine = DockerShellEngine(
                physical_workspace_dir=user_ctx.physical_workspace_dir if user_ctx else workspace_dir,
            )
            logical_workspace_dir = "/workspace"
        else:
            if session_id:
                import os
                import hashlib
                ws_str = str(Path(workspace_dir).resolve())
                ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
                sandbox_root = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
                from agent.shell.local_engine import LocalShellEngine
                # Initialize local engine with sandbox as write-target and physical workspace for double-mapping validation
                engine = LocalShellEngine(root_dir=str(sandbox_root), physical_root=workspace_dir)
                logical_workspace_dir = workspace_dir
            else:
                from agent.shell.local_engine import LocalShellEngine
                engine = LocalShellEngine()
                engine.cwd = workspace_dir
                logical_workspace_dir = workspace_dir

        # --- Import all middlewares ---
        from agent.middlewares.compression import CompressionMiddleware
        from agent.middlewares.memory import MemoryMiddleware
        from agent.middlewares.skills import SkillsMiddleware
        from agent.middlewares.filesystem import FilesystemMiddleware
        from agent.middlewares.yanyu import YanyuMiddleware
        from agent.middlewares.acps import AcpsMiddleware

        # --- Build middleware instances (correct dependency order) ---
        middlewares = []

        # 1. Context management layer
        middlewares.append(CompressionMiddleware(
            workspace_root=logical_workspace_dir,
        ))

        # 2. Knowledge layer
        middlewares.append(MemoryMiddleware(
            engine=engine,
            workspace_root=logical_workspace_dir,
        ))

        # 3. Filesystem tools layer
        middlewares.append(FilesystemMiddleware(
            engine=engine,
            workspace_root=logical_workspace_dir,
        ))

        # 4. Skills layer
        middlewares.append(SkillsMiddleware(
            engine=engine,
            workspace_root=logical_workspace_dir,
        ))

        middlewares.append(YanyuMiddleware(workspace_root=logical_workspace_dir))

        middlewares.append(AcpsMiddleware(
            engine=engine,
            workspace_root=logical_workspace_dir,
            ))
        logger.info("Agent built with all capabilities (YanyuMiddleware and AcpsMiddleware)")

        checkpointer = await self._get_checkpointer(workspace_dir)

        agent = create_agent(
            model=model,
            system_prompt=system_prompt,
            middleware=middlewares,
            checkpointer=checkpointer,
        )

        return agent

    async def _get_agent(self, workspace_dir: str, session_id: str | None = None):
        cache_key = (workspace_dir, session_id) if session_id else workspace_dir
        async with self._lock_registry_mutex:
            if cache_key not in self._agents:
                await self._evict_workspaces_if_needed_unlocked()
                self._agents[cache_key] = await self._build_agent(workspace_dir, session_id)
            
            # Update MRU order
            if cache_key in self._workspace_history:
                self._workspace_history.remove(cache_key)
            self._workspace_history.append(cache_key)
            
            return self._agents[cache_key]

    def _make_config(self, thread_id: str) -> dict:
        """构建带有用于状态隔离的 thread_id 的 LangGraph 配置。"""
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100,
        }

    async def stream_chat(
        self, thread_id: str, user_message: str, workspace_dir: str,
        attachment_infos: list[dict] | None = None,
        capability: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        以 SSE 格式事件流式传输 Agent 响应。

        产生格式为：``data: {json}\n\n`` 的行。

        事件类型：
          - {"type": "token", "content": "..."} — 流式传输的文本 Token
          - {"type": "tool_start", "name": "...", "input": {...}} — 工具调用开始
          - {"type": "tool_end", "name": "...", "output": "..."} — 工具调用结束
          - {"type": "error", "message": "..."} — 发生错误
          - {"type": "done"} — 流式传输完成
        """
        container_id = None
        try:
            async with self._session_context(thread_id):
                async with self._workspace_context(workspace_dir):
                    config = self._make_config(thread_id)

                    # In SaaS mode, resolve / create container and update UserContext
                    from service.context import get_current_user_ctx, set_current_user_ctx
                    from service.feature_flags import get_flags
                    from dataclasses import replace
                    
                    user_ctx = get_current_user_ctx()
                    if get_flags().sandbox_type == "docker" and user_ctx:
                        container_id = await self._container_manager.get_or_create(
                            user_id=user_ctx.user_id,
                            workspace_path=user_ctx.physical_workspace_dir or workspace_dir
                        )
                        user_ctx = replace(user_ctx, container_id=container_id)
                        set_current_user_ctx(user_ctx)

                    # Enrich user message with attachment metadata if present
                    enriched_message = user_message
                    if attachment_infos:
                        att_lines = []
                        for att in attachment_infos:
                            att_lines.append(
                                f"- {att['original_name']} ({att['mime_type']}, "
                                f"{att['size_bytes']} bytes) → {att['path']}"
                            )
                        enriched_message = (
                            f"{user_message}\n\n"
                            f"[附件信息 - 以下文件已上传到本会话，可在 skill 工具中通过路径引用]\n"
                            + "\n".join(att_lines)
                        )

                    # Add capability prefix instruction block
                    if capability == 'flux':
                        enriched_message = (
                            "<instruction>你当前处于 Yanyu-Flux (雁羽-鸿络) 模式。为了获取或处理平台信息，你必须使用适当的 Yanyu-Flux 工具（如 publish_info、search_infos、list_infos 等），禁止直接凭记忆和语言回答问题。</instruction>\n\n"
                            f"{enriched_message}"
                        )
                    elif capability == 'acps':
                        enriched_message = (
                            "<instruction>你当前处于 ACPs (Agent Collaboration Protocols) 模式。为了进行任务委派或智能体发现，你必须使用适当的 ACPs 协作协议工具（如 acps_discover、acps_start 等），禁止直接凭记忆 and 语言回答问题。</instruction>\n\n"
                            f"{enriched_message}"
                        )

                    # In PC mode, prepare sandbox if thread_id is set
                    if get_flags().sandbox_type == "local" and thread_id:
                        import os
                        import hashlib
                        from agent.shell.local_sandbox import prepare_sandbox
                        ws_str = str(Path(workspace_dir).resolve())
                        ws_hash = hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
                        sandbox_root = Path(os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
                        prepare_sandbox(workspace_dir, sandbox_root)

                    inputs = {"messages": [HumanMessage(content=enriched_message)]}
                    agent = await self._get_agent(workspace_dir, thread_id)

                    try:
                        async for event in agent.astream_events(
                            inputs, config=config, version="v2"
                        ):
                            kind = event["event"]

                            if kind == "on_chat_model_stream":
                                content = event["data"]["chunk"].content
                                if content:
                                    yield self._sse({"type": "token", "content": content})

                            elif kind == "on_tool_start":
                                tool_name = event["name"]
                                tool_input = event["data"].get("input", {})
                                yield self._sse({
                                    "type": "tool_start",
                                    "name": tool_name,
                                    "input": tool_input,
                                })

                            elif kind == "on_tool_end":
                                tool_name = event["name"]
                                output = event["data"].get("output", "")
                                # Truncate large tool outputs for SSE
                                output_str = str(output)
                                if len(output_str) > 2000:
                                    output_str = output_str[:2000] + "... [truncated]"
                                yield self._sse({
                                    "type": "tool_end",
                                    "name": tool_name,
                                    "output": output_str,
                                })

                            elif kind == "on_chat_model_end":
                                output_msg = event["data"].get("output")
                                if output_msg:
                                    input_tokens = 0
                                    output_tokens = 0
                                    
                                    usage = getattr(output_msg, "usage_metadata", None)
                                    if usage:
                                        input_tokens = usage.get("input_tokens", 0)
                                        output_tokens = usage.get("output_tokens", 0)
                                    else:
                                        resp_meta = getattr(output_msg, "response_metadata", {})
                                        if isinstance(resp_meta, dict):
                                            token_usage = resp_meta.get("token_usage")
                                            if isinstance(token_usage, dict):
                                                input_tokens = token_usage.get("prompt_tokens", 0)
                                                output_tokens = token_usage.get("completion_tokens", 0)
                                                
                                    if input_tokens > 0 or output_tokens > 0:
                                        from service.context import get_current_user_ctx
                                        user_ctx = get_current_user_ctx()
                                        if user_ctx:
                                            try:
                                                from service.app import db
                                                db.record_token_usage(
                                                    user_id=user_ctx.user_id,
                                                    model=getattr(output_msg, "model_name", "unknown") or "unknown",
                                                    input_tokens=input_tokens,
                                                    output_tokens=output_tokens
                                                )
                                                logger.info(f"Recorded token usage for {user_ctx.user_id}: input={input_tokens}, output={output_tokens}")
                                            except Exception as ex:
                                                logger.warning(f"Failed to record token usage: {ex}")

                    except Exception as e:
                        logger.exception("Error during agent streaming for thread %s", thread_id)
                        yield self._sse({"type": "error", "message": str(e)})

                    # Auto-commit sandbox state after agent round (PC mode)
                    if get_flags().sandbox_type == "local" and thread_id:
                        try:
                            import os as _os
                            import hashlib as _hashlib
                            from agent.shell.local_sandbox import git_commit_round, list_versions
                            ws_str = str(Path(workspace_dir).resolve())
                            ws_hash = _hashlib.md5(ws_str.encode("utf-8")).hexdigest()[:16]
                            _sandbox_root = Path(_os.path.expanduser("~/.yanyu-wit/sandbox")) / f"pc_{ws_hash}"
                            if _sandbox_root.exists():
                                # Round number = current commit count (excluding initial)
                                existing = list_versions(_sandbox_root)
                                round_num = max(len(existing), 1)
                                # Use truncated user message as summary
                                _summary = enriched_message[:60] if enriched_message else ""
                                git_commit_round(_sandbox_root, round_num, _summary)
                        except Exception as commit_err:
                            logger.warning("Failed to auto-commit sandbox round: %s", commit_err)

                    yield self._sse({"type": "done"})
        finally:
            if container_id:
                await self._container_manager.release(container_id)

    async def get_messages(self, thread_id: str, workspace_dir: str, capability: str | None = None) -> list[dict]:
        """
        从检查点状态中检索消息历史记录。

        返回适合前端展示的、包含角色（role）、内容（content）、工具调用（tool_calls）等字段的字典列表。
        """
        async with self._workspace_context(workspace_dir):
            config = self._make_config(thread_id)
            agent = await self._get_agent(workspace_dir, thread_id)
            try:
                state = await agent.aget_state(config)
            except Exception:
                return []

            if not state or not state.values:
                return []

            messages = state.values.get("messages", [])
            result = []
            for msg in messages:
                item = self._message_to_dict(msg)
                if item:
                    result.append(item)
            return result

    def _message_to_dict(self, msg) -> dict | None:
        """将 LangChain 消息转换为对前端友好的字典。"""
        if isinstance(msg, HumanMessage):
            return {
                "role": "user",
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            }
        elif isinstance(msg, AIMessage):
            item = {
                "role": "assistant",
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            }
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                    }
                    for tc in msg.tool_calls
                ]
            return item
        elif isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate large tool results for frontend
            if len(content) > 3000:
                content = content[:3000] + "... [truncated]"
            return {
                "role": "tool",
                "content": content,
                "tool_call_id": msg.tool_call_id,
                "name": getattr(msg, "name", None),
            }
        elif isinstance(msg, SystemMessage):
            # Skip system messages in frontend display
            return None
        return None

    @staticmethod
    def _sse(data: dict) -> str:
        """将字典格式化为 SSE 数据行。"""
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate_title(self, user_message: str) -> str:
        """
        使用大模型根据用户的首条消息生成简洁的会话标题。

        返回简短的标题字符串（通常为 5-20 个字符）。
        如果出错，则回退到截断消息。
        """
        try:
            model = self._model_factory.get_model("title", streaming=False)
            response = await model.ainvoke([
                SystemMessage(content=(
                    "你是一个标题生成器。根据用户的消息，生成一个简短的会话标题。"
                    "要求：直接输出标题，不要加引号、标点或解释，"
                    "标题不超过20个字，要简洁明了地概括用户意图。"
                )),
                HumanMessage(content=user_message),
            ])
            title = response.content.strip().strip('"\'')
            if title:
                return title[:50]
        except Exception as e:
            logger.warning("Failed to generate title via LLM: %s", e)

        # Fallback: truncate user message
        return user_message[:50] + ("..." if len(user_message) > 50 else "")

    async def close(self):
        """清理资源。"""
        # Stop all heartbeat managers
        for mgr in list(self._heartbeat_managers.values()):
            try:
                await mgr.shutdown()
            except Exception as e:
                logger.warning("Failed to stop heartbeat manager on shutdown: %s", e)
        self._heartbeat_managers.clear()

        # Stop container manager
        try:
            await self._container_manager.stop_all()
        except Exception as e:
            logger.warning("Failed to stop container manager on shutdown: %s", e)

        # Close Redis client
        if self._redis_client:
            try:
                await self._redis_client.aclose()
            except Exception as e:
                logger.warning("Failed to close Redis client: %s", e)
            self._redis_client = None

        for conn in list(self._conns.values()):
            try:
                res = conn.close()
                import inspect
                if inspect.iscoroutine(res) or asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning("Failed to close connection on shutdown: %s", e)
        self._conns.clear()
        self._checkpointers.clear()
        self._agents.clear()
        self._workspace_history.clear()
        logger.info("AgentRuntime checkpointer connections closed")
