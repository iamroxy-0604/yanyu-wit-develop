"""加载 Agent 技能并将其展示在系统提示词中的技能中间件。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, NotRequired, TypedDict

import yaml
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import SystemMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from agent.shell import BaseShellEngine

logger = logging.getLogger(__name__)


# === Constants ===
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024


# === Data type ===
class SkillMetadata(TypedDict):
    """从 SKILL.md 前导数据中解析出的技能元数据。"""

    path: str
    """SKILL.md 文件的绝对路径。"""

    name: str
    """技能标识符（仅限小写字母数字和连字符，长度为 1-64 个字符）。"""

    description: str
    """该技能的作用（1-1024 个字符）。"""

    license: str | None
    """如果提供，则为许可证名称。"""

    compatibility: str | None
    """如果提供，则为环境兼容性要求。"""

    metadata: dict[str, str]
    """来自前导数据的任意键值对元数据。"""

    allowed_tools: list[str]
    """该技能推荐使用的工具名称列表。"""


class SkillsState(AgentState):
    """扩展了技能元数据的 Agent 状态。"""

    skills_metadata: NotRequired[Annotated[list[SkillMetadata], PrivateStateAttr]]
    """已加载的技能元数据。私有属性 —— 不会传播给父 Agent。"""


class SkillsStateUpdate(TypedDict):
    """包含已加载技能元数据 State 的更新。"""

    skills_metadata: list[SkillMetadata]


# === Utils ===
def _validate_skill_name(name: str, directory_name: str) -> tuple[bool, str]:
    """根据 Agent 技能规范验证技能名称。

    参数:
        name: 来自 YAML 前导数据的技能名称。
        directory_name: 父目录名称。

    返回:
        (is_valid, error_message) 元组。如果有效，错误消息为空。
    """
    if not name:
        return False, "name is required"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"name exceeds {MAX_SKILL_NAME_LENGTH} characters"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, "name must not start/end with '-' or contain '--'"
    for c in name:
        if c == "-":
            continue
        if (c.isalpha() and c.islower()) or c.isdigit():
            continue
        return False, "name must be lowercase alphanumeric with hyphens only"
    if name != directory_name:
        return False, f"name '{name}' must match directory name '{directory_name}'"
    return True, ""


def _parse_skill_metadata(
    content: str,
    skill_path: str,
    directory_name: str,
) -> SkillMetadata | None:
    """从 SKILL.md 内容中解析 YAML 前导数据。

    参数:
        content: SKILL.md 文件的全文内容。
        skill_path: 文件路径（用于日志记录）。
        directory_name: 用于名称验证的父目录名称。

    返回:
        如果解析成功，返回 SkillMetadata，否则返回 None。
    """
    if len(content) > MAX_SKILL_FILE_SIZE:
        logger.warning("Skipping %s: content too large (%d bytes)", skill_path, len(content))
        return None

    # 匹配 --- 分隔符之间的 YAML 前导数据
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        logger.warning("Skipping %s: no valid YAML frontmatter found", skill_path)
        return None

    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logger.warning("Invalid YAML in %s: %s", skill_path, e)
        return None

    if not isinstance(data, dict):
        logger.warning("Skipping %s: frontmatter is not a mapping", skill_path)
        return None

    name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()

    if not name or not description:
        logger.warning("Skipping %s: missing required 'name' or 'description'", skill_path)
        return None

    # 验证名称（警告但为了向后兼容不拒绝）
    is_valid, error = _validate_skill_name(name, directory_name)
    if not is_valid:
        logger.warning("Skill '%s' in %s: %s", name, skill_path, error)

    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        logger.warning("Description too long in %s, truncating", skill_path)
        description = description[:MAX_SKILL_DESCRIPTION_LENGTH]

    # 解析 allowed-tools
    raw_tools = data.get("allowed-tools")
    if isinstance(raw_tools, str):
        allowed_tools = [t.strip(",") for t in raw_tools.split() if t.strip(",")]
    else:
        allowed_tools = []

    # 解析元数据字典
    raw_meta = data.get("metadata", {})
    metadata = {str(k): str(v) for k, v in raw_meta.items()} if isinstance(raw_meta, dict) else {}

    return SkillMetadata(
        name=name,
        description=description,
        path=skill_path,
        license=str(data.get("license", "")).strip() or None,
        compatibility=str(data.get("compatibility", "")).strip() or None,
        metadata=metadata,
        allowed_tools=allowed_tools,
    )


def _list_skills(engine: BaseShellEngine, source_path: str) -> list[SkillMetadata]:
    """使用 Shell 引擎扫描源目录以获取技能。

    预期结构::

        source_path/
        └── skill-name/
            ├── SKILL.md   # 必需
            └── helper.py  # 可选

    参数:
        engine: 用于文件系统访问的 Shell 引擎。
        source_path: 技能源目录的路径。

    返回:
        解析后的 SkillMetadata 列表。
    """
    skills: list[SkillMetadata] = []

    ls_result = engine.ls(source_path)
    if ls_result.error:
        logger.warning("Cannot list skills source '%s': %s", source_path, ls_result.error)
        return []

    # 查找技能目录
    skill_dirs = [
        entry["path"]
        for entry in (ls_result.entries or [])
        if entry.get("is_dir")
    ]
    if not skill_dirs:
        return []

    # 从每个目录读取 SKILL.md 或 skill.md
    for skill_dir_path in skill_dirs:
        skill_md_path = None
        content = ""
        for filename in ("SKILL.md", "skill.md"):
            candidate_path = os.path.join(skill_dir_path, filename)
            read_result = engine.read(candidate_path, offset=0, limit=5000)
            if not read_result.error:
                skill_md_path = candidate_path
                content = read_result.content or ""
                break

        if not skill_md_path or not content:
            continue

        # 去除行号前缀（engine.read 返回 "  N: content" 格式）
        raw_lines = content.splitlines()
        clean_lines = []
        for line in raw_lines:
            # 匹配类似 "  1: content" 或 " 10: content" 的模式
            m = re.match(r"^\s*\d+: ?(.*)", line)
            clean_lines.append(m.group(1) if m else line)
        clean_content = "\n".join(clean_lines)

        directory_name = os.path.basename(skill_dir_path.rstrip("/"))
        skill_metadata = _parse_skill_metadata(
            content=clean_content,
            skill_path=skill_md_path,
            directory_name=directory_name,
        )
        if skill_metadata:
            skills.append(skill_metadata)

    return skills

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
    return SystemMessage(content=f"{existing}\n\n{text}")

# === System prompt ===
SKILLS_SYSTEM_PROMPT = """## 技能系统

