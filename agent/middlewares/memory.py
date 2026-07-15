"""内存中间件。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from agent.middlewares.utils.memory_types import (
    MEMORY_TYPES,
    MemoryType,
    build_memory_system_prompt,
)
from agent.shell import BaseShellEngine

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


# === Constants ===
MEMORY_DIR_NAME = "memory"
MEMORY_INDEX_FILE = "MEMORY.md"
MIN_NEW_MESSAGES_FOR_EXTRACTION = 4

# === Tool schemas ===
class MemorizeSchema(BaseModel):
    """`memorize` 工具的输入模式。"""

    category: MemoryType = Field(
        description=(
            f"记忆类别。必须是以下之一：{', '.join(MEMORY_TYPES)}。"
            "为该信息选择最合适的类别。"
        ),
    )
    title: str = Field(
        description=(
            "记忆的简短描述性标题（例如 'hobbies'、"
            "'meeting_schedule'、'dietary_preferences'）。用作文件名。"
        ),
    )
    content: str = Field(
        description=(
            "要保存的记忆内容。对于 interaction_rules 和 active_context，"
            "结构为：规则/事实，然后是 **Why:** 和 **How to apply:** 行。"
        ),
    )


class RecallSchema(BaseModel):
    """`recall` 工具的输入模式。"""

    query: str = Field(
        description="用于查找相关记忆的搜索查询。",
    )

# === State ===
class MemoryState(AgentState):
    """扩展了记忆跟踪的 Agent 状态。"""

    last_memory_cursor: NotRequired[Annotated[int, PrivateStateAttr]]
    """消息列表中的索引 —— 提取操作将从此点向前扫描。"""

    memory_index_content: NotRequired[Annotated[str, PrivateStateAttr]]
    """用于注入的 MEMORY.md 缓存内容。"""

class MemoryStateUpdate(TypedDict):
    """来自记忆操作的部分状态更新。"""

    last_memory_cursor: int
    memory_index_content: str


# === Utils ===
def _sanitize_filename(name: str) -> str:
    """净化字符串以用作文件名。"""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name)
    return sanitized[:64] or "untitled"

def _count_model_visible_messages(
    messages: list[BaseMessage],
    since_index: int = 0,
) -> int:
    """统计自给定索引以来对模型可见的消息数。"""
    count = 0
    for msg in messages[since_index:]:
        if isinstance(msg, (HumanMessage, AIMessage)):
            count += 1
    return count





# === Middleware ===
class MemoryMiddleware(AgentMiddleware[MemoryState, ContextT, ResponseT]):
    """具有后台提取功能的持久记忆中间件。"""

    state_schema = MemoryState

    def __init__(
        self,
        *,
        engine: BaseShellEngine,
        workspace_root: str,
    ) -> None:
        self.engine = engine
        self.workspace_root = workspace_root
        self.memory_dir = os.path.join(workspace_root, MEMORY_DIR_NAME)
        self.memory_index_path = os.path.join(self.memory_dir, MEMORY_INDEX_FILE)

        self.tools: list[BaseTool] = [
            self._create_memorize_tool(),
            self._create_recall_tool(),
        ]



    def _ensure_memory_dir(self) -> None:
        """如果记忆目录不存在，则创建它。"""
        os.makedirs(self.memory_dir, exist_ok=True)

    def _read_memory_index(self) -> str:
        """读取 MEMORY.md 索引文件内容。"""
        self._ensure_memory_dir()
        result = self.engine.read(self.memory_index_path, offset=0, limit=5000)
        if result.error:
            return ""
        content = result.content or ""
        # 从 engine.read 的输出中去除行号前缀
        lines = content.splitlines()
        clean = []
        for line in lines:
            m = re.match(r"^\s*\d+: ?(.*)", line)
            clean.append(m.group(1) if m else line)
        return "\n".join(clean)



    def _create_memorize_tool(self) -> BaseTool:
        """创建 `memorize` 工具（显式记忆保存）。"""

        def sync_memorize(category: str, title: str, content: str) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_memorize(category: str, title: str, content: str) -> str:
            """使用两步模式保存记忆。

            第一步：写入带有 YAML 前导数据的特征主题文件。
            第二步：在 MEMORY.md 中追加一行指向该文件的指针。
            """
            if category not in MEMORY_TYPES:
                return (
                    f"Error: invalid category '{category}'. "
                    f"Must be one of: {', '.join(MEMORY_TYPES)}"
                )

            self._ensure_memory_dir()

            # 构建文件名
            safe_title = _sanitize_filename(title)
            filename = f"{category}_{safe_title}.md"
            filepath = os.path.join(self.memory_dir, filename)

            # 写入带有前导数据的特征主题文件
            file_content = (
                f"---\n"
                f"name: {title}\n"
                f"type: {category}\n"
                f"---\n\n"
                f"{content}\n"
            )

            write_result = await asyncio.to_thread(
                self.engine.write, filepath, file_content
            )
            if write_result.error:
                return f"Error writing memory file: {write_result.error}"

            # 更新 MEMORY.md 索引
            hook_line = content[:100].replace("\n", " ")
            pointer = f"- [{title}]({filename}) — {hook_line}\n"

            existing_index = await asyncio.to_thread(self._read_memory_index)
            new_index = existing_index + "\n" + pointer if existing_index else pointer

            index_result = await asyncio.to_thread(
                self.engine.write, self.memory_index_path, new_index
            )
            if index_result.error:
                return f"Memory file saved but index update failed: {index_result.error}"

            logger.info("Memory saved: %s (%s)", title, category)
            return f"Memory saved: [{title}]({filename})"

        return StructuredTool.from_function(
            name="memorize",
            description=(
                "将重要信息保存到持久记忆中。当用户分享个人详情、偏好、日程安排或应该在对话之间"
                "记住的重要上下文时，请使用此工具。信息将被保存到经过适当分类的结构化记忆文件中。"
            ),
            func=sync_memorize,
            coroutine=async_memorize,
            infer_schema=False,
            args_schema=MemorizeSchema,
        )

    def _create_recall_tool(self) -> BaseTool:
        """创建 ``recall`` 工具（记忆搜索）。"""
        middleware = self

        def sync_recall(query: str) -> str:
            raise RuntimeError("Sync execution is not supported. Use the async version.")

        async def async_recall(query: str) -> str:
            """在记忆文件中搜索相关信息。"""
            middleware._ensure_memory_dir()

            # 读取 MEMORY.md 索引
            index_content = await asyncio.to_thread(middleware._read_memory_index)
            if not index_content:
                return "No memories saved yet."

            # 索引中的简单关键字搜索
            query_lower = query.lower()
            matching_lines: list[str] = []
            for line in index_content.splitlines():
                if query_lower in line.lower():
                    matching_lines.append(line.strip())

            if not matching_lines:
                return (
                    f"No memories matching '{query}' found in the index.\n\n"
                    f"Full memory index:\n{index_content}\n\n"
                    "Use read_file to access specific memory files listed above."
                )

            return (
                f"Memories matching '{query}':\n"
                + "\n".join(matching_lines)
                + f"\n\nFull index:\n{index_content}\n\n"
                "Use read_file to access the full content of specific memory files."
            )

        return StructuredTool.from_function(
            name="recall",
            description=(
                "在持久记忆中搜索先前保存的信息。"
                "当需要回忆用户偏好、先前的上下文或过去对话中的重要细节时，请使用此工具。"
            ),
            func=sync_recall,
            coroutine=async_recall,
            infer_schema=False,
            args_schema=RecallSchema,
        )

    # --- Hooks ---

    async def abefore_agent(
        self, state: MemoryState, runtime: object
    ) -> dict[str, Any] | None:
        """在首次运行时加载记忆索引。

        确保记忆目录存在并缓存索引内容。
        """
        if "memory_index_content" in state and state.get("memory_index_content"):
            return None

        self._ensure_memory_dir()
        index_content = await asyncio.to_thread(self._read_memory_index)

        return {
            "memory_index_content": index_content,
            "last_memory_cursor": len(state.get("messages", [])),
        }

    def before_agent(
        self, state: MemoryState, runtime: object
    ) -> dict[str, Any] | None:
        """同步版本。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """将 MEMORY.md 内容注入到系统提示词中。

        每次调用都会重新读取索引，以获取当前轮次中的任何写入。
        """
        # 读取最新索引（可能已被 memorize 工具更新）
        index_content = await asyncio.to_thread(self._read_memory_index)

        if index_content:
            memory_prompt = build_memory_system_prompt(
                self.memory_dir, index_content
            )
            existing = request.system_message
            if existing is None:
                new_sys = SystemMessage(content=memory_prompt)
            else:
                old_content = (
                    existing.content
                    if isinstance(existing.content, str)
                    else str(existing.content)
                )
                new_sys = SystemMessage(content=f"{old_content}\n\n{memory_prompt}")
            request = request.override(system_message=new_sys)

        return await handler(request)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """内存注入的同步版本。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    async def aafter_agent(
        self, state: MemoryState, runtime: object
    ) -> dict[str, Any] | None:
        """推进记忆游标。

        NOTE: 后台记忆提取功能已移除（原依赖 SubagentMiddleware）。
        后续可根据需要用其他方式重新实现。
        """
        messages: list[BaseMessage] = list(state.get("messages", []))
        cursor = state.get("last_memory_cursor", 0)

        new_count = _count_model_visible_messages(messages, cursor)
        if new_count < MIN_NEW_MESSAGES_FOR_EXTRACTION:
            return None

        return {"last_memory_cursor": len(messages)}


__all__ = ["MemoryMiddleware"]

