"""ACPs 中间件"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    NotRequired,
    TypedDict,
)

import httpx
from agent.shell import BaseShellEngine
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from langgraph.runtime import Runtime
    from acps_sdk.aip.aip_base_model import TaskResult

logger = logging.getLogger(__name__)

# 常量
DISCOVERY_URL = os.getenv("DISCOVERY_URL", "https://ioa.pub/discovery/acps-adp-v2/discover")
DEFAULT_LEADER_AIC = "default-aic"
MAX_DISCOVERY_RETRIES = 3
RETRY_DELAY = 3.0

TERMINAL_STATES = frozenset({"completed", "canceled", "failed", "rejected"})
WORKING_STATES = frozenset({"working", "accepted"})
AWAITING_INPUT_STATE = "awaiting-input"
AWAITING_COMPLETION_STATE = "awaiting-completion"
CONTINUABLE_STATES = frozenset({AWAITING_INPUT_STATE, AWAITING_COMPLETION_STATE})

# 补充状态
class DiscoveredAgent(TypedDict):
    """已发现的 Partner Agent 的 ACS 摘要。"""

    aic: str
    name: str
    description: str
    active: bool
    skills_summary: str
    endpoint_url: str
    protocol_version: str
    ranking: int

class AcpsState(AgentState):
    """ACPs 中间件的扩展 Agent 状态。"""

    acps_discovered_agents: NotRequired[dict[str, DiscoveredAgent]]
    """来自发现结果的 AIC -> DiscoveredAgent 映射。"""

    acps_task_cache: NotRequired[dict[str, dict]]
    """task_id -> 任务结果字典的映射。"""

class AcpsStateUpdate(TypedDict):
    """由 before_agent 或工具调用触发的部分状态更新。"""

    acps_discovered_agents: NotRequired[dict[str, DiscoveredAgent]]
    acps_task_cache: NotRequired[dict[str, dict]]

# 工具输入schema
class DiscoverSchema(BaseModel):
    """acps_discover 工具的输入 Schema。"""

    query: str = Field(
        description="所需 Agent 能力的自然语言描述。"
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="要返回的最大发现结果数量 (1-20)。",
    )
    include_inactive: bool = Field(
        default=False,
        description="是否包含非活跃状态的 Agent。默认为 False，仅返回活跃 Agent。",
    )

class StartTaskSchema(BaseModel):
    """acps_start 工具的输入 Schema。"""

    aic: str | None = Field(
        default=None,
        description="来自 acps_discover 结果的 Partner Agent AIC。如果提供了 url 则可选。"
    )
    url: str | None = Field(
        default=None,
        description=" Partner Agent 的直接 HTTP/HTTPS URL。优先级高于 AIC。"
    )
    content: str = Field(
        description="委派给 Partner Agent 的自然语言任务描述。"
    )

class GetTaskSchema(BaseModel):
    """acps_get 工具的输入 Schema。"""

    task_id: str = Field(
        description="acps_start 返回的任务 ID。"
    )
    poll: bool = Field(
        default=True,
        description=(
            "如果为 true，该工具将阻塞并持续轮询 Partner Agent，直到"
            "任务退出 'working'/'accepted' 状态。设置 poll=false 可进行单次"
            "即时查询。"
        ),
    )

class ContinueTaskSchema(BaseModel):
    """acps_continue 工具的输入 Schema。"""

    task_id: str = Field(
        description="等待输入的任务 ID。"
    )
    user_input: str = Field(
        description="用户对 Partner Agent 的回复或额外信息。"
    )

class CompleteTaskSchema(BaseModel):
    """acps_complete 工具的输入 Schema。"""

    task_id: str = Field(
        description="等待确认完成的任务 ID。"
    )

class CancelTaskSchema(BaseModel):
    """acps_cancel 工具的输入 Schema。"""

    task_id: str = Field(
        description="要取消的任务 ID。"
    )

class DelaySchema(BaseModel):
    """acps_delay 工具的输入 Schema。"""

    seconds: int = Field(
        description="延迟的秒数 (1-60)。",
        ge=1,
        le=60,
    )

# 工具描述
ACPS_DISCOVER_DESCRIPTION = (
    "通过 ADP 搜索 Partner Agent。"
    "返回已发现的 Partner Agent 及其能力列表。"
    "当用户需要为特定任务寻找合适的 Agent 时使用此工具。"
)

ACPS_START_DESCRIPTION = (
    "与 Partner Agent 启动一个新的 AIP 任务。"
    "需要 Partner Agent 的 AIC 或 url（源自 acps_discover 结果，优先填写 url）和任务描述。"
    "返回初始任务状态和 task_id。"
)

ACPS_GET_DESCRIPTION = (
    "查询（并可选地轮询）AIP 任务的当前状态。"
    "默认情况下会阻塞并持续轮询，直到任务离开 'working'/'accepted' 状态。"
    "设置 poll=false 可以进行单次查询。"
)

ACPS_CONTINUE_DESCRIPTION = (
    "向处于 'awaiting-input' 或 'awaiting-completion' 状态的任务提供用户输入。"
    "仅在 Partner Agent 提出澄清问题且用户提供了回答时有效。"
    "在调用此工具前，你可能需要向用户索要必要的信息。"
    "切勿尝试对非活动任务执行继续操作。"
)

ACPS_COMPLETE_DESCRIPTION = (
    "确认 Partner Agent 的交付成果，并将任务状态转移为 'completed'。"
    "仅在任务处于 'awaiting-completion' 状态时有效。"
    "请仅在将 Partner Agent 的产物展示给用户并获得明确确认后，再调用此工具。"
)

ACPS_CANCEL_DESCRIPTION = (
    "取消一个活动中的（非终态）任务。可在除 completed、canceled、failed 或 rejected 以外的任何状态下调用。"
    "当用户明确想要中止任务，或者拒绝 Partner Agent 的结果时使用。"
)

# 辅助工具
def _extract_endpoint_url(acs: dict) -> str:
    """从 ACS 文档中提取最佳 RPC 端点 URL。"""
    endpoints = acs.get("endPoints") or []
    for ep in endpoints:
        protocol = (ep.get("protocol") or "").lower()
        if "aip" in protocol or "rpc" in protocol:
            url = ep.get("url", "")
            if url:
                return url
    if endpoints:
        return endpoints[0].get("url", "")
    return ""

def _build_skills_summary(acs: dict) -> str:
    """将 ACS 文档中的所有技能概括为一个以竖线分隔的字符串。"""
    skills = acs.get("skills") or []
    parts = []
    for skill in skills:
        name = skill.get("name", "")
        desc = skill.get("description", "")
        if name or desc:
            parts.append(f"{name}: {desc}".strip(": "))
    return " | ".join(parts)

def _build_discovered_agent(acs: dict, ranking: int) -> DiscoveredAgent:
    """从原始 ACS 文档构建规范化的 DiscoveredAgent。"""
    return DiscoveredAgent(
        aic=acs.get("aic", ""),
        name=acs.get("name", ""),
        description=acs.get("description", ""),
        active=acs.get("active", True),
        skills_summary=_build_skills_summary(acs),
        endpoint_url=_extract_endpoint_url(acs),
        protocol_version=acs.get("protocolVersion", ""),
        ranking=ranking,
    )

async def _write_agent_to_cache(agent: DiscoveredAgent, discovered_agents_dir: Path) -> None:
    """将发现的 Agent 写入工作区中基于文件的缓存（异步通过 to_thread）。"""
    def _write() -> None:
        try:
            discovered_agents_dir.mkdir(parents=True, exist_ok=True)
            path = discovered_agents_dir / f"{agent['aic']}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"normalized_summary": agent}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to write agent cache: %s", e)

    await asyncio.to_thread(_write)

def _extract_message(result: Any) -> str:
    """从 AIP TaskResult 对象中提取人类可读的消息。"""
    try:
        items = result.status.dataItems or []
        for item in items:
            if hasattr(item, "text"):
                return item.text
    except Exception:
        pass
    return ""

def _extract_products(result: Any) -> list[dict[str, str]]:
    """从 AIP TaskResult 返回简洁的产品字典列表。"""
    products: list[dict[str, str]] = []
    try:
        for product in result.products or []:
            texts = []
            for item in product.dataItems or []:
                if hasattr(item, "text"):
                    texts.append(item.text)
            products.append({
                "id": product.id,
                "name": product.name or "",
                "content": " ".join(texts),
            })
    except Exception:
        pass
    return products

def _get_task_state_str(result: TaskResult) -> str:
    """从 AIP TaskResult 中提取状态字符串，并处理枚举。"""
    state = result.status.state
    return state.value if hasattr(state, "value") else str(state)

def _now_iso() -> str:
    """以 ISO 8601 字符串格式返回当前 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()



