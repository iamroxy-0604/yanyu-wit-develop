"""文件系统中间件"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.shell import BaseShellEngine


# === Input schemas for tools ===
class ListSchema(BaseModel):
    """`list` 工具的输入模式。"""

    path: str = Field(
        description="要列出的目录的绝对路径。必须是绝对路径，不能是相对路径。"
    )

class ReadFileSchema(BaseModel):
    """`read_file` 工具的输入模式。"""

    file_path: str = Field(
        description="要读取的文件的绝对路径。必须是绝对路径，不能是相对路径。"
    )
    offset: int = Field(
        default=0,
        description="开始读取的行号（从 0 开始计数）。用于大文件的分页读取。",
    )
    limit: int = Field(
        default=2000,
        description="最大读取行数。用于大文件的分页读取。",
    )

class WriteFileSchema(BaseModel):
    """`write_file` 工具的输入模式。"""

    file_path: str = Field(
        description="要创建的文件的绝对路径。必须是绝对路径，不能是相对路径。"
    )
    content: str = Field(
        description="要写入文件的文本内容。此参数是必填的。"
    )

class EditFileSchema(BaseModel):
    """`edit_file` 工具的输入模式。"""

    file_path: str = Field(
        description="要编辑的文件的绝对路径。必须是绝对路径，不能是相对路径。"
    )
    old_string: str = Field(
        description="要查找并替换的精确文本。除非 replace_all 为 True，否则在文件中必须是唯一的。"
    )
    new_string: str = Field(
        description="用于替换 old_string 的新文本。必须与 old_string 不同。"
    )
    replace_all: bool = Field(
        default=False,
        description="如果为 True，替换所有出现的 old_string。如果为 False（默认值），old_string 必须是唯一的。",
    )

class GlobFindSchema(BaseModel):
    """`glob_find` 工具的输入模式。"""

    pattern: str = Field(
        description="匹配文件的 Glob 模式（例如 '**/*.py'、'*.txt'、'/subdir/**/*.md'）。"
    )
    path: str = Field(
        default="/",
        description="开始搜索的基目录。默认为根目录 '/'。"
    )
    recursive: bool = Field(
        default=False,
        description="是否进行递归搜索。默认为 False。"
    )

class GrepSearchSchema(BaseModel):
    """`grep_search` 工具的输入模式。"""

    pattern: str = Field(
        description="要搜索的文本模式（字面字符串，不是正则表达式）。"
    )
    path: str | None = Field(
        default=None,
        description="要搜索的目录。默认为当前工作目录。"
    )
    file_pattern: str | None = Field(
        default=None,
        description="过滤要搜索的文件的 Glob 模式（例如 '*.py'）。"
    )

class ExecuteSchema(BaseModel):
    """`execute` 工具的输入模式。"""

    command: str = Field(
        description="要执行的 Shell 命令。"
    )
    timeout: int | None = Field(
        default=None,
        description=(
            "该命令的可选超时时间（以秒为单位）。"
            "会覆盖默认超时时间。"
        ),
    )


# === Description text for tools === 
LIST_FILES_TOOL_DESCRIPTION = """列出指定路径中的所有文件和目录（等同于 Linux 的 `ls` 命令）。

适用于探索文件系统并寻找需要读取或编辑的文件。
在调用 read_file 或 edit_file 工具之前，几乎总是应该先调用此工具。"""

READ_FILE_TOOL_DESCRIPTION = """从文件系统中读取文件。

用法：
- 对大文件使用带有 offset 和 limit 参数的分页读取，以避免上下文溢出。
  - 首次扫描：read_file(file_path, limit=100) 以查看文件结构。
  - 读取更多部分：read_file(file_path, offset=100, limit=200) 读取接下来的 200 行。
- 返回的结果采用 `cat -n` 格式，行号从 1 开始。
- 你可以在单次响应中调用多个工具。
  - 批量读取可能有用的一组文件总是更好的选择。
- 在编辑文件之前，请务必确保已经读取了该文件。"""

EDIT_FILE_TOOL_DESCRIPTION = """在文件中执行精确的字符串替换。

用法：
- 编辑前必须先读取文件。如果找不到目标字符串，此工具将报错。
- 编辑时，请保持读取输出中的精确缩进（制表符/空格）。在 old_string 或 new_string 中绝对不能包含行号前缀。
- 总是优先选择编辑现有文件，而不是创建新文件。
- 只有在用户明确要求时才使用 emoji 表情。"""

WRITE_FILE_TOOL_DESCRIPTION = """向文件系统中的新文件写入内容。

