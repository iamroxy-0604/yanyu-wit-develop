"""上下文压缩中间件。

第 1 级 — 微压缩（MicroCompact，就地减肥）：
  在每次模型调用前运行。将旧的 ToolMessage 条目的内容替换为简短的存根（stubs），
  同时保留 ``tool_call_id`` 配对（这对于 LangChain 的 AIMessage ↔ ToolMessage 契约至关重要）。
  同时从旧的 HumanMessage 内容中去除 Base64 图像。

第 2 级 — 自动压缩（AutoCompact，摘要压缩）：
  当估计的 Token 数量超过阈值时触发。将历史记录（不含最近轮次）
  发送给轻量级模型进行摘要，然后将消息重构为：[摘要, ...最近消息, 状态回填]。
"""

from __future__ import annotations

import asyncio
import logging
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
    ToolMessage,
)
from langchain_core.language_models import BaseChatModel

from agent.middlewares.utils.compression_prompt import (
    get_compact_prompt,
    get_compact_user_summary_message,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# === 常量 ===
EFFECTIVE_CONTEXT_WINDOW = 236_000 # qwen3.5:35b ~ 256k 上下文
AUTOCOMPACT_BUFFER_TOKENS = 36_000
AUTOCOMPACT_THRESHOLD = EFFECTIVE_CONTEXT_WINDOW - AUTOCOMPACT_BUFFER_TOKENS  # ~200k

# 微压缩：保持最近的这几个消息对（用户+助手+工具）完好无损
MICRO_COMPACT_KEEP_RECENT = 10

# 清除旧 ToolMessage 内容的内容大小阈值
TOOL_RESULT_CLEAR_THRESHOLD = 500  # 字符数

# 连续自动压缩失败的最大次数（断路器，参考 autoCompact.ts）
MAX_CONSECUTIVE_FAILURES = 3

# 自动压缩摘要期间要保留的最近“轮次”的数量
AUTOCOMPACT_PRESERVE_ROUNDS = 5

# 用于 Token 计数的粗略字符/Token 估计值
CHARS_PER_TOKEN = 4

# 已清除内容的存根（参考 microCompact.ts 模式）
CLEARED_TOOL_STUB = "[工具结果已清除]"
CLEARED_IMAGE_STUB = "[图片]"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class CompressionState(AgentState):
    """扩展了压缩跟踪的 Agent 状态。"""

    compression_summary: NotRequired[Annotated[str | None, PrivateStateAttr]]
    """最新的压缩摘要文本（如果有的话）。"""

    compression_consecutive_failures: NotRequired[Annotated[int, PrivateStateAttr]]
    """断路器：连续自动压缩失败次数。"""


class CompressionStateUpdate(TypedDict):
    """来自压缩操作的部分状态更新。"""

    compression_summary: str | None
    compression_consecutive_failures: int


# ---------------------------------------------------------------------------
# Token estimation utilities
# ---------------------------------------------------------------------------

def _estimate_message_tokens(msg: BaseMessage) -> int:
    """粗略计算单条消息的 Token 数。"""
    content = msg.content
    if isinstance(content, str):
        return len(content) // CHARS_PER_TOKEN
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if block.get("type") == "image_url":
                    total += 500  # rough estimate for image tokens
                else:
                    total += len(str(text)) // CHARS_PER_TOKEN
            else:
                total += len(str(block)) // CHARS_PER_TOKEN
        return total
    return len(str(content)) // CHARS_PER_TOKEN


def _estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """粗略计算消息列表的总 Token 数。"""
    return sum(_estimate_message_tokens(m) for m in messages)


# ---------------------------------------------------------------------------
# MicroCompact: in-place slimming (照搬 microCompact.ts)
# ---------------------------------------------------------------------------

def _is_base64_content(text: str) -> bool:
    """检查文本是否看起来像 Base64 编码的数据。"""
    if len(text) < 200:
        return False
    # 检查常见的 Base64 模式（数据 URI 或原始 Base64）
    return bool(re.match(r"^data:[^;]+;base64,", text)) or bool(
        re.match(r"^[A-Za-z0-9+/]{200,}={0,2}$", text[:300])
    )


def _micro_compact_messages(
    messages: list[BaseMessage],
    keep_recent: int = MICRO_COMPACT_KEEP_RECENT,
) -> tuple[list[BaseMessage], int]:
    """对旧消息执行就地减肥。

    保持最近的 ``keep_recent`` 轮消息完好无损。
    对于更旧的消息：
    - ToolMessage 内容 > 阈值 → 替换为存根
    - 带有 Base64 的 HumanMessage 内容 → 替换为存根
    - AIMessage 内容保留（较小，包含 tool_calls 元数据）

    重要：``tool_call_id`` 绝不改变，以保留
    LangChain 所需的 AIMessage ↔ ToolMessage 配对。

    参数:
        messages: 当前消息列表。
        keep_recent: 要保持完好无损的最近消息数量。

    返回:
        (modified_messages, tokens_freed) 元组。
    """
    if len(messages) <= keep_recent:
        return messages, 0

    boundary = len(messages) - keep_recent
    tokens_freed = 0
    result: list[BaseMessage] = []

    for i, msg in enumerate(messages):
        if i >= boundary:
            # 最近的消息 —— 保持完好无损
            result.append(msg)
            continue

        if isinstance(msg, ToolMessage):
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            if len(content_str) > TOOL_RESULT_CLEAR_THRESHOLD:
                old_tokens = len(content_str) // CHARS_PER_TOKEN
                new_msg = ToolMessage(
                    content=CLEARED_TOOL_STUB,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                    id=msg.id,
                    status=msg.status,
                )
                tokens_freed += old_tokens - len(CLEARED_TOOL_STUB) // CHARS_PER_TOKEN
                result.append(new_msg)
            else:
                result.append(msg)

        elif isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str) and _is_base64_content(content):
                old_tokens = len(content) // CHARS_PER_TOKEN
                new_msg = HumanMessage(content=CLEARED_IMAGE_STUB, id=msg.id)
                tokens_freed += old_tokens - len(CLEARED_IMAGE_STUB) // CHARS_PER_TOKEN
                result.append(new_msg)
            elif isinstance(content, list):
                # 处理块列表内容（剥离图像块）
                new_blocks = []
                changed = False
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "image_url":
                            new_blocks.append({"type": "text", "text": CLEARED_IMAGE_STUB})
                            tokens_freed += 500  # 粗略的图像 Token 估计值
                            changed = True
                        elif block.get("type") == "text" and _is_base64_content(
                            block.get("text", "")
                        ):
                            old_tokens = len(block.get("text", "")) // CHARS_PER_TOKEN
                            new_blocks.append({"type": "text", "text": CLEARED_IMAGE_STUB})
                            tokens_freed += old_tokens
                            changed = True
                        else:
                            new_blocks.append(block)
                    else:
                        new_blocks.append(block)
                if changed:
                    result.append(HumanMessage(content=new_blocks, id=msg.id))
                else:
                    result.append(msg)
            else:
                result.append(msg)
        else:
            # AIMessage, SystemMessage, etc. — keep as-is
            result.append(msg)

    return result, tokens_freed


