"""Heartbeat 定时任务管理模块 - Agent 可用的任务管理工具。

这些工具通过 HeartbeatToolsMiddleware 注入到常规对话 agent 中，
让用户可以在对话中管理定时任务。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, StructuredTool

if TYPE_CHECKING:
    from . import HeartbeatManager

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Tool Schemas
# ═══════════════════════════════════════════════════════════════════════════

class HeartbeatAddSchema(BaseModel):
    """添加定时任务。"""
    name: str = Field(description="任务名称，简短描述（如 '每日新闻摘要'）")
    description: str = Field(default="", description="任务的详细描述")
    task_type: str = Field(
        default="agent",
        description="任务类型：'agent'（智能体任务）或 'script'（脚本任务）",
    )
    frequency: str = Field(
        description=(
            "调度频率：'daily'（每天）、'weekly'（每周）、"
            "'monthly'（每月）、'once'（一次性）"
        )
    )
    time: str = Field(
        default="09:00",
        description="执行时间，HH:MM 格式，如 '09:00'、'14:30'",
    )
    weekdays: list[int] = Field(
        default_factory=list,
        description="每周执行时的星期选择，0=周一, 1=周二, ..., 6=周日。仅 frequency='weekly' 时有效",
    )
    monthdays: list[int] = Field(
        default_factory=list,
        description="每月执行时的日期选择，1-31。仅 frequency='monthly' 时有效",
    )
    instruction: str = Field(
        default="",
        description="任务触发时发送给你自己（agent）的消息/指令。task_type='agent' 时必填",
    )
    script_path: str = Field(
        default="",
        description="脚本路径（相对于 heartbeat 目录）。task_type='script' 时必填",
    )
    once_at: str = Field(
        default="",
        description="一次性任务的执行时间，ISO 8601 格式。仅 frequency='once' 时有效",
    )
    timezone: str = Field(default="Asia/Shanghai", description="时区")


class HeartbeatRemoveSchema(BaseModel):
    """删除定时任务。"""
    job_id: str = Field(description="要删除的任务 ID")


class HeartbeatListSchema(BaseModel):
    """列出定时任务。"""
    include_disabled: bool = Field(
        default=True, description="是否包含已暂停/已完成的任务"
    )


class HeartbeatUpdateSchema(BaseModel):
    """更新定时任务。"""
    job_id: str = Field(description="要更新的任务 ID")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    enabled: bool | None = Field(default=None, description="是否启用")
    frequency: str | None = Field(default=None, description="新调度频率")
    time: str | None = Field(default=None, description="新执行时间 (HH:MM)")
    weekdays: list[int] | None = Field(default=None, description="新星期选择")
    monthdays: list[int] | None = Field(default=None, description="新日期选择")
    instruction: str | None = Field(default=None, description="新指令")


# ═══════════════════════════════════════════════════════════════════════════
# Tool Creation
# ═══════════════════════════════════════════════════════════════════════════

def create_heartbeat_tools(manager: HeartbeatManager) -> list[BaseTool]:
    """创建定时任务管理工具列表。"""
    return [
        _create_add_tool(manager),
        _create_remove_tool(manager),
        _create_list_tool(manager),
        _create_update_tool(manager),
    ]


def _create_add_tool(manager: HeartbeatManager) -> BaseTool:
    from .models import HeartbeatJob, ScheduleConfig, generate_job_id, now_ms
    from .scheduler import compute_next_run_at_ms

    def sync_fn(**kwargs) -> str:
        raise RuntimeError("Use async version")

    async def async_fn(
        name: str,
        description: str = "",
        task_type: str = "agent",
        frequency: str = "daily",
        time: str = "09:00",
        weekdays: list[int] = [],
        monthdays: list[int] = [],
        instruction: str = "",
        script_path: str = "",
        once_at: str = "",
        timezone: str = "Asia/Shanghai",
    ) -> str:
        if task_type not in ("agent", "script"):
            return f"错误：task_type 必须是 'agent' 或 'script'，收到 '{task_type}'"
        if frequency not in ("daily", "weekly", "monthly", "once"):
            return f"错误：frequency 必须是 'daily'、'weekly'、'monthly' 或 'once'，收到 '{frequency}'"

        now = now_ms()
        schedule = ScheduleConfig(
            frequency=frequency,
            time=time,
            weekdays=weekdays,
            monthdays=monthdays,
            once_at=once_at or None,
            timezone=timezone,
        )
        job = HeartbeatJob(
            id=generate_job_id(),
            name=name,
            description=description,
            enabled=True,
            type=task_type,
            instruction=instruction,
            script_path=script_path,
            schedule=schedule,
            created_at_ms=now,
            updated_at_ms=now,
        )
        job.state.next_run_at_ms = compute_next_run_at_ms(schedule, now)

        await manager.store.add_job(job)
        manager.scheduler.reschedule()

        next_run = "-"
        if job.state.next_run_at_ms:
            try:
                dt = datetime.fromtimestamp(job.state.next_run_at_ms / 1000)
                next_run = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                pass

        return (
            f"✅ 定时任务已创建\n"
            f"- ID: {job.id}\n"
            f"- 名称: {job.name}\n"
            f"- 类型: {task_type}\n"
            f"- 调度: {schedule.human_readable()}\n"
            f"- 下次执行: {next_run}"
        )

    return StructuredTool.from_function(
        name="heartbeat_add",
        description=(
            "添加一个定时任务。支持 'agent'（智能体任务）和 'script'（脚本任务）两种类型。"
            "支持 'daily'（每天）、'weekly'（每周）、'monthly'（每月）、'once'（一次性）四种调度频率。"
        ),
        func=sync_fn,
        coroutine=async_fn,
        infer_schema=False,
        args_schema=HeartbeatAddSchema,
    )


def _create_remove_tool(manager: HeartbeatManager) -> BaseTool:
    def sync_fn(**kwargs) -> str:
        raise RuntimeError("Use async version")

    async def async_fn(job_id: str) -> str:
        removed = await manager.store.remove_job(job_id)
        if removed:
            manager.scheduler.reschedule()
            return f"✅ 任务 {job_id} 已删除"
        return f"❌ 未找到任务 {job_id}"

    return StructuredTool.from_function(
        name="heartbeat_remove",
        description="删除一个定时任务。需要提供任务 ID。",
        func=sync_fn,
        coroutine=async_fn,
        infer_schema=False,
        args_schema=HeartbeatRemoveSchema,
    )


def _create_list_tool(manager: HeartbeatManager) -> BaseTool:
    def sync_fn(**kwargs) -> str:
        raise RuntimeError("Use async version")

    async def async_fn(include_disabled: bool = True) -> str:
        jobs = manager.store.list_jobs(include_disabled=include_disabled)
        if not jobs:
            return "暂无定时任务。"

        lines: list[str] = ["当前定时任务列表：", ""]
        for j in jobs:
            status = manager.store._status_icon(j)
            schedule = j.schedule.human_readable()
            type_label = "🤖 智能体" if j.type == "agent" else "📜 脚本"

            next_run = "-"
            if j.state.next_run_at_ms:
                try:
                    dt = datetime.fromtimestamp(j.state.next_run_at_ms / 1000)
                    next_run = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass

            lines.append(
                f"- **{j.name}** [{j.id}]\n"
                f"  类型: {type_label} | 状态: {status} | 调度: {schedule} | "
                f"下次执行: {next_run}\n"
                f"  描述: {j.description}"
            )
        return "\n".join(lines)

    return StructuredTool.from_function(
        name="heartbeat_list",
        description="列出所有定时任务，包括状态、调度规则和下次执行时间。",
        func=sync_fn,
        coroutine=async_fn,
        infer_schema=False,
        args_schema=HeartbeatListSchema,
    )


def _create_update_tool(manager: HeartbeatManager) -> BaseTool:
    def sync_fn(**kwargs) -> str:
        raise RuntimeError("Use async version")

    async def async_fn(
        job_id: str,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        frequency: str | None = None,
        time: str | None = None,
        weekdays: list[int] | None = None,
        monthdays: list[int] | None = None,
        instruction: str | None = None,
    ) -> str:
        patch: dict[str, Any] = {}
        if name is not None:
            patch["name"] = name
        if description is not None:
            patch["description"] = description
        if enabled is not None:
            patch["enabled"] = enabled
        if instruction is not None:
            patch["instruction"] = instruction

        # 构建 schedule patch
        sched_patch = {}
        if frequency is not None:
            sched_patch["frequency"] = frequency
        if time is not None:
            sched_patch["time"] = time
        if weekdays is not None:
            sched_patch["weekdays"] = weekdays
        if monthdays is not None:
            sched_patch["monthdays"] = monthdays
        if sched_patch:
            # 获取当前 job 的 schedule 作为基础
            job = manager.store.get_job(job_id)
            if job:
                from dataclasses import asdict
                current = asdict(job.schedule)
                current.update(sched_patch)
                patch["schedule"] = current

        if not patch:
            return "未提供任何修改内容"

        job = await manager.store.update_job(job_id, patch)
        if job is None:
            return f"❌ 未找到任务 {job_id}"

        # 重新计算 next_run
        if job.enabled:
            from .scheduler import compute_next_run_at_ms
            from .models import now_ms
            job.state.next_run_at_ms = compute_next_run_at_ms(job.schedule, now_ms())
            await manager.store.save()

        manager.scheduler.reschedule()
        return f"✅ 任务 {job.name} [{job.id}] 已更新"

    return StructuredTool.from_function(
        name="heartbeat_update",
        description=(
            "更新一个定时任务的配置。可修改名称、描述、启用状态、调度规则或执行指令。"
        ),
        func=sync_fn,
        coroutine=async_fn,
        infer_schema=False,
        args_schema=HeartbeatUpdateSchema,
    )