def _append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """向系统消息追加文本，必要时新建一条系统消息。"""
    if system_message is None:
        return SystemMessage(content=text)
    existing = system_message.content
    if isinstance(existing, str):
        return SystemMessage(content=f"{existing}\n\n{text}")
    # Handle list-of-blocks content
    from langchain_core.messages import ContentBlock
    new_content: list[ContentBlock] = list(system_message.content_blocks)
    if new_content:
        text = f"\n\n{text}"
    new_content.append({"type": "text", "text": text}) 
    return SystemMessage(content_blocks=new_content)

# === System prompt ===
ACPS_SYSTEM_PROMPT = """
## ACPs — Leader Agent 工具

### 可用工具

- acps_discover: 根据能力描述搜索 Partner Agent。
- acps_start: 向 Partner Agent 发起任务。
- acps_get: 轮询/等待 Partner Agent 的响应。
- acps_continue: 回复请求更多输入的 Partner Agent。
- acps_complete: 确认 Partner Agent 的交付成果并结束任务。
- acps_cancel: 取消当前活动的任务。

### 使用指南

1. **直接交互（已知 URL）：**
   如果你已经通过其他渠道（例如用户提供或外部平台）获取了 Partner Agent 的端点 URL，你可以直接与其进行交互。在调用 acps_start 时直接将该 URL 传入 url 或 aic 参数，从而跳过发现步骤。

2. **明确的发现请求：**
   如果用户明确要求寻找或发现特定类型的 Agent，优先查看本地已发现智能体的缓存，如果本地缓存没有匹配项，使用 acps_discover 根据用户的需求搜索合适的候选者。

3. **委派无法解决的任务（需要授权）：**
   如果用户指派的任务超出了你内置的能力范围，你可以考虑将其委派给 Partner Agent。但是，你必须在调用 acps_start 委派任务前，明确向用户请求许可。在未获得用户明确同意的情况下，不要自主进行委派。

### 本地缓存

在进行智能体发现和任务管理时，你可以读取和写入本地缓存文件，它们保存在以下路径中：
- 本地缓存总目录：`{acps_dir}`
- 已发现智能体的缓存目录：`{acps_dir}/discovered/`，每个已发现的智能体以其 AIC 命名的 JSON 文件形式保存在这里。
- 任务（AIP Tasks）缓存目录：`{acps_dir}/tasks/active/`（存放活跃中的任务信息）与 `{acps_dir}/tasks/inactive/`（存放已结束或取消的任务信息）。

### 交互流程
调用 acps_start 后：
- **即时响应：** 如果 acps_start 返回了 is_terminal=True 或等待状态，直接读取结果即可。除非任务仍处于进行中（working），否则你不需要调用 acps_get。
- **超时异常：** 如果 acps_start 因超时而失败，该任务可能仍在 Partner Agent 端运行。你必须使用 acps_get 并传入 task_id 来轮询任务状态。
- **其他错误：** 如果 acps_start 因无法连接或其他错误而失败，切勿调用 acps_get。
- **等待输入：** 如果状态变为 awaiting-input，使用 acps_continue 提供所需的信息。
- **等待完成：** 如果状态变为 awaiting-completion，审查交付物并使用 acps_complete 接受或使用 acps_cancel 拒绝。
"""