你拥有一个技能库，提供专门的能力和领域知识。

{skills_locations}

**可用技能:**

{skills_list}

**如何使用技能（渐进式透露）：**

技能遵循**渐进式透露**模式 —— 你在上方看到了它们的名称和描述，
但仅在需要时才读取完整指令：

1. **识别何时适用某项技能**：检查用户的任务是否与某个技能'的描述相匹配
2. **读取技能的完整指令**：使用 `read_file` 读取上面显示的路径
3. **遵循技能的指令**：SKILL.md 包含了分步工作流程和示例
4. **访问辅助文件**：技能可能包含辅助脚本 —— 在技能目录上使用 `ls`

**何时使用技能：**
- 用户的请求符合某个技能的领域
- 你需要专业知识或结构化的工作流程
- 技能为复杂的任务提供了经过验证的模式"""




# === Middleware class ===
class SkillsMiddleware(AgentMiddleware[SkillsState, ContextT, ResponseT]):
    """用于加载技能并将其展示在系统提示词中的中间件。"""

    state_schema = SkillsState

    def __init__(
        self,
        *,
        engine: BaseShellEngine,
        workspace_root: str,
    ) -> None:
        self.engine = engine
        self.workspace_root = workspace_root
        self.skills_dir = os.path.join(workspace_root, "skills")

    def _format_skills_locations(self) -> str:
        """格式化系统提示词中的技能目录位置。"""
        return f"**Skills**: `{self.skills_dir}`"

    def _format_skills_list(self, skills: list[SkillMetadata]) -> str:
        """格式化系统提示词中的技能元数据列表。"""
        if not skills:
            return f"(暂无可用技能。请在 `{self.skills_dir}` 中创建技能)"

        lines: list[str] = []
        for skill in skills:
            # Build annotation
            annotations: list[str] = []
            if skill.get("license"):
                annotations.append(f"许可证: {skill['license']}")
            if skill.get("compatibility"):
                annotations.append(f"兼容性: {skill['compatibility']}")
            annotation_str = f" ({', '.join(annotations)})" if annotations else ""

            lines.append(f"- **{skill['name']}**: {skill['description']}{annotation_str}")
            if skill["allowed_tools"]:
                lines.append(f"  → 推荐工具: {', '.join(skill['allowed_tools'])}")
            lines.append(f"  → 读取 `{skill['path']}` 获取完整说明")

        return "\n".join(lines)

    def _inject_skills_prompt(
        self,
        request: ModelRequest[ContextT],
    ) -> ModelRequest[ContextT]:
        """构建技能信息并将其注入到模型请求系统消息中。

        参数:
            request: 要修改的模型请求。

        返回:
            注入了技能文档的修改后的请求。
        """
        skills_metadata: list[SkillMetadata] = request.state.get("skills_metadata", [])
        skills_section = SKILLS_SYSTEM_PROMPT.format(
            skills_locations=self._format_skills_locations(),
            skills_list=self._format_skills_list(skills_metadata),
        )
        new_system_message = _append_to_system_message(
            request.system_message, skills_section,
        )
        return request.override(system_message=new_system_message)


    def before_agent(self, state: SkillsState, runtime: object) -> SkillsStateUpdate | None:
        """在 Agent 执行前加载技能元数据（同步）。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    async def abefore_agent(self, state: SkillsState, runtime: object) -> SkillsStateUpdate | None:
        """在 Agent 执行前加载技能元数据（异步）。

        参数:
            state: 当前 Agent 状态。
            runtime: 运行时上下文。

        返回:
            包含 skills_metadata 的状态更新。
        """
        all_skills: dict[str, SkillMetadata] = {}
        skills_list = await asyncio.to_thread(_list_skills, self.engine, self.skills_dir)
        for skill in skills_list:
            all_skills[skill["name"]] = skill

        return SkillsStateUpdate(skills_metadata=list(all_skills.values()))

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """将技能目录注入到系统提示词中（同步）。"""
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """将技能目录注入到系统提示词中（异步）。

        参数:
            request: 正在处理的模型请求。
            handler: 使用修改后的请求调用的异步处理函数。

        返回:
            来自处理器的模型响应。
        """
        modified_request = self._inject_skills_prompt(request)
        return await handler(modified_request)


__all__ = ["SkillMetadata", "SkillsMiddleware"]