用法：
- write_file 工具会创建一个新文件。
- 如果文件已存在则会报错（使用 edit_file 修改已存在的文件）。
- 如果父目录不存在，将自动创建。
- 在可能的情况下，优先选择编辑现有文件（使用 edit_file 工具），而不是创建新文件。"""

FIND_TOOL_DESCRIPTION = """查找与 glob 模式匹配的文件（等同于 Linux 的 `glob` 或带有名称模式的 `find` 命令）。

支持标准 glob 模式：`*`（任意字符）、`**`（任意目录）、`?`（单个字符）。
返回与模式匹配的文件路径列表。

示例：
- `**/*.py` - 查找所有 Python 文件
- `*.txt` - 查找根目录下的所有文本文件
- `/subdir/**/*.md` - 查找 /subdir 下的所有 markdown 文件"""

SEARCH_TOOL_DESCRIPTION = """跨文件搜索文本模式（等同于 Linux 的 `grep` 命令）。

搜索字面文本（非正则表达式），并返回带有行号的匹配文件。
括号、方括号、管道符等特殊字符会被视为字面字符，而不是正则运算符。

示例：
- 搜索所有文件：`grep_search(pattern="TODO")`
- 仅搜索 Python 文件：`grep_search(pattern="import", file_pattern="*.py")`
- 搜索包含特殊字符的代码：`grep_search(pattern="def __init__(self):")`"""

EXECUTE_TOOL_DESCRIPTION = """在配置的环境中（本地或 Docker 容器）执行 Shell 命令。

用法：
- 对于包含空格的文件路径，请务必用双引号括起来。
- 如果命令将创建新的目录或文件，请先使用 ls 工具验证父目录是否存在。
- 输出将返回标准输出（stdout）和标准错误（stderr）的合并内容以及退出状态码。如果输出过大，可能会被截断。
- 对于长时间运行的命令，使用可选的 timeout 参数来覆盖默认超时时间。
- 执行多条命令时，使用 ';' 或 '&&' 运算符进行分隔。不要使用换行符。
    - 当命令相互依赖时使用 '&&'（例如 "mkdir dir && cd dir"）
    - 仅在需要按顺序运行命令但不在乎前面的命令是否失败时，才使用 ';'
- 尽量通过使用绝对路径来保持当前的工作目录
- 必须避免使用 find 和 grep 等搜索命令，而是使用 `find` 和 `search` 工具。必须避免使用 cat、head、tail 等读取工具，而是使用 read_file 来读取文件。

示例：
  推荐示例：
    - execute(command="pytest /foo/bar/tests")
    - execute(command="python /path/to/script.py")
    - execute(command="npm install && npm test")
    - execute(command="make build", timeout=300)

  避免示例（请勿使用）：
    - execute(command="cat file.txt")  # 应使用 read_file 工具
    - execute(command="find . -name '*.py'")  # 应使用 `find` 工具
    - execute(command="grep -r 'pattern' .")  # 应使用 `search` 工具"""



# === Eviction constants ===
NUM_CHARS_PER_TOKEN = 4
"""用于计算逐出阈值的每个 Token 的近似字符数。"""

DEFAULT_TOOL_RESULT_MAX_TOKENS = 20_000
"""工具结果逐出的默认 Token 阈值。"""

DEFAULT_MAX_EXECUTE_TIMEOUT = 3600
"""执行超时允许的默认最大值（以秒为单位）。"""

TOOLS_EXCLUDED_FROM_EVICTION = (
    "list",
    "read_file",
    "write_file",
    "edit_file",
    "glob_find",
    "grep_search",
)
"""结果绝不被逐出的工具。"""

TOO_LARGE_TOOL_MSG = """工具结果太大。完整输出已保存至：{file_path}

使用带有 offset 和 limit 的 read_file 分段读取它。
示例：read_file(file_path="{file_path}", offset=0, limit=100)

预览（头部和尾部）：

{content_preview}
"""