# 中间件类
class AcpsMiddleware(AgentMiddleware[AcpsState, ContextT, ResponseT]):
    """
    params:
        discovery_url: ADP 发现服务器的 URL。
        leader_aic: 当前Leader Agent 的 AIC。
        http_timeout: 发现及 AIP 调用的 HTTP 请求超时时间（秒）。
    """

    state_schema = AcpsState

    def __init__(
        self,
        engine: BaseShellEngine,
        workspace_root: str,
        *,
        leader_aic: str = DEFAULT_LEADER_AIC,
        http_timeout: float = 120.0,
    ) -> None:
        self._engine = engine
        self._workspace_root = workspace_root
        self._discovery_url = DISCOVERY_URL
        self._leader_aic = leader_aic
        self._http_timeout = http_timeout

        self.acps_dir = Path(self._workspace_root) / "acps"
        self.tasks_dir = self.acps_dir / "tasks"
        self.discovered_agents_dir = self.acps_dir / "discovered"

        # Build all tools at initialization time
        self.tools: list[BaseTool] = [
            self._create_discover_tool(),
            self._create_start_task_tool(),
            self._create_get_task_tool(),
            self._create_continue_task_tool(),
            self._create_complete_task_tool(),
            self._create_cancel_task_tool(),
            self._create_delay_tool(),
        ]


    @staticmethod
    def _get_discovered_agents(state: AcpsState) -> dict[str, DiscoveredAgent]:
        """从状态中获取已发现 Agent 的映射，默认为空。"""
        return state.get("acps_discovered_agents", {})


    def _load_all_tasks_from_disk(self) -> dict[str, dict]:
        """扫描 active/ 和 inactive/ 任务目录并返回合并后的字典。"""
        tasks: dict[str, dict] = {}
        tasks_dir = self.tasks_dir
        for subdir in ["active", "inactive"]:
            directory = tasks_dir / subdir
            if not directory.exists():
                continue
            for path in directory.iterdir():
                if not path.is_file() or path.suffix != ".json":
                    continue
                task_id = path.stem
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    data["_cache_location"] = subdir  # active or inactive
                    tasks[task_id] = data
                except Exception as e:
                    logger.warning("Failed to load task %s: %s", path, e)
        return tasks

    def before_agent(
        self,
        state: AcpsState,
        runtime: Runtime[Any],
    ) -> AcpsStateUpdate | None:
        """初始化并刷新 ACPs 状态字段。

        从磁盘加载最新的任务缓存，以便将其与已发现的 Agent 一起
        持久化到 SQLite 检查点保存器中。

        参数:
            state: 当前 Agent 状态。
            runtime: 运行时上下文。

        返回:
            包含 ACPS 字段的状态更新。
        """
        update: AcpsStateUpdate = {}
        if "acps_discovered_agents" not in state:
            update["acps_discovered_agents"] = {}

        # Always reload task cache from disk to pick up changes from the
        # previous turn's tool calls, then persist via the state saver.
        try:
            update["acps_task_cache"] = self._load_all_tasks_from_disk()
        except Exception as e:
            logger.warning("Failed to load task cache from disk: %s", e)
            if "acps_task_cache" not in state:
                update["acps_task_cache"] = {}

        return update if update else None

    async def abefore_agent(
        self,
        state: AcpsState,
        runtime: Runtime[Any],
    ) -> AcpsStateUpdate | None:
        """before_agent 的异步版本。"""
        return self.before_agent(state, runtime)

    def _modify_request(
        self, request: ModelRequest[ContextT]
    ) -> ModelRequest[ContextT]:
        """将 ACPS 协议指令注入到系统提示词中。

        参数:
            request: 要修改的模型请求。

        返回:
            在系统消息中带有 ACPS 指令的新模型请求。
        """
        acps_dir = self.acps_dir
        prompt = ACPS_SYSTEM_PROMPT.replace("{acps_dir}", str(acps_dir))

        try:
            active_dir = self.tasks_dir / "active"
            if active_dir.exists():
                active_task_ids = [f.stem for f in active_dir.iterdir() if f.is_file() and f.suffix == ".json"]
                if active_task_ids:
                    task_lines = [f"  - {tid}" for tid in active_task_ids]
                    active_summary = (
                        "\n\n### 当前跟踪的任务\n\n"
                        "你当前有以下处于活动状态的 AIP 任务。\n"
                        f"你可以在 {acps_dir}/tasks/active/ 目录中查看它们的详细信息。\n"
                        + "\n".join(task_lines)
                    )
                    prompt = prompt + active_summary
        except Exception as e:
            logger.warning("Failed to scan active tasks directory: %s", e)

        new_system_message = _append_to_system_message(
            request.system_message, prompt
        )
        return request.override(system_message=new_system_message)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """将 ACPS 系统提示词注入到模型请求中（同步）。

        参数:
            request: 正在处理的模型请求。
            handler: 用修改后的请求调用的处理函数。

        返回:
            来自处理器的模型响应。
        """
        modified_request = self._modify_request(request)
        return handler(modified_request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """将 ACPS 系统提示词注入到模型请求中（异步）。

        参数:
            request: 正在处理的模型请求。
            handler: 用修改后的请求调用的异步处理函数。

        返回:
            来自处理器的模型响应。
        """
        modified_request = self._modify_request(request)
        return await handler(modified_request)

    def _create_discover_tool(self) -> BaseTool:
        """创建用于 Agent 发现的 acps_discover 工具。"""

        discovery_url = self._discovery_url
        http_timeout = self._http_timeout

        def sync_discover(
            query: str,
            limit: int = 5,
            include_inactive: bool = False,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_discover(
            query: str,
            limit: int = 5,
            include_inactive: bool = False,
        ) -> str:
            """异步 ADP 发现。"""
            return await _run_discovery_async(
                query=query,
                limit=limit,
                include_inactive=include_inactive,
                discovery_url=discovery_url,
                http_timeout=http_timeout,
                discovered_agents_dir=self.discovered_agents_dir,
            )
        return StructuredTool.from_function(
            name="acps_discover",
            description=ACPS_DISCOVER_DESCRIPTION,
            func=sync_discover,
            coroutine=async_discover,
            infer_schema=False,
            args_schema=DiscoverSchema,
        )

    def _create_start_task_tool(self) -> BaseTool:
        """创建用于启动 AIP 任务的 acps_start 工具。"""

        leader_aic = self._leader_aic
        http_timeout = self._http_timeout

        def sync_start(
            content: str,
            aic: str | None = None,
            url: str | None = None,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_start(
            content: str,
            aic: str | None = None,
            url: str | None = None,
        ) -> str:
            """异步 AIP 启动任务。"""
            def _do_start():
                return _run_start_task_sync(
                    aic=aic,
                    url=url,
                    content=content,
                    session_id=f"session-{uuid.uuid4()}",
                    task_id=f"task-{uuid.uuid4()}",
                    leader_aic=leader_aic,
                    http_timeout=http_timeout,
                    tasks_dir=self.tasks_dir,
                    discovered_agents_dir=self.discovered_agents_dir,
                )
            return await asyncio.to_thread(_do_start)
        return StructuredTool.from_function(
            name="acps_start",
            description=ACPS_START_DESCRIPTION,
            func=sync_start,
            coroutine=async_start,
            infer_schema=False,
            args_schema=StartTaskSchema,
        )

    def _create_get_task_tool(self) -> BaseTool:
        """创建用于查询/轮询 AIP 任务状态的 acps_get 工具。"""

        leader_aic = self._leader_aic
        http_timeout = self._http_timeout

        def sync_get(
            task_id: str,
            poll: bool = True,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_get(
            task_id: str,
            poll: bool = True,
        ) -> str:
            """异步 AIP 获取/轮询任务。"""
            def _do_get():
                return _run_get_task_sync(
                    task_id=task_id,
                    leader_aic=leader_aic,
                    poll=poll,
                    http_timeout=http_timeout,
                    tasks_dir=self.tasks_dir,
                    discovered_agents_dir=self.discovered_agents_dir,
                )
            return await asyncio.to_thread(_do_get)
        return StructuredTool.from_function(
            name="acps_get",
            description=ACPS_GET_DESCRIPTION,
            func=sync_get,
            coroutine=async_get,
            infer_schema=False,
            args_schema=GetTaskSchema,
        )

    def _create_continue_task_tool(self) -> BaseTool:
        """创建用于提供用户输入的 acps_continue 工具。"""

        leader_aic = self._leader_aic
        http_timeout = self._http_timeout

        def sync_continue(
            task_id: str,
            user_input: str,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_continue(
            task_id: str,
            user_input: str,
        ) -> str:
            """异步 AIP 继续任务。"""
            def _do_continue():
                return _run_continue_task_sync(
                    task_id=task_id,
                    user_input=user_input,
                    leader_aic=leader_aic,
                    http_timeout=http_timeout,
                    tasks_dir=self.tasks_dir,
                    discovered_agents_dir=self.discovered_agents_dir,
                )
            return await asyncio.to_thread(_do_continue)
        return StructuredTool.from_function(
            name="acps_continue",
            description=ACPS_CONTINUE_DESCRIPTION,
            func=sync_continue,
            coroutine=async_continue,
            infer_schema=False,
            args_schema=ContinueTaskSchema,
        )

    def _create_complete_task_tool(self) -> BaseTool:
        """创建用于确认交付成果的 acps_complete 工具。"""

        leader_aic = self._leader_aic
        http_timeout = self._http_timeout

        def sync_complete(
            task_id: str,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_complete(
            task_id: str,
        ) -> str:
            """异步 AIP 完成任务。"""
            def _do_complete():
                return _run_complete_task_sync(
                    task_id=task_id,
                    leader_aic=leader_aic,
                    http_timeout=http_timeout,
                    tasks_dir=self.tasks_dir,
                    discovered_agents_dir=self.discovered_agents_dir,
                )
            return await asyncio.to_thread(_do_complete)
        return StructuredTool.from_function(
            name="acps_complete",
            description=ACPS_COMPLETE_DESCRIPTION,
            func=sync_complete,
            coroutine=async_complete,
            infer_schema=False,
            args_schema=CompleteTaskSchema,
        )

    def _create_cancel_task_tool(self) -> BaseTool:
        """创建用于取消活动中任务的 acps_cancel 工具。"""
        
        leader_aic = self._leader_aic
        http_timeout = self._http_timeout

        def sync_cancel(
            task_id: str,
        ) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_cancel(
            task_id: str,
        ) -> str:
            """异步 AIP 取消任务。"""
            def _do_cancel():
                return _run_cancel_task_sync(
                    task_id=task_id,
                    leader_aic=leader_aic,
                    http_timeout=http_timeout,
                    tasks_dir=self.tasks_dir,
                    discovered_agents_dir=self.discovered_agents_dir,
                )
            return await asyncio.to_thread(_do_cancel)
        return StructuredTool.from_function(
            name="acps_cancel",
            description=ACPS_CANCEL_DESCRIPTION,
            func=sync_cancel,
            coroutine=async_cancel,
            infer_schema=False,
            args_schema=CancelTaskSchema,
        )

    def _create_delay_tool(self) -> BaseTool:
        """创建用于等待的 acps_delay 工具。"""
        
        description = (
            "在继续之前等待指定的秒数 (1-60秒)。"
            "当你需要等待 Partner Agent 完成任务而又不想进行主动轮询时使用此工具。"
        )

        def sync_delay(seconds: int) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_delay(seconds: int) -> str:
            """异步延迟。"""
            def _do_delay():
                time.sleep(seconds)
                return f"已等待 {seconds} 秒。"
            return await asyncio.to_thread(_do_delay)
        return StructuredTool.from_function(
            name="acps_delay",
            description=description,
            func=sync_delay,
            coroutine=async_delay,
            infer_schema=False,
            args_schema=DelaySchema,
        )

async def _run_discovery_async(
    *,
    query: str,
    limit: int,
    include_inactive: bool,
    discovery_url: str,
    http_timeout: float,
    discovered_agents_dir: Path,
) -> str:
    """异步 ADP 发现"""
    last_error: str | None = None

    payload: dict = {
        "query": query,
        "limit": limit,
        "type": "explicit",
    }
    if include_inactive:
        payload["filter"] = {
            "conditions": [
                {"field": "onlyAvailable", "op": "eq", "value": False}
            ]
        }

    for attempt in range(MAX_DISCOVERY_RETRIES):
        try:
            async with httpx.AsyncClient(verify=False, timeout=http_timeout) as client:
                response = await client.post(
                    discovery_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
            break
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_error = str(e)
            logger.warning(
                "Discovery attempt %d/%d failed: %s",
                attempt + 1, MAX_DISCOVERY_RETRIES, last_error,
            )
            if attempt < MAX_DISCOVERY_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY)
            continue
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status >= 500 and attempt < MAX_DISCOVERY_RETRIES - 1:
                last_error = f"HTTP {status}"
                logger.warning(
                    "Discovery server error %d, retrying (%d/%d)...",
                    status, attempt + 1, MAX_DISCOVERY_RETRIES,
                )
                await asyncio.sleep(RETRY_DELAY)
                continue
            return json.dumps({
                "success": False,
                "error": f"HTTP {status} from discovery server",
                "error_type": "discovery_error",
            })
    else:
        return json.dumps({
            "success": False,
            "error": f"Discovery server unreachable after {MAX_DISCOVERY_RETRIES} attempts: {last_error}",
            "error_type": "discovery_error",
        })

    return await _parse_discovery_response(response.json(), query, discovered_agents_dir)

async def _parse_discovery_response(data: dict, query: str, discovered_agents_dir: Path) -> str:
    """将发现服务器返回的原始 JSON 响应解析为精简摘要，并将摘要缓存到worpspace。

    参数:
        data: 来自发现服务器的原始 JSON 响应。
        query: 用于错误信息的原始查询字符串。

    返回:
        包含发现结果的 JSON 字符串。
    """
    result = data.get("result") or {}
    acs_map = result.get("acsMap") or {}

    # Collect and rank agent skills
    agent_skills: list[dict] = []
    for group in result.get("agents") or []:
        for skill in group.get("agentSkills") or []:
            agent_skills.append(skill)
    agent_skills.sort(key=lambda s: s.get("ranking", 999))

    if not agent_skills:
        return json.dumps({
            "success": False,
            "error": f"No agents found for query: {query}",
            "error_type": "discovery_error",
        })

    # Build slim summaries
    summaries: list[dict] = []
    for skill_entry in agent_skills:
        aic = skill_entry.get("aic", "")
        ranking = skill_entry.get("ranking", 99)
        acs = acs_map.get(aic)
        if not isinstance(acs, dict):
            continue
        agent_summary = _build_discovered_agent(acs, ranking)
        summaries.append(dict(agent_summary))
        await _write_agent_to_cache(agent_summary, discovered_agents_dir)

    return json.dumps({
        "success": True,
        "summary": f"Discovered {len(summaries)} agent(s) for query: {query}",
        "data": {
            "agents": summaries,
            "total": len(summaries),
        },
    }, ensure_ascii=False, indent=2)

def _resolve_partner_url(aic: str, discovered_agents_dir: Path) -> tuple[str, str]:
    """从本地 ACS 缓存解析 Partner Agent 的 RPC 端点 URL，或使用直接 URL/AIC。"""
    if aic.startswith("http://") or aic.startswith("https://"):
        return aic, ""

    # Try to read from the workspace discovered directory
    cache_path = discovered_agents_dir / f"{aic}.json"
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
                summary = data.get("normalized_summary") or {}
                url = summary.get("endpoint_url") or summary.get("endpoint")
                if url:
                    return url, ""
        except Exception as e:
            logger.warning("Failed to read agent cache %s: %s", cache_path, e)

    return "", f"ACS cache not found for aic: {aic}. Run acps_discover first."

def _run_start_task_sync(
    *,
    aic: str | None = None,
    url: str | None = None,
    content: str,
    session_id: str,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """同步 AIP 启动任务实现。"""
    if url:
        partner_url = url
    elif aic:
        partner_url, error = _resolve_partner_url(aic, discovered_agents_dir)
        if error:
            return json.dumps({
                "success": False,
                "error": error,
                "error_type": "cache_miss",
            })
    else:
        return json.dumps({
            "success": False,
            "error": "Must specify either 'aic' or 'url'.",
            "error_type": "invalid_argument",
        })

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed. Install it with: pip install acps-sdk",
            "error_type": "dependency_error",
        })


    async def _inner() -> dict:
        client = AipRpcClient(
            partner_url=partner_url,
            leader_id=leader_aic,
        )
        try:
            result = await client.start_task(
                session_id, content, task_id=task_id
            )
            _write_task_cache(task_id, result, tasks_dir, {"aic": aic, "partner_url": partner_url, "session_id": session_id})
        except httpx.TimeoutException as e:
            return {
                "success": False,
                "error": str(e) or repr(e),
                "error_type": "timeout",
                "hint": (
                    "任务已发送，但 Partner Agent 在响应时超时。 "
                    "任务可能仍在运行。请使用 acps_get 轮询任务状态，或使用 acps_delay 等待任务完成。"
                ),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e) or repr(e),
                "error_type": "http_error",
                "hint": (
                    "无法连接到 Partner Agent，或者发生错误。 "
                    "请勿对失败的 acps_start 返回的 task_id 调用 acps_get。"
                ),
            }
        finally:
            await client.close()

        state = _get_task_state_str(result)
        message = _extract_message(result)
        products = _extract_products(result)

        return {
            "success": True,
            "task_id": task_id,
            "state": state,
            "message": message,
            "products": products,
            "is_terminal": state in TERMINAL_STATES,
            "needs_input": state == AWAITING_INPUT_STATE,
            "awaiting_completion": state == AWAITING_COMPLETION_STATE,
            "aic": aic,
            "partner_url": partner_url,
            "session_id": session_id,
        }

    result_dict = asyncio.run(_inner())
    return json.dumps(result_dict, ensure_ascii=False, indent=2)

async def _run_start_task_async(
    *,
    aic: str | None = None,
    url: str | None = None,
    content: str,
    session_id: str,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """异步 AIP 启动任务实现。"""
    if url:
        partner_url = url
    elif aic:
        partner_url, error = _resolve_partner_url(aic, discovered_agents_dir)
        if error:
            return json.dumps({
                "success": False,
                "error": error,
                "error_type": "cache_miss",
            })
    else:
        return json.dumps({
            "success": False,
            "error": "Must specify either 'aic' or 'url'.",
            "error_type": "invalid_argument",
        })

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed. Install it with: pip install acps-sdk",
            "error_type": "dependency_error",
        })

    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=leader_aic,
    )
    try:
        result = await client.start_task(
            session_id, content, task_id=task_id
        )
        _write_task_cache(task_id, result, tasks_dir, {"aic": aic, "partner_url": partner_url, "session_id": session_id})
    except httpx.TimeoutException as e:
        return json.dumps({
            "success": False,
            "error": str(e) or repr(e),
            "error_type": "timeout",
            "hint": (
                "任务已发送，但 Partner Agent 在响应时超时。 "
                "任务可能仍在运行。请使用 acps_get 轮询任务状态。"
            ),
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e) or repr(e),
            "error_type": "http_error",
            "hint": (
                "无法连接到 Partner Agent，或者发生错误。 "
                "请勿对失败的 acps_start 返回的 task_id 调用 acps_get。"
            ),
        })
    finally:
        await client.close()

    state = _get_task_state_str(result)
    message = _extract_message(result)
    products = _extract_products(result)

    res_dict = {
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
        "products": products,
        "is_terminal": state in TERMINAL_STATES,
        "needs_input": state == AWAITING_INPUT_STATE,
        "awaiting_completion": state == AWAITING_COMPLETION_STATE,
        "aic": aic,
        "partner_url": partner_url,
        "session_id": session_id,
    }
    return json.dumps(res_dict, ensure_ascii=False, indent=2)

async def _query_task_once(
    task_id: str,
    partner_url: str,
    session_id: str,
    leader_aic: str,
    tasks_dir: Path,
) -> dict:
    """向 Partner Agent 发送单次 AIP Get RPC 请求并返回规范化字典。"""
    from acps_sdk.aip.aip_rpc_client import AipRpcClient

    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=leader_aic,
    )
    try:
        result = await client.get_task(task_id, session_id)
        _write_task_cache(task_id, result, tasks_dir)
    except Exception as e:
        return {
            "success": False,
            "error": str(e) or repr(e),
            "error_type": "http_error",
            "task_id": task_id,
        }
    finally:
        await client.close()

    state = _get_task_state_str(result)
    message = _extract_message(result)
    products = _extract_products(result)

    return {
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
        "needs_input": state == AWAITING_INPUT_STATE,
        "awaiting_completion": state == AWAITING_COMPLETION_STATE,
        "is_terminal": state in TERMINAL_STATES,
        "products": products,
    }

