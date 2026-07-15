"""记忆类型分类和提示词模板。"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# Type taxonomy
# ---------------------------------------------------------------------------

MEMORY_TYPES = [
    "user_profile",
    "interaction_rules",
    "active_context",
    "knowledge_base",
]

MemoryType = Literal[
    "user_profile",
    "interaction_rules",
    "active_context",
    "knowledge_base",
]


def parse_memory_type(raw: str | None) -> MemoryType | None:
    """将原始 frontmatter 值解析为 MemoryType。

    无效或缺失的值返回 ``None`` —— 即使没有 ``type:`` 字段的遗留文件也能继续工作，未知类型的文件会优雅地降级。
    """
    if raw is None:
        return None
    return raw if raw in MEMORY_TYPES else None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Types section — injected into extraction prompt
# ---------------------------------------------------------------------------

TYPES_SECTION = """\
## 记忆类型

你可以在你的记忆系统中存储几种离散类型的记忆：

<types>
<type>
    <name>user_profile</name>
    <description>包含有关用户身份、生活习惯、社会关系、健康和饮食偏好的信息。优秀的 user_profile 记忆能帮助你使未来的行为适应用户的特定情况。例如：“对花生过敏”、“喜欢早起”、“养了两只猫”、“是 X 公司的后端工程师”。目的是为了提供帮助——避免记录可能被视为负面评价或与协助用户无关的记忆。</description>
    <when_to_save>当你了解到有关用户身份、偏好、习惯、社会关系或个人背景的任何细节时</when_to_save>
    <how_to_use>当你的回答应当结合用户的个人档案时——例如，推荐避开过敏原的餐厅、尊重其早起习惯的日程建议</how_to_use>
    <examples>
    user: 我对花生过敏，帮我推荐个餐厅
    assistant: [saves user_profile memory: 用户对花生过敏 — 推荐餐厅时必须排除含花生菜品]

    user: 我是做后端开发的，最近在学 Rust
    assistant: [saves user_profile memory: 后端开发工程师，正在学习 Rust — 技术讨论可以使用专业术语，Rust 相关问题给入门级解释]
    </examples>
</type>
<type>
    <name>interaction_rules</name>
    <description>用户给你的关于如何进行对话的指导——包括要避免什么以及要继续做什么。这是一种非常重要的记忆类型。在失败和成功中都要进行记录：如果你只保存纠正，你固然会避免过去的错误，但也会偏离已被证实的正确方法。请包含*原因*，以便稍后判断边缘情况。</description>
    <when_to_save>任何时候用户纠正你的方法（“别这样”、“不要”、“停止做某事”）或确认非显而易见的方法有效（“对，就这样”、“完美”，无异议地接受一个不寻常的选择）</when_to_save>
    <how_to_use>让这些记忆引导你的行为，这样用户就无需多次提供相同的指导</how_to_use>
    <body_structure>以规则本身开头，然后是一行 **Why:**（原因）和一行 **How to apply:**（如何应用）</body_structure>
    <examples>
    user: 回答不要太长，简洁点就行
    assistant: [saves interaction_rules memory: 用户偏好简洁回答，不要长篇大论。How to apply: 回答控制在3-5句以内，除非用户明确要求详细解释]

    user: 对，用表格展示比较好，以后都这样
    assistant: [saves interaction_rules memory: 对比类信息优先使用表格展示 — 用户确认了这种方式更清晰]
    </examples>
</type>
<type>
    <name>active_context</name>
    <description>有关进行中的任务、计划、待办事项、即将到来的日程安排和时效性事件的信息。这些状态变化很快——保持你的理解是最新的。保存时务必将相对日期转换为绝对日期（例如，“下周四” → “2026-03-05”）。</description>
    <when_to_save>当你了解到进行中的计划、待办事项、截止日期或即将发生的事件时</when_to_save>
    <how_to_use>利用这些来了解用户当前的上下文、预测需求，并主动提供相关的协助</how_to_use>
    <body_structure>以事实开头，然后是一行 **Why:**（动机）和一行 **How to apply:**（如何应用）</body_structure>
    <examples>
    user: 下周三有个重要的演讲，帮我准备下大纲
    assistant: [saves active_context memory: 2026-03-05（周三）有重要演讲 — 可能需要后续帮助完善内容和准备]

    user: 我正在减肥，目标是三个月减10斤
    assistant: [saves active_context memory: 正在进行减肥计划，目标三个月减10斤（截止约2026-06-01） — 饮食和运动建议应考虑此目标]
    </examples>
