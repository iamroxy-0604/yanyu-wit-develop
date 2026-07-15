"""压缩中间件提示词模板。"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# No-tools preamble (照搬 compact/prompt.ts: NO_TOOLS_PREAMBLE)
# ---------------------------------------------------------------------------

NO_TOOLS_PREAMBLE = """\
重要提示：仅以文本形式回复。切勿调用任何工具。

- 切勿使用 read_file、write_file、execute 或任何其他工具。
- 你已拥有上述对话中所需的所有上下文。
- 工具调用将被拒绝，且会浪费你仅有的一次交互机会——从而导致任务失败。
- 你的所有回复必须是纯文本：一个 <analysis> 块后跟一个 <summary> 块。

"""


# ---------------------------------------------------------------------------
# Detailed analysis instruction (照搬 DETAILED_ANALYSIS_INSTRUCTION_BASE)
# ---------------------------------------------------------------------------

DETAILED_ANALYSIS_INSTRUCTION = """\
在提供最终摘要之前，将你的分析用 <analysis> 标签包裹起来，以整理你的思路并确保你已涵盖所有必要的内容。在分析过程中：

1. 按时间顺序分析对话的每条消息和每个部分。对于每个部分，彻底识别：
   - 用户的明确请求和意图
   - 你处理用户请求的方法
   - 关键决策和重要上下文
   - 具体细节（如文件名、重要数据、行动项）
   - 发生的错误以及如何修复的
   - 特别注意用户的具体反馈，尤其是用户让你以不同的方式做某事时。
2. 仔细检查完整性，彻底处理每个要求的元素。"""


# ---------------------------------------------------------------------------
# Base compact prompt (照搬 BASE_COMPACT_PROMPT, adapted for personal assistant)
# ---------------------------------------------------------------------------

BASE_COMPACT_PROMPT = f"""\
你的任务是为迄今为止的对话创建一个详细的摘要，密切关注用户的明确请求和你之前的操作。
该摘要应详尽地捕获重要的细节、上下文和决策，这对于在不丢失上下文的情况下继续对话至关重要。

{DETAILED_ANALYSIS_INSTRUCTION}

你的摘要应包括以下部分：

1. 核心请求与意图：详细捕获用户所有的明确请求和意图
2. 关键上下文：列出讨论过的所有重要上下文、主题和领域。
3. 重要细节：列出提到的具体细节——姓名、日期、偏好、事实、文件内容等。在适用时包含具体信息。
4. 问题与解决方案：列出遇到的任何问题以及如何解决的。特别注意用户的反馈和纠正。
5. 问题解决：记录已解决的问题和任何正在进行的故障排除工作。
6. 所有用户消息：列出所有非工具结果的用户消息。这些对于理解用户的反馈和改变意图至关重要。
7. 待办任务：概述明确要求你处理的任何待办任务。
8. 当前工作：详细描述在请求此摘要之前正在处理的工作，特别注意用户和助理的最新消息。
9. 可选的下一步：列出与最新工作相关的下一步。重要提示：确保此步骤直接符合用户最近的明确请求。如果你最后一项任务已经结束，仅在明确要求时列出下一步。不要开始处理其他不相关的请求。
   如果有下一步，请包含最新对话中的直接引用，准确显示你正在处理什么任务以及在哪里中断的。

以下是你的输出结构示例：

<example>
<analysis>
[你的思考过程，确保所有要点都得到彻底和准确的覆盖]
</analysis>

<summary>
1. 核心请求与意图：
   [详细描述]

2. 关键上下文：
   - [上下文 1]
   - [上下文 2]
   - [...]

3. 重要细节：
   - [细节 1]
   - [细节 2]
   - [...]

4. 问题与解决方案：
    - [问题描述]:
      - [如何解决的]
      - [用户的反馈（如果有）]
    - [...]

5. 问题解决：
   [已解决问题和正在进行的故障排除的描述]

6. 所有用户消息:
    - [详细的非工具调用的用户消息]
    - [...]

7. 待办任务：
   - [任务 1]
   - [任务 2]
   - [...]

8. 当前工作：
   [当前工作的精确描述]

9. 可选的下一步：
   [可选的下一步操作]

</summary>
</example>

请根据目前的对话提供你的摘要，遵循此结构并确保你回复的准确性和彻底性。
"""


# ---------------------------------------------------------------------------
# No-tools trailer
# ---------------------------------------------------------------------------

NO_TOOLS_TRAILER = (
    "\n\n温馨提示：切勿调用任何工具。仅以纯文本形式回复 — "
    "一个 <analysis> 块后跟一个 <summary> 块。 "
    "工具调用将被拒绝，你将无法完成任务。"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """构建完整的压缩提示词。

    参数:
        custom_instructions: 可选的额外摘要指令。

    返回:
        完整的压缩提示词字符串。
    """
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT

    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"

    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(summary: str) -> str:
    """通过剥离 ``<analysis>`` 暂存器来格式化原始压缩摘要。

    密切遵循 Claude Code 的 ``formatCompactSummary``:
    - 剥离 ``<analysis>`` 部分（起草暂存器）。
    - 提取并格式化 ``<summary>`` 部分。
    - 清理多余的空格。

    参数:
        summary: 来自压缩模型的原始摘要字符串。

    返回:
        已剥离分析部分的格式化摘要。
    """
    import re

    formatted = summary

    # Strip analysis section
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", formatted)

    # Extract and format summary section
    match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content}",
            formatted,
        )

    # Clean up extra whitespace
    formatted = re.sub(r"\n\n+", "\n\n", formatted)

    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up_questions: bool = False,
) -> str:
    """构建在压缩后注入的面向用户的摘要消息。

    密切遵循 Claude Code 的 ``getCompactUserSummaryMessage``。

    参数:
        summary: 来自压缩模型的原始摘要。
        suppress_follow_up_questions: 如果为 True，指示模型在不提问的情况下继续。

    返回:
        用于注入到对话中的格式化摘要消息。
    """
    formatted = format_compact_summary(summary)

    base = (
        "本会话是之前超出上下文的对话的延续。以下摘要涵盖了对话的前面部分。\n\n"
        f"{formatted}"
    )

    if suppress_follow_up_questions:
        return (
            f"{base}\n\n"
            "从中断的地方继续对话，不要向用户提出任何进一步的问题。直接恢复——不要提及该摘要，"
            "不要概括发生过的事情，不要以“我将继续”或类似内容开头。接手最后一项任务，就像中断从未发生过一样。"
        )

    return base


__all__ = [
    "get_compact_prompt",
    "format_compact_summary",
    "get_compact_user_summary_message",
]