def _write_task_cache(task_id: str, result: TaskResult, tasks_dir: Path, cache_meta: dict | None = None) -> None:
    """将任务状态写入工作区中的本地 active/inactive 目录。"""
    try:
        from acps_sdk.aip.aip_base_model import TaskResult
        if not isinstance(result, TaskResult):
            return

        state = result.status.state
        state_str = state.value if hasattr(state, "value") else str(state)
        
        active_dir = tasks_dir / "active"
        inactive_dir = tasks_dir / "inactive"
        
        active_dir.mkdir(parents=True, exist_ok=True)
        inactive_dir.mkdir(parents=True, exist_ok=True)
        
        active_path = active_dir / f"{task_id}.json"
        inactive_path = inactive_dir / f"{task_id}.json"
        
        is_terminal = state_str in TERMINAL_STATES
        target_path = inactive_path if is_terminal else active_path
        cleanup_path = active_path if is_terminal else inactive_path
        
        data = result.model_dump(exclude_none=True)
        old_cache = _load_task_cache(task_id, tasks_dir) or {}
        for k in ["partner_url", "aic"]:
            if k in old_cache:
                data[k] = old_cache[k]
        if cache_meta:
            data.update(cache_meta)
            
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        if cleanup_path.exists():
            cleanup_path.unlink()
            
    except Exception as e:
        logger.warning("Failed to write task cache: %s", e)