# === System prompts ===
FILESYSTEM_SYSTEM_PROMPT = """## 遵循规范

- 编辑文件前先读取文件 —— 在做出更改前理解现有内容
- 模仿现有的风格、命名规范和模式

## 文件系统工具 `list`、`read_file`、`write_file`、`edit_file`、`find`、`search`

你可以使用这些工具与文件系统进行交互。
所有文件路径必须以 `/` 开头。遵循可用工具的文档，并在读取大文件时使用分页（offset/limit）。"""

EXECUTION_SYSTEM_PROMPT = """## 执行工具 `execute`

你可以使用 `execute` 工具来运行 Shell 命令。
使用此工具运行命令、脚本、测试、构建和其他 Shell操作。"""

WORKSPACE_SYSTEM_PROMPT_TEMPLATE = """## 你的工作空间：`{workspace_root}`

你有一个用于存储和检索信息的持久工作空间目录。
使用文件系统工具（list、read_file、write_file 等）与其进行交互。

工作空间布局：
- `{workspace_root}/large_results/`  — 大型文件输出结果的缓存。使用 read_file 进行分页读取。
- `{workspace_root}/skills/`         — 你的可用 Agent 技能、参考文档和操作指南。
- `{workspace_root}/scratchpad/`     — 技能执行或推理过程中产生的草稿、中间文件和工作笔记的临时工作空间。
- `{workspace_root}/artifacts/`      — 技能的最终产物、结构化输出以及向用户展示的持久文件。
- `{workspace_root}/discovered/`     — 通过 ACP 发现的外部 Agent 缓存。
- `{workspace_root}/flux/`           — 与 flux 平台交互相关的缓存。
- `{workspace_root}/attachments/`    — 用户上传的文件附件，按会话 ID 组织。每个会话子目录包含一个 manifest.json 和上传的文件。使用用户消息中 [附件信息] 里的文件路径在技能命令中引用这些文件。
- `{workspace_root}/tasks/active/`   — 活跃的、非终端 AIP 任务缓存。你可以在此处查看任务详情。
- `{workspace_root}/tasks/inactive/` — 已完成的、终端 AIP 任务缓存。"""


# === Utilities ===
def _append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """向系统消息追加文本，如果需要则创建。"""
    if system_message is None:
        return SystemMessage(content=text)
    existing = system_message.content
    if isinstance(existing, str):
        return SystemMessage(content=f"{existing}\n\n{text}")
    
    # 处理块列表内容
    from langchain_core.messages.content import ContentBlock

    blocks: list[ContentBlock] = list(system_message.content_blocks)
    if blocks:
        text = f"\n\n{text}"
    blocks.append({"type": "text", "text": text})
    return SystemMessage(content_blocks=blocks)

def _format_search_matches(matches: list[dict[str, Any]]) -> str:
    """将 grep 匹配结果格式化为易于 LLM 阅读的字符串。

    按文件对结果进行分组，显示文件路径标题和匹配行。

    参数:
        matches: 包含 path、line、text 键的 GrepMatch 字典列表。

    返回:
        格式化后的 grep 结果字符串。
    """
    if not matches:
        return "No matches found."

    # 按文件分组
    by_file: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_file.setdefault(m["path"], []).append(m)

    parts: list[str] = []
    for path, file_matches in by_file.items():
        parts.append(f"── {path}")
        for m in file_matches:
            parts.append(f"  {m['line']}: {m['text']}")

    return "\n".join(parts)


def _truncate_output(output: str, max_chars: int = 80_000) -> str:
    """如果输出超过字符限制，则进行截断。

    参数:
        output: 可能需要截断的字符串。
        max_chars: 允许的最大字符数。

    返回:
        原始字符串或追加了截断声明的被截断字符串。
    """
    if len(output) <= max_chars:
        return output
    return (
        output[:max_chars]
        + f"\n\n... [已于 {max_chars} 字符处截断输出]"
    )


def _create_content_preview(
    content: str,
    *,
    head_lines: int = 5,
    tail_lines: int = 5,
) -> str:
    """为已逐出的工具结果创建内容的首尾预览。

    参数:
        content: 完整的内容字符串。
        head_lines: 从开始处显示的行数。
        tail_lines: 从末尾处显示的行数。

    返回:
        带有截断标记的格式化预览。
    """
    lines = content.splitlines()
    total = len(lines)

    if total <= head_lines + tail_lines:
        return content

    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    omitted = total - head_lines - tail_lines

    return (
        "\n".join(head)
        + f"\n\n... [截断了 {omitted} 行] ...\n\n"
        + "\n".join(tail)
    )


