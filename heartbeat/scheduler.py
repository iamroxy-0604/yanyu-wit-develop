"""Heartbeat 定时任务管理模块 - asyncio 调度引擎。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from .models import (
    HeartbeatJob,
    ScheduleConfig,
    Frequency,
    now_ms,
    BACKOFF_SCHEDULE,
)

if TYPE_CHECKING:
    from .store import HeartbeatStore

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Schedule Computation
# ═══════════════════════════════════════════════════════════════════════════

def compute_next_run_at_ms(schedule: ScheduleConfig, from_ms: float) -> float | None:
    """根据调度配置计算下次执行时间（毫秒时间戳）。"""
    freq = schedule.frequency

    if freq in (Frequency.ONCE, "once"):
        if not schedule.once_at:
            return None
        try:
            dt = datetime.fromisoformat(schedule.once_at)
            at_ms = dt.timestamp() * 1000
            return at_ms if at_ms > from_ms else None
        except (ValueError, TypeError):
            return None

    # daily / weekly / monthly → 转换为 cron 后计算
    cron_expr = schedule.to_cron_expr()
    if not cron_expr:
        return None

    try:
        from croniter import croniter
        import zoneinfo

        tz = zoneinfo.ZoneInfo(schedule.timezone or "Asia/Shanghai")
        now_dt = datetime.fromtimestamp(from_ms / 1000, tz=tz)
        cron = croniter(cron_expr, now_dt)
        next_dt = cron.get_next(datetime)
        return next_dt.timestamp() * 1000
    except Exception as e:
        logger.warning("Failed to compute schedule: %s", e)
        return None


def compute_next_run_after_execution(
    job: HeartbeatJob, ended_at_ms: float
) -> float | None:
    """任务执行完成后计算下次运行时间。"""
    freq = job.schedule.frequency

    if freq in (Frequency.ONCE, "once"):
        return None  # 一次性任务不再调度

    return compute_next_run_at_ms(job.schedule, ended_at_ms)


def error_backoff_ms(consecutive_errors: int) -> float:
    """根据连续错误次数返回退避延迟（毫秒）。"""
    idx = min(consecutive_errors - 1, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[max(0, idx)] * 1000


# ═══════════════════════════════════════════════════════════════════════════
# HeartbeatScheduler
# ═══════════════════════════════════════════════════════════════════════════

class HeartbeatScheduler:
    """基于 asyncio 的定时任务调度器。

    纯 Python 实现，不依赖系统 cron。
    通过 execute_callback 将到期任务分发给 executor 执行。
    """

    def __init__(
        self,
        store: HeartbeatStore,
        execute_callback: Callable[[HeartbeatJob], Awaitable[Any]] | None = None,
    ) -> None:
        self._store = store
        self._execute_callback = execute_callback
        self._task: asyncio.Task | None = None
        self._reschedule_event = asyncio.Event()
        self._stopped = False

    def set_execute_callback(
        self, callback: Callable[[HeartbeatJob], Awaitable[Any]]
    ) -> None:
        """设置任务执行回调。"""
        self._execute_callback = callback

    async def start(self) -> None:
        """启动调度器后台任务。"""
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("HeartbeatScheduler started")

    async def stop(self) -> None:
        """停止调度器。"""
        self._stopped = True
        self._reschedule_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("HeartbeatScheduler stopped")

    def reschedule(self) -> None:
        """通知调度器重新计算下次唤醒时间。"""
        self._reschedule_event.set()

    async def _scheduler_loop(self) -> None:
        """后台调度主循环。"""
        while not self._stopped:
            try:
                await self._store.load()
                now = now_ms()
                due_jobs = self._find_due_jobs(now)

                for job in due_jobs:
                    await self._dispatch_job(job)

                delay = self._compute_sleep_seconds()
                if delay is None:
                    self._reschedule_event.clear()
                    await self._reschedule_event.wait()
                else:
                    clamped_delay = min(max(delay, 1.0), 60.0)
                    self._reschedule_event.clear()
                    try:
                        await asyncio.wait_for(
                            self._reschedule_event.wait(),
                            timeout=clamped_delay,
                        )
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("HeartbeatScheduler loop error")
                await asyncio.sleep(5)

    def _find_due_jobs(self, now_ms_val: float) -> list[HeartbeatJob]:
        """找出所有已到期且可执行的任务。"""
        due: list[HeartbeatJob] = []
        for job in self._store.jobs:
            if not job.enabled:
                continue
            if job.state.running:
                continue
            next_run = job.state.next_run_at_ms
            if next_run is not None and next_run <= now_ms_val:
                due.append(job)
        return due

    def _compute_sleep_seconds(self) -> float | None:
        """计算距离最近到期任务的等待秒数。"""
        now = now_ms()
        nearest: float | None = None
        for job in self._store.jobs:
            if not job.enabled or job.state.running:
                continue
            nxt = job.state.next_run_at_ms
            if nxt is not None:
                if nearest is None or nxt < nearest:
                    nearest = nxt
        if nearest is None:
            return None
        delay_ms = nearest - now
        return max(delay_ms / 1000, 0)

    async def _dispatch_job(self, job: HeartbeatJob) -> None:
        """将到期任务分发给执行器。"""
        if self._execute_callback:
            # 异步执行，不阻塞调度循环
            asyncio.create_task(self._execute_callback(job))
        else:
            logger.warning(
                "No execute callback set, skipping job: %s (%s)", job.name, job.id
            )