def _load_task_cache(task_id: str, tasks_dir: Path) -> dict | None:
    """从工作区本地基于文件的缓存中加载任务状态。"""
    from pathlib import Path
    import json
    import logging
    
    for subdir in ["active", "inactive"]:
        path = tasks_dir / subdir / f"{task_id}.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    if "status" in data and "state" in data["status"]:
                        data["state"] = data["status"]["state"]
                    if "sessionId" in data:
                        data["session_id"] = data["sessionId"]
                    return data
            except Exception as e:
                logging.getLogger(__name__).warning("Failed to read task cache %s: %s", path, e)
    return None

def _run_get_task_sync(
    *,
    task_id: str,
    leader_aic: str,
    poll: bool,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """同步 AIP 获取/轮询任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}. Did you call acps_start first?",
            "error_type": "cache_miss",
        })
        
    partner_url = cache.get("partner_url", "")
    if not partner_url:
        cached_aic = cache.get("aic", "")
        if cached_aic:
            partner_url, _ = _resolve_partner_url(cached_aic, discovered_agents_dir)
            
    if not partner_url:
        return json.dumps({
            "success": False,
            "error": f"Partner URL not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    session_id = cache.get("session_id", "")
    if not session_id:
        return json.dumps({
            "success": False,
            "error": f"Session ID not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    cached_state = cache.get("state", "")

    # start_task failed — partner never saw this task
    if cached_state == "error":
        return json.dumps({
            "success": False,
            "error": cache.get("last_result", {}).get("error", "Task failed to start."),
            "error_type": "task_start_failed",
            "task_id": task_id,
            "state": "error",
            "hint": "在调用 acps_start 时无法连接到 Partner Agent。请尝试再次调用 acps_start。",
        })

    # Already terminal locally — no need to query partner
    if cached_state in TERMINAL_STATES:
        return json.dumps({
            "success": True,
            "task_id": task_id,
            "state": cached_state,
            "message": "(restored from local cache)",
            "needs_input": False,
            "awaiting_completion": False,
            "is_terminal": True,
            "products": [],
            "from_cache": True,
        })


    async def _inner() -> dict:
        attempts = 0

        while True:
            attempts += 1
            res = await _query_task_once(
                task_id, partner_url, session_id, leader_aic, tasks_dir
            )

            if not res["success"]:
                return res

            state = res["state"]
            
            if state not in WORKING_STATES:
                res["poll_attempts"] = attempts
                return res

            if not poll:
                if attempts >= 3:
                    res["poll_attempts"] = attempts
                    return res
                await asyncio.sleep(10.0)
            else:
                await asyncio.sleep(5.0)

    result_dict = asyncio.run(_inner())
    return json.dumps(result_dict, ensure_ascii=False, indent=2)

async def _run_get_task_async(
    *,
    task_id: str,
    leader_aic: str,
    poll: bool,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """异步 AIP 获取/轮询任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}. Did you call acps_start first?",
            "error_type": "cache_miss",
        })
        
    partner_url = cache.get("partner_url", "")
    if not partner_url:
        cached_aic = cache.get("aic", "")
        if cached_aic:
            partner_url, _ = _resolve_partner_url(cached_aic, discovered_agents_dir)
            
    if not partner_url:
        return json.dumps({
            "success": False,
            "error": f"Partner URL not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    session_id = cache.get("session_id", "")
    if not session_id:
        return json.dumps({
            "success": False,
            "error": f"Session ID not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    cached_state = cache.get("state", "")

    if cached_state == "error":
        return json.dumps({
            "success": False,
            "error": cache.get("last_result", {}).get("error", "Task failed to start."),
            "error_type": "task_start_failed",
            "task_id": task_id,
            "state": "error",
            "hint": "在调用 acps_start 时无法连接到 Partner Agent。请尝试再次调用 acps_start。",
        })

    if cached_state in TERMINAL_STATES:
        return json.dumps({
            "success": True,
            "task_id": task_id,
            "state": cached_state,
            "message": "(restored from local cache)",
            "needs_input": False,
            "awaiting_completion": False,
            "is_terminal": True,
            "products": [],
            "from_cache": True,
        })


    attempts = 0

    while True:
        attempts += 1
        res = await _query_task_once(
            task_id, partner_url, session_id, leader_aic, tasks_dir
        )

        if not res["success"]:
            return json.dumps(res, ensure_ascii=False, indent=2)

        state = res["state"]
        
        if state not in WORKING_STATES:
            res["poll_attempts"] = attempts
            return json.dumps(res, ensure_ascii=False, indent=2)

        if not poll:
            if attempts >= 3:
                res["poll_attempts"] = attempts
                return json.dumps(res, ensure_ascii=False, indent=2)
            await asyncio.sleep(10.0)
        else:
            await asyncio.sleep(5.0)

def _run_continue_task_sync(
    *,
    task_id: str,
    user_input: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """同步 AIP 继续任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}.",
            "error_type": "cache_miss",
        })
        
    partner_url = cache.get("partner_url", "")
    if not partner_url:
        cached_aic = cache.get("aic", "")
        if cached_aic:
            partner_url, _ = _resolve_partner_url(cached_aic, discovered_agents_dir)
            
    if not partner_url:
        return json.dumps({
            "success": False,
            "error": f"Partner URL not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    session_id = cache.get("session_id", "")
    if not session_id:
        return json.dumps({
            "success": False,
            "error": f"Session ID not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    current_state = cache.get("state", "")
    if current_state not in CONTINUABLE_STATES:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is in state '{current_state}', "
                f"which does not accept continue. "
                f"Continue is only valid from: {sorted(CONTINUABLE_STATES)}"
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })


    async def _inner() -> dict:
        client = AipRpcClient(
            partner_url=partner_url,
            leader_id=leader_aic,
        )
        try:
            result = await client.continue_task(task_id, session_id, user_input)
            _write_task_cache(task_id, result, tasks_dir)
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "http_error",
                "task_id": task_id,
            }
        finally:
            await client.close()

        state = _get_task_state_str(result)
        message = _extract_message(result)
        return {
            "success": True,
            "task_id": task_id,
            "state": state,
            "message": message,
        }

    result_dict = asyncio.run(_inner())
    return json.dumps(result_dict, ensure_ascii=False, indent=2)

async def _run_continue_task_async(
    *,
    task_id: str,
    user_input: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """异步 AIP 继续任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}.",
            "error_type": "cache_miss",
        })
        
    partner_url = cache.get("partner_url", "")
    if not partner_url:
        cached_aic = cache.get("aic", "")
        if cached_aic:
            partner_url, _ = _resolve_partner_url(cached_aic, discovered_agents_dir)
            
    if not partner_url:
        return json.dumps({
            "success": False,
            "error": f"Partner URL not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    session_id = cache.get("session_id", "")
    if not session_id:
        return json.dumps({
            "success": False,
            "error": f"Session ID not found in cache for task_id: {task_id}.",
            "error_type": "invalid_argument",
        })

    current_state = cache.get("state", "")
    if current_state not in CONTINUABLE_STATES:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is in state '{current_state}', "
                f"which does not accept continue. "
                f"Continue is only valid from: {sorted(CONTINUABLE_STATES)}"
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })

    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=leader_aic,
    )
    try:
        result = await client.continue_task(task_id, session_id, user_input)
        _write_task_cache(task_id, result, tasks_dir)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": "http_error",
            "task_id": task_id,
        })
    finally:
        await client.close()

    state = _get_task_state_str(result)
    message = _extract_message(result)

    return json.dumps({
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
    }, ensure_ascii=False, indent=2)