# ---------------------------------------------------------------------------
# AutoCompact: summary compaction (照搬 compactConversation flow)
# ---------------------------------------------------------------------------

def _split_messages_for_compact(
    messages: list[BaseMessage],
    preserve_rounds: int = AUTOCOMPACT_PRESERVE_ROUNDS,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """将消息分割为摘要目标和保留目标。

    从末尾向前遍历，计算“轮次”（每个 HumanMessage
    开始一个新轮次）。保留最近的 ``preserve_rounds`` 轮。

    返回:
        (to_summarize, to_preserve) 元组。
    """
    if len(messages) <= 3:
        return messages, []

    rounds_found = 0
    split_index = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            rounds_found += 1
            if rounds_found >= preserve_rounds:
                split_index = i
                break

    # 确保我们不会在 tool_call ↔ ToolMessage 对的中间进行分割
    # 从 split_index 向前遍历以寻找安全边界
    while split_index > 0:
        msg = messages[split_index]
        if isinstance(msg, ToolMessage):
            # 此 ToolMessage 属于它之前的 AIMessage —— 两者都包含
            split_index -= 1
        else:
            break

    to_summarize = messages[:split_index]
    to_preserve = messages[split_index:]

    if not to_summarize:
        return messages, []

    return to_summarize, to_preserve


def _strip_images_from_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """在发送压缩之前从消息中剥离图像块。

    参考 compact.ts: stripImagesFromMessages。
    生成对话摘要不需要图像。
    """
    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
            new_content = []
            has_image = False
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    new_content.append({"type": "text", "text": CLEARED_IMAGE_STUB})
                    has_image = True
                else:
                    new_content.append(block)
            if has_image:
                result.append(HumanMessage(content=new_content, id=msg.id))
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


async def _run_autocompact(
    messages_to_summarize: list[BaseMessage],
    model: BaseChatModel,
) -> str | None:
    """运行压缩模型以生成摘要。

    参数:
        messages_to_summarize: 输入到压缩模型的消息。
        model: 用于摘要的 LLM。

    返回:
        摘要文本，失败时为 None。
    """
    # 摘要前剥离图像
    clean_messages = _strip_images_from_messages(messages_to_summarize)

    # 构建压缩提示词
    compact_prompt = get_compact_prompt()

    # 构建请求：原始消息 + 压缩指令
    request_messages: list[BaseMessage] = [
        *clean_messages,
        HumanMessage(content=compact_prompt),
    ]

    try:
        response = await model.ainvoke(request_messages)
        summary = response.content
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        logger.warning("AutoCompact: empty summary response")
        return None
    except Exception:
        logger.exception("AutoCompact: model invocation failed")
        return None


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------

class CompressionMiddleware(AgentMiddleware[CompressionState, ContextT, ResponseT]):
    """两级上下文压缩中间件。

    第 1 级 (MicroCompact)：在 ``abefore_model`` 中运行 —— 清除旧 ToolMessage
    内容并从旧 HumanMessage 中剥离图像。

    第 2 级 (AutoCompact)：在 MicroCompact 之后的 ``abefore_model`` 中运行 —— 如果
    估计的 Token 仍然超过阈值，则生成摘要并重构消息历史记录。
    """

    state_schema = CompressionState

    def __init__(
        self,
        *,
        workspace_root: str,
        autocompact_threshold: int = AUTOCOMPACT_THRESHOLD,
        keep_recent: int = MICRO_COMPACT_KEEP_RECENT,
        preserve_rounds: int = AUTOCOMPACT_PRESERVE_ROUNDS,
    ) -> None:
        self.workspace_root = workspace_root
        self.autocompact_threshold = autocompact_threshold
        self.keep_recent = keep_recent
        self.preserve_rounds = preserve_rounds
        self.tools: list = []

    def _get_subagent_model(self) -> BaseChatModel:
        """创建用于压缩摘要的 LLM。"""
        from provider import get_model_factory
        return get_model_factory().get_model("summarizer", streaming=False)

    # --- before_model: two-level compression ---

    async def abefore_model(
        self, state: CompressionState, runtime: object
    ) -> dict[str, Any] | None:
        """在每次模型调用前执行两级压缩。

        1. 微压缩 (MicroCompact)：清除旧 ToolMessage 内容
        2. 检查 Token 阈值 → 可能会触发自动压缩 (AutoCompact)
        """
        messages: list[BaseMessage] = list(state.get("messages", []))
        if len(messages) < 5:
            return None

        # --- Level 1: MicroCompact ---
        messages, tokens_freed = _micro_compact_messages(messages, self.keep_recent)
        if tokens_freed > 0:
            logger.info(
                "MicroCompact: freed ~%d tokens from %d messages",
                tokens_freed,
                len(messages),
            )

        # --- Level 2: AutoCompact check ---
        estimated_tokens = _estimate_messages_tokens(messages)
        logger.debug(
            "Compression: estimated %d tokens (threshold=%d)",
            estimated_tokens,
            self.autocompact_threshold,
        )

        if estimated_tokens < self.autocompact_threshold:
            # Only return MicroCompact result if tokens were freed
            if tokens_freed > 0:
                return {"messages": messages}
            return None

        # 断路器检查
        consecutive_failures = state.get("compression_consecutive_failures", 0)
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "AutoCompact: circuit breaker tripped after %d failures, skipping",
                consecutive_failures,
            )
            if tokens_freed > 0:
                return {"messages": messages}
            return None

        # --- 运行 AutoCompact ---
        logger.info(
            "AutoCompact: triggering (estimated %d tokens > threshold %d)",
            estimated_tokens,
            self.autocompact_threshold,
        )

        to_summarize, to_preserve = _split_messages_for_compact(
            messages, self.preserve_rounds
        )

        if not to_summarize:
            logger.warning("AutoCompact: nothing to summarize")
            if tokens_freed > 0:
                return {"messages": messages}
            return None

        model = self._get_subagent_model()
        summary = await _run_autocompact(to_summarize, model)

        if summary is None:
            logger.warning("AutoCompact: summary generation failed")
            new_failures = consecutive_failures + 1
            update: dict[str, Any] = {
                "compression_consecutive_failures": new_failures,
            }
            if tokens_freed > 0:
                update["messages"] = messages
            return update

        # 构建压缩后的消息序列：
        # [summary_message, ...preserved_recent_messages]
        summary_user_msg = get_compact_user_summary_message(
            summary, suppress_follow_up_questions=True
        )
        compacted: list[BaseMessage] = [
            HumanMessage(content=summary_user_msg),
            *to_preserve,
        ]

        post_tokens = _estimate_messages_tokens(compacted)
        logger.info(
            "AutoCompact: %d tokens → %d tokens (freed %d)",
            estimated_tokens,
            post_tokens,
            estimated_tokens - post_tokens,
        )

        return {
            "messages": compacted,
            "compression_summary": summary,
            "compression_consecutive_failures": 0,
        }

    def before_model(
        self, state: CompressionState, runtime: object
    ) -> dict[str, Any] | None:
        """同步回退 —— 通过线程委托给异步。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    # --- wrap_model_call: state backfill ---

    def _build_backfill_prompt(self, state: CompressionState) -> str | None:
        """构建压缩后要注入的状态回填文本。

        参考 compact.ts 压缩后附件逻辑：重新注入活动任务、
        已发现的 Agent 和工作空间布局，使模型在压缩后不会失去记忆。
        """
        parts: list[str] = []

        # Backfill: active task cache summary
        task_cache = state.get("acps_task_cache")
        if task_cache:
            active_tasks = []
            for task_id, task_data in task_cache.items():
                status = task_data.get("status", "unknown")
                if status not in ("completed", "canceled", "failed", "rejected"):
                    name = task_data.get("name", task_id)
                    active_tasks.append(f"  - [{status}] {name} (id: {task_id})")
            if active_tasks:
                parts.append("## Active Tasks\n" + "\n".join(active_tasks))

        # Backfill: discovered agents summary
        discovered = state.get("acps_discovered_agents")
        if discovered:
            agents = [
                f"  - {info.get('name', aic)} ({aic}): {info.get('description', 'N/A')}"
                for aic, info in list(discovered.items())[:10]
            ]
            if agents:
                parts.append("## Known Agents\n" + "\n".join(agents))

        if not parts:
            return None
        return "\n\n".join(parts)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """压缩后将状态回填注入到系统提示词中。"""
        backfill = self._build_backfill_prompt(request.state)
        if backfill:
            existing = request.system_message
            if existing is None:
                new_sys = SystemMessage(content=backfill)
            else:
                old_content = (
                    existing.content
                    if isinstance(existing.content, str)
                    else str(existing.content)
                )
                new_sys = SystemMessage(content=f"{old_content}\n\n{backfill}")
            request = request.override(system_message=new_sys)

        return await handler(request)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """状态回填注入的同步版本。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")


__all__ = ["CompressionMiddleware"]