</type>
<type>
    <name>knowledge_base</name>
    <description>存储重要的摘要、外部账号记录、联系人信息、专业术语以及指向外部系统的指针。这些记忆使你能够记住到哪里寻找最新的信息。</description>
    <when_to_save>当你了解到在未来的对话中可能有用的外部资源、重要联系人或专业信息时</when_to_save>
    <how_to_use>当用户引用外部系统、联系人或之前讨论过的专业知识时</how_to_use>
    <examples>
    user: 我的 GitHub 账号是 yanyu-dev，常用邮箱是 yanyu@example.com
    assistant: [saves knowledge_base memory: GitHub 账号 yanyu-dev, 常用邮箱 yanyu@example.com]

    user: 如果要查球场预订，去「约球」App 的「场地」页面看
    assistant: [saves knowledge_base memory: 球场预订信息在「约球」App 的「场地」页面查看]
    </examples>
</type>
</types>
"""


# ---------------------------------------------------------------------------
# What NOT to save
# ---------------------------------------------------------------------------

WHAT_NOT_TO_SAVE = """\
## 记忆中切勿保存的内容

- 临时任务细节：在本次对话结束后将变得毫无意义的进行中工作。
- 可以通过再次询问用户重新获取的信息（例如：“会议在几点？”）。
- 当前对话上下文——聊天历史已经包含它。
- 用户文件或笔记中已经记录的任何内容。
- 活动摘要或对话日志——如果用户要求你保存，请询问其中有什么是*令人惊讶*或*非显而易见*的。那才是值得保留的部分。
"""


# ---------------------------------------------------------------------------
# When to access memories
# ---------------------------------------------------------------------------

WHEN_TO_ACCESS = """\
## 何时读取记忆

- 当记忆似乎与用户当前的请求相关，或者用户引用了之前对话的工作时。
- 当用户明确要求你检查、召回或记住时，你必须读取记忆。
- 如果用户说*忽略*或*不使用*记忆：请当作 MEMORY.md 为空来处理。不要应用、引用、对比或提及记忆内容。
- 记忆记录可能会随着时间的推移而过时。将记忆作为在特定时间点上为真的上下文来使用。在完全基于记忆进行回答之前，尽可能验证它是否仍然正确。如果召回的记忆与当前信息发生冲突，请相信你目前观察到的情况——并更新或删除过时的记忆。
"""


# ---------------------------------------------------------------------------
# Trusting recalled memories
# ---------------------------------------------------------------------------

TRUSTING_RECALL = """\
## 在根据记忆行动之前

指明了特定文件、联系人或细节的记忆是一个断言，即它在*写入记忆时*存在。从那时起它可能已经发生了变化。在根据其行动之前：

- 如果记忆指明了文件路径：检查该文件是否存在。
- 如果用户即将根据你的建议采取行动（而不仅仅是询问历史），请先进行验证。

“记忆说 X”不等于“X 现在仍然为真”。

总结过去状态的记忆是被时间冻结的。如果用户询问*当前*状态，相比于召回快照，更倾向于直接询问用户或直接检查。
"""


# ---------------------------------------------------------------------------
# Frontmatter format example
# ---------------------------------------------------------------------------

MEMORY_FRONTMATTER_EXAMPLE = """\
```markdown
---
name: {{记忆名称}}
description: {{单行描述 — 用于决定在未来对话中的相关性，因此请具体一些}}
type: {{user_profile, interaction_rules, active_context, knowledge_base}}
---

{{记忆内容 — 对于 interaction_rules/active_context 类型，结构为：规则/事实，然后是 **Why:** 和 **How to apply:** 行}}
```
"""


# ---------------------------------------------------------------------------
# Build the extraction prompt (照搬 prompts.ts: buildExtractAutoOnlyPrompt)
# ---------------------------------------------------------------------------

def build_extraction_prompt(
    new_message_count: int,
    existing_memories: str,
    memory_dir: str,
) -> str:
    """构建后台记忆提取子 Agent 的提示词。

    密切遵循 Claude Code 的 ``buildExtractAutoOnlyPrompt`` 结构：
    开头 → 类型 → 切勿保存的内容 → 如何保存 → 步数预算 → 现有索引。

    参数:
        new_message_count: 自上次提取以来新产生的模型可见消息数。
        existing_memories: 现有记忆文件的格式化清单（来自 MEMORY.md）。
        memory_dir: 记忆目录的绝对路径。

    返回:
        完整的提取提示词字符串。
    """
    return f"""\