def _run_complete_task_sync(
    *,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """同步 AIP 完成任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        })

    current_state = cache.get("state", "")
    if current_state != AWAITING_COMPLETION_STATE:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is in state '{current_state}'. "
                f"complete is only valid from '{AWAITING_COMPLETION_STATE}'."
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })


    async def _inner() -> dict:
        client = AipRpcClient(
            partner_url=partner_url,
            leader_id=leader_aic,
        )
        try:
            result = await client.complete_task(task_id, session_id)
            _write_task_cache(task_id, result, tasks_dir)
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "http_error",
                "task_id": task_id,
            }
        finally:
            await client.close()

        state = _get_task_state_str(result)
        message = _extract_message(result)
        return {
            "success": True,
            "task_id": task_id,
            "state": state,
            "message": message,
        }

    result_dict = asyncio.run(_inner())
    return json.dumps(result_dict, ensure_ascii=False, indent=2)

async def _run_complete_task_async(
    *,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """异步 AIP 完成任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        })

    current_state = cache.get("state", "")
    if current_state != AWAITING_COMPLETION_STATE:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is in state '{current_state}'. "
                f"complete is only valid from '{AWAITING_COMPLETION_STATE}'."
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })

    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=leader_aic,
    )
    try:
        result = await client.complete_task(task_id, session_id)
        _write_task_cache(task_id, result, tasks_dir)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": "http_error",
            "task_id": task_id,
        })
    finally:
        await client.close()

    state = _get_task_state_str(result)
    message = _extract_message(result)

    return json.dumps({
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
    }, ensure_ascii=False, indent=2)

