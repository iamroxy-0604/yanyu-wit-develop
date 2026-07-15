"""Heartbeat 定时任务管理模块。

与 agent/ 平级的独立模块，提供：
- agent 类型任务（由精简版 agent 执行）
- script 类型任务（由 subprocess 执行 .sh/.py 脚本）
- 人性化调度配置（每日/每周/每月）
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Awaitable

from .models import HeartbeatJob, ScheduleConfig, now_ms
from .store import HeartbeatStore
from .scheduler import HeartbeatScheduler, compute_next_run_at_ms
from .executor import TaskExecutor
from .tools import create_heartbeat_tools


logger = logging.getLogger(__name__)


class HeartbeatManager:
    """Heartbeat 模块的统一管理器。

    协调 store、scheduler、executor 的生命周期，
    对外提供简洁的接口供 runtime 和 middleware 使用。
    """

    def __init__(self, workspace_root: str, db_instance: Any = None) -> None:
        self.workspace_root = workspace_root
        self.heartbeat_dir = os.path.join(workspace_root, "heartbeat")

        self.store = HeartbeatStore(self.heartbeat_dir, db_instance=db_instance)
        self.scheduler = HeartbeatScheduler(self.store)
        self.executor = TaskExecutor(
            store=self.store,
            scheduler=self.scheduler,
            workspace_root=workspace_root,
        )

        self._initialized = False
        self._init_lock = asyncio.Lock()

        # 将 executor 作为 scheduler 的回调
        self.scheduler.set_execute_callback(self.executor.execute_job)

    async def initialize(self) -> None:
        """初始化 heartbeat 模块：加载数据、启动调度器。"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            await self.store.load()

            # 重新计算所有活跃任务的 next_run_at_ms
            now = now_ms()
            for job in self.store.jobs:
                if job.enabled and job.state.next_run_at_ms is None:
                    job.state.next_run_at_ms = compute_next_run_at_ms(
                        job.schedule, now
                    )
            await self.store.save()

            # 启动调度器（SaaS 模式下不再初始化 HeartbeatManager，此处始终启动）
            await self.scheduler.start()

            self._initialized = True
            logger.info(
                "HeartbeatManager initialized: %d jobs loaded",
                len(self.store.jobs),
            )

    async def shutdown(self) -> None:
        """停止调度器。"""
        await self.scheduler.stop()

    def set_agent_builder(self, builder: Callable[..., Awaitable[Any]]) -> None:
        """设置 agent 构建回调（由 runtime 层注入）。"""
        self.executor.set_agent_builder(builder)

    def build_summary(self) -> str:
        """构建当前任务概要（用于 system prompt 注入）。"""
        jobs = self.store.list_jobs(include_disabled=True)
        if not jobs:
            return "（暂无定时任务）"
        lines: list[str] = []
        for j in jobs:
            status = HeartbeatStore._status_icon(j)
            schedule = j.schedule.human_readable()
            type_label = "🤖" if j.type == "agent" else "📜"
            lines.append(f"- [{j.id}] {type_label} {status} {j.name} | {schedule}")
        return "\n".join(lines)

    def get_tools(self):
        """获取任务管理工具列表。"""
        return create_heartbeat_tools(self)


__all__ = [
    "HeartbeatManager",
    "HeartbeatStore",
    "HeartbeatScheduler",
    "TaskExecutor",
]