你是记忆提取子 Agent。请分析上面大约 {new_message_count} 条最近的消息，并使用它们更新持久化记忆系统。

你的可用工具：read_file, write_file（且只能在 {memory_dir} 目录下操作）。

你的轮数预算有限。高效的策略是：
第 1 轮 — 并行读取你可能需要更新的所有文件。
第 2 轮 — 并行写入所有文件。
切勿在多轮交互中交替进行读写。

你必须且仅能使用最近大约 {new_message_count} 条消息的内容来更新记忆。不要虚构信息。

{TYPES_SECTION}
{WHAT_NOT_TO_SAVE}

## 如何保存记忆

两步流程：

**第 1 步** — 将记忆写入单独的主题文件（例如 ``user_profile_hobbies.md``），使用 YAML frontmatter 格式：
{MEMORY_FRONTMATTER_EXAMPLE}

**第 2 步** — 在 ``{memory_dir}/MEMORY.md`` 中添加一条指针。每行最多 150 个字符：
``- [标题](filename.md) — 单行摘要``

## 现有记忆文件
{existing_memories if existing_memories else "(目前尚无现有记忆。)"}

在写入前检查此列表——更新现有文件而不是创建重复项。
如果你发现没有什么值得保存的，输出简短的提示并停止。

{WHEN_TO_ACCESS}
{TRUSTING_RECALL}
"""


# ---------------------------------------------------------------------------
# System prompt section for main agent (injected by MemoryMiddleware)
# ---------------------------------------------------------------------------

def build_memory_system_prompt(
    memory_dir: str,
    memory_index_content: str,
) -> str:
    """构建主 Agent 的记忆系统提示词部分。

    参数:
        memory_dir: 记忆目录的绝对路径。
        memory_index_content: MEMORY.md（索引文件）的内容。

    返回:
        描述记忆系统的系统提示词部分。
    """
    lines = [line for line in (memory_index_content.splitlines() if memory_index_content else []) if line.strip()]
    
    if not lines:
        preview = "(目前尚无已保存的记忆。)"
        more_notice = ""
    else:
        preview = "\n".join(lines[:10])
        if len(lines) > 10:
            more_notice = f"\n\n*(注意：还存在 {len(lines) - 10} 条记忆。使用 `read_file` 工具查看 `{memory_dir}/MEMORY.md` 以获取完整列表。)*"
        else:
            more_notice = ""

    return f"""\
## 记忆系统

你配备了位于 `{memory_dir}/` 的持久化记忆管理系统。
你可以使用 `memorize` 和 `recall` 工具来主动管理和搜索这些记录。

**记忆索引预览（MEMORY.md 的前 10 条记录）：**
{preview}{more_notice}

要发现更多已保存的记忆，请使用 `read_file` 工具查看完整的 `{memory_dir}/MEMORY.md` 索引文件。
要读取特定记忆的详细内容，请使用 `read_file` 工具查看索引中引用的相应 `.md` 文件。

{WHEN_TO_ACCESS}
"""


__all__ = [
    "MEMORY_TYPES",
    "MemoryType",
    "parse_memory_type",
    "TYPES_SECTION",
    "WHAT_NOT_TO_SAVE",
    "WHEN_TO_ACCESS",
    "TRUSTING_RECALL",
    "MEMORY_FRONTMATTER_EXAMPLE",
    "build_extraction_prompt",
    "build_memory_system_prompt",
]