def _extract_tool_message_text(message: ToolMessage) -> str:
    """从 ToolMessage 中提取纯文本内容。

    参数:
        message: 要从中提取文本的 ToolMessage。

    返回:
        作为字符串的文本内容。
    """
    if isinstance(message.content, str):
        return message.content
    # 块内容列表 — 拼接文本块
    texts = [
        block["text"]
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(texts)


def _sanitize_tool_call_id(tool_call_id: str) -> str:
    """净化工具调用 ID 以用作文件名。

    参数:
        tool_call_id: 原始工具调用 ID 字符串。

    返回:
        文件系统安全的字符串。
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)


# === Middleware class ===
class FilesystemMiddleware(AgentMiddleware[AgentState, ContextT, ResponseT]):
    """为 Agent 提供文件系统和 Shell 执行工具的中间件。

    所有工具都支持同步和异步调用。

    参数:
        engine: 一个 `BaseShellEngine` 实现。
        workspace_root: Agent 工作空间目录的路径。
    """

    def __init__(
        self,
        *,
        engine: BaseShellEngine,
        workspace_root: str | None = None,
    ) -> None:
        self.engine = engine
        self._workspace_root = os.path.abspath(workspace_root or os.path.join(os.getcwd(), "workspace"))
        self._large_results_dir = os.path.join(self._workspace_root, "large_results")
        self._tool_result_max_tokens = DEFAULT_TOOL_RESULT_MAX_TOKENS
        self._max_execute_timeout = DEFAULT_MAX_EXECUTE_TIMEOUT

        self.tools: list[BaseTool] = [
            self._create_list_tool(),
            self._create_read_file_tool(),
            self._create_write_file_tool(),
            self._create_edit_file_tool(),
            self._create_glob_find_tool(),
            self._create_grep_search_tool(),
            self._create_execute_tool(),
        ]

    def _build_system_prompt(self) -> str:
        """构建文件系统 + 执行 + 工作空间的完整系统提示词。"""
        workspace_prompt = WORKSPACE_SYSTEM_PROMPT_TEMPLATE.format(
            workspace_root=self._workspace_root,
        )
        return (
            f"{FILESYSTEM_SYSTEM_PROMPT}\n\n"
            f"{EXECUTION_SYSTEM_PROMPT}\n\n"
            f"{workspace_prompt}"
        )

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """将文件系统系统提示词注入到模型请求中（同步）。

        参数:
            request: 正在处理的模型请求。
            handler: 使用修改后的请求调用的处理函数。

        返回:
            来自处理器的模型响应。
        """
        new_system_message = _append_to_system_message(
            request.system_message, self._build_system_prompt()
        )
        modified_request = request.override(system_message=new_system_message)
        return handler(modified_request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """将文件系统系统提示词注入到模型请求中（异步）。

        参数:
            request: 正在处理的模型请求。
            handler: 使用修改后的请求调用的异步处理函数。

        返回:
            来自处理器的模型响应。
        """
        new_system_message = _append_to_system_message(
            request.system_message, self._build_system_prompt()
        )
        modified_request = request.override(system_message=new_system_message)
        return await handler(modified_request)

    def _evict_large_result(
        self,
        message: ToolMessage,
    ) -> ToolMessage:
        """检查工具结果大小，如果过大则逐出到工作空间。

        如果结果文本超过 ``tool_result_max_tokens``，则将完整内容
        写入 ``workspace/large_results/<tool_call_id>``，并用截断后的预览
        替换消息内容。

        参数:
            message: 要检查的工具结果消息。

        返回:
            如果足够小则返回原始消息，否则返回带有预览的替换消息。
        """
        if self._tool_result_max_tokens is None:
            return message

        content_str = _extract_tool_message_text(message)
        threshold = NUM_CHARS_PER_TOKEN * self._tool_result_max_tokens

        if len(content_str) <= threshold:
            return message

        # 太大，将完整内容写入工作空间
        sanitized_id = _sanitize_tool_call_id(message.tool_call_id)
        file_path = os.path.join(self._large_results_dir, sanitized_id)
        result = self.engine.write(file_path, content_str)

        if result.error:
            # 如果写入失败，返回原始数据（总比丢失数据好）
            return message

        # 构建预览替换
        preview = _create_content_preview(content_str)
        replacement = TOO_LARGE_TOOL_MSG.format(
            file_path=file_path,
            content_preview=preview,
        )

        return ToolMessage(
            content=replacement,
            tool_call_id=message.tool_call_id,
            name=message.name,
            id=message.id,
            status=message.status,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """拦截大型工具结果并逐出到工作空间（同步）。

        参数:
            request: 正在处理的工具调用请求。
            handler: 执行工具的处理函数。

        返回:
            原始或已逐出的工具结果。
        """
        tool_result = handler(request)

        if (
            self._tool_result_max_tokens is None
            or request.tool_call["name"] in TOOLS_EXCLUDED_FROM_EVICTION
        ):
            return tool_result

        if isinstance(tool_result, ToolMessage):
            return self._evict_large_result(tool_result)

        return tool_result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest], Awaitable[ToolMessage | Command[Any]]
        ],
    ) -> ToolMessage | Command[Any]:
        """拦截大型工具结果并逐出到工作空间（异步）。

        参数:
            request: 正在处理的工具调用请求。
            handler: 执行工具的异步处理函数。

        返回:
            原始或已逐出的工具结果。
        """
        tool_result = await handler(request)

        if (
            self._tool_result_max_tokens is None
            or request.tool_call["name"] in TOOLS_EXCLUDED_FROM_EVICTION
        ):
            return tool_result

        if isinstance(tool_result, ToolMessage):
            return await asyncio.to_thread(
                self._evict_large_result, tool_result
            )

        return tool_result

    def _create_list_tool(self) -> BaseTool:
        """创建 `list` 工具。"""
        engine = self.engine

        def sync_ls(
            path: str,
        ) -> str:
            """同步 ls 实现。"""
            result = engine.ls(path)
            if result.error:
                return f"Error: {result.error}"
            entries = result.entries or []
            lines: list[str] = []
            for entry in entries:
                marker = "d" if entry.get("is_dir") else "f"
                size = entry.get("size", "")
                lines.append(f"[{marker}] {entry['path']}  ({size} bytes)")
            return "\n".join(lines) if lines else "(Empty directory)"

        async def async_ls(
            path: str,
        ) -> str:
            """异步 ls 实现。"""
            return await asyncio.to_thread(sync_ls, path)

        return StructuredTool.from_function(
            name="list",
            description=LIST_FILES_TOOL_DESCRIPTION,
            func=sync_ls,
            coroutine=async_ls,
            infer_schema=False,
            args_schema=ListSchema,
        )

    def _create_read_file_tool(self) -> BaseTool:
        """创建 `read_file` 工具。"""
        engine = self.engine

        def sync_read_file(
            file_path: str,
            offset: int = 0,
            limit: int = 2000,
        ) -> str:
            """同步 read_file 实现。"""
            result = engine.read(file_path, offset=offset, limit=limit)
            if result.error:
                return f"Error: {result.error}"
            content = result.content or ""
            return _truncate_output(content)

        async def async_read_file(
            file_path: str,
            offset: int = 0,
            limit: int = 2000,
        ) -> str:
            """异步 read_file 实现。"""
            return await asyncio.to_thread(
                sync_read_file, file_path, offset, limit
            )

        return StructuredTool.from_function(
            name="read_file",
            description=READ_FILE_TOOL_DESCRIPTION,
            func=sync_read_file,
            coroutine=async_read_file,
            infer_schema=False,
            args_schema=ReadFileSchema,
        )

    def _create_write_file_tool(self) -> BaseTool:
        """创建 `write_file` 工具。"""
        engine = self.engine

        def sync_write_file(
            file_path: str,
            content: str,
        ) -> str:
            """同步 write_file 实现。"""
            result = engine.write(file_path, content)
            if result.error:
                return f"Error: {result.error}"
            return f"Successfully created file: {result.path}"

        async def async_write_file(
            file_path: str,
            content: str,
        ) -> str:
            """异步 write_file 实现。"""
            return await asyncio.to_thread(sync_write_file, file_path, content)

        return StructuredTool.from_function(
            name="write_file",
            description=WRITE_FILE_TOOL_DESCRIPTION,
            func=sync_write_file,
            coroutine=async_write_file,
            infer_schema=False,
            args_schema=WriteFileSchema,
        )

    def _create_edit_file_tool(self) -> BaseTool:
        """创建 `edit_file` 工具。"""
        engine = self.engine

        def sync_edit_file(
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> str:
            """同步 edit_file 实现。"""
            result = engine.edit(
                file_path, old_string, new_string, replace_all=replace_all
            )
            if result.error:
                return f"Error: {result.error}"
            return (
                f"Successfully replaced {result.occurrences} instance(s) "
                f"in '{result.path}'"
            )

        async def async_edit_file(
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> str:
            """异步 edit_file 实现。"""
            return await asyncio.to_thread(
                sync_edit_file, file_path, old_string, new_string, replace_all
            )

        return StructuredTool.from_function(
            name="edit_file",
            description=EDIT_FILE_TOOL_DESCRIPTION,
            func=sync_edit_file,
            coroutine=async_edit_file,
            infer_schema=False,
            args_schema=EditFileSchema,
        )

    def _create_glob_find_tool(self) -> BaseTool:
        """创建 `glob_find` 工具。"""
        engine = self.engine

        def sync_glob_find(
            pattern: str,
            path: str = "/",
            recursive: bool = False,
        ) -> str:
            """同步 glob 实现。"""
            result = engine.glob(pattern, path, recursive=recursive)
            if result.error:
                return f"Error: {result.error}"
            matches = result.matches or []
            if not matches:
                return "No files matched the pattern."
            paths = [m["path"] for m in matches]
            output = "\n".join(paths)
            return _truncate_output(output)

        async def async_glob_find(
            pattern: str,
            path: str = "/",
            recursive: bool = False,
        ) -> str:
            """异步 glob 实现。"""
            return await asyncio.to_thread(sync_glob_find, pattern, path, recursive)

        return StructuredTool.from_function(
            name="glob_find",
            description=FIND_TOOL_DESCRIPTION,
            func=sync_glob_find,
            coroutine=async_glob_find,
            infer_schema=False,
            args_schema=GlobFindSchema,
        )

    def _create_grep_search_tool(self) -> BaseTool:
        """创建 `grep_search` 工具。"""
        engine = self.engine

        def sync_grep_search(
            pattern: str,
            path: str | None = None,
            file_pattern: str | None = None,
        ) -> str:
            """同步 grep 实现。"""
            result = engine.grep(pattern, path=path, glob_pattern=file_pattern)
            if result.error:
                return f"Error: {result.error}"
            matches = result.matches or []
            formatted = _format_search_matches(matches)
            return _truncate_output(formatted)

        async def async_grep_search(
            pattern: str,
            path: str | None = None,
            file_pattern: str | None = None,
        ) -> str:
            """异步 grep 实现。"""
            return await asyncio.to_thread(sync_grep_search, pattern, path, file_pattern)

        return StructuredTool.from_function(
            name="grep_search",
            description=SEARCH_TOOL_DESCRIPTION,
            func=sync_grep_search,
            coroutine=async_grep_search,
            infer_schema=False,
            args_schema=GrepSearchSchema,
        )

    def _create_execute_tool(self) -> BaseTool:
        """创建用于执行 shell 命令的 ``execute`` 工具。"""
        engine = self.engine
        max_timeout = self._max_execute_timeout

        def sync_execute(
            command: str,
            timeout: int | None = None,
        ) -> str:
            """同步 execute 实现。"""
            if timeout is not None:
                if timeout < 0:
                    return f"Error: timeout must be non-negative, got {timeout}."
                if timeout > max_timeout:
                    return (
                        f"Error: timeout {timeout}s exceeds maximum allowed "
                        f"({max_timeout}s)."
                    )

            result = engine.execute(command, timeout=timeout)

            # 格式化输出以供 LLM 消费
            parts = [result.output]

            if result.exit_code is not None:
                status = "succeeded" if result.exit_code == 0 else "failed"
                parts.append(
                    f"\n[Command {status} with exit code {result.exit_code}]"
                )

            if result.truncated:
                parts.append("\n[Output was truncated due to size limits]")

            return "".join(parts)

        async def async_execute(
            command: str,
            timeout: int | None = None,
        ) -> str:
            """异步 execute 实现。"""
            return await asyncio.to_thread(sync_execute, command, timeout)

        return StructuredTool.from_function(
            name="execute",
            description=EXECUTE_TOOL_DESCRIPTION,
            func=sync_execute,
            coroutine=async_execute,
            infer_schema=False,
            args_schema=ExecuteSchema,
        )


__all__ = ["FilesystemMiddleware"]