def _run_cancel_task_sync(
    *,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """同步 AIP 取消任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        })

    current_state = cache.get("state", "")
    if current_state in TERMINAL_STATES:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is already in terminal state '{current_state}'. "
                f"Cannot cancel a terminated task."
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })


    async def _inner() -> dict:
        client = AipRpcClient(
            partner_url=partner_url,
            leader_id=leader_aic,
        )
        try:
            result = await client.cancel_task(task_id, session_id)
            _write_task_cache(task_id, result, tasks_dir)
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "http_error",
                "task_id": task_id,
            }
        finally:
            await client.close()

        state = _get_task_state_str(result)
        message = _extract_message(result)
        return {
            "success": True,
            "task_id": task_id,
            "state": state,
            "message": message,
        }

    result_dict = asyncio.run(_inner())
    return json.dumps(result_dict, ensure_ascii=False, indent=2)

async def _run_cancel_task_async(
    *,
    task_id: str,
    leader_aic: str,
    http_timeout: float,
    tasks_dir: Path,
    discovered_agents_dir: Path,
) -> str:
    """异步 AIP 取消任务实现。"""
    cache = _load_task_cache(task_id, tasks_dir)
    if cache is None:
        return json.dumps({
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        })

    current_state = cache.get("state", "")
    if current_state in TERMINAL_STATES:
        return json.dumps({
            "success": False,
            "error": (
                f"Task {task_id} is already in terminal state '{current_state}'. "
                f"Cannot cancel a terminated task."
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        })

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    try:
        from acps_sdk.aip.aip_rpc_client import AipRpcClient
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "acps_sdk is not installed.",
            "error_type": "dependency_error",
        })

    client = AipRpcClient(
        partner_url=partner_url,
        leader_id=leader_aic,
    )
    try:
        result = await client.cancel_task(task_id, session_id)
        _write_task_cache(task_id, result, tasks_dir)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": "http_error",
            "task_id": task_id,
        })
    finally:
        await client.close()

    state = _get_task_state_str(result)
    message = _extract_message(result)

    return json.dumps({
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
    }, ensure_ascii=False, indent=2)

__all__ = [
    "AcpsMiddleware",
    "AcpsState",
    "AcpsStateUpdate",
    "DiscoveredAgent",
]
