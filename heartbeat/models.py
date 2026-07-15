"""Heartbeat 定时任务管理模块 - 数据模型。"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

MAX_RUN_LOG_ENTRIES = 100
BACKOFF_SCHEDULE = [30, 60, 300, 900, 3600]  # seconds


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════

class JobType(str, Enum):
    """任务类型。"""
    AGENT = "agent"    # 由 agent 执行的任务
    SCRIPT = "script"  # 由脚本执行的任务


class Frequency(str, Enum):
    """调度频率。"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ONCE = "once"


# ═══════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ScheduleConfig:
    """人性化的调度配置。"""
    frequency: str = "daily"          # "daily" | "weekly" | "monthly" | "once"
    time: str = "09:00"               # HH:MM 格式
    weekdays: list[int] = field(default_factory=list)   # 0=周一..6=周日
    monthdays: list[int] = field(default_factory=list)  # 1-31
    once_at: str | None = None        # ISO 时间字符串 (frequency=once)
    timezone: str = "Asia/Shanghai"

    def to_cron_expr(self) -> str | None:
        """将人性化配置转换为 cron 表达式。"""
        if self.frequency == Frequency.ONCE or self.frequency == "once":
            return None  # 一次性任务不用 cron

        hour, minute = self._parse_time()

        if self.frequency == Frequency.DAILY or self.frequency == "daily":
            return f"{minute} {hour} * * *"

        if self.frequency == Frequency.WEEKLY or self.frequency == "weekly":
            if not self.weekdays:
                return f"{minute} {hour} * * *"  # 未指定星期，默认每天
            # cron 中 0=周日, 1=周一... 我们的模型 0=周一, 6=周日
            cron_days = ",".join(str((d + 1) % 7) for d in sorted(self.weekdays))
            return f"{minute} {hour} * * {cron_days}"

        if self.frequency == Frequency.MONTHLY or self.frequency == "monthly":
            if not self.monthdays:
                return f"{minute} {hour} 1 * *"  # 未指定日期，默认1号
            days = ",".join(str(d) for d in sorted(self.monthdays))
            return f"{minute} {hour} {days} * *"

        return None

    def _parse_time(self) -> tuple[int, int]:
        """解析 HH:MM 时间字符串。"""
        try:
            parts = self.time.split(":")
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return 9, 0

    def human_readable(self) -> str:
        """返回人类可读的调度描述。"""
        if self.frequency in (Frequency.ONCE, "once"):
            if self.once_at:
                try:
                    dt = datetime.fromisoformat(self.once_at)
                    return f"一次性: {dt.strftime('%Y-%m-%d %H:%M')}"
                except (ValueError, TypeError):
                    pass
            return "一次性"

        time_str = self.time or "09:00"

        if self.frequency in (Frequency.DAILY, "daily"):
            return f"每天 {time_str}"

        if self.frequency in (Frequency.WEEKLY, "weekly"):
            if self.weekdays:
                day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                days = ", ".join(day_names[d] for d in sorted(self.weekdays) if 0 <= d <= 6)
                return f"每周 {days} {time_str}"
            return f"每周 {time_str}"

        if self.frequency in (Frequency.MONTHLY, "monthly"):
            if self.monthdays:
                days = ", ".join(f"{d}日" for d in sorted(self.monthdays))
                return f"每月 {days} {time_str}"
            return f"每月 {time_str}"

        return "-"


@dataclass
class JobState:
    """任务运行时状态。"""
    next_run_at_ms: float | None = None
    last_run_at_ms: float | None = None
    last_status: str | None = None       # "ok" | "error"
    last_error: str | None = None
    last_result: str | None = None       # 最终执行结果
    consecutive_errors: int = 0
    running: bool = False


@dataclass
class HeartbeatJob:
    """定时任务。"""
    id: str
    user_id: str                 # 归属用户 ID
    name: str
    description: str
    enabled: bool
    type: str                    # "agent" | "script"
    schedule: ScheduleConfig
    # agent 类型字段
    instruction: str = ""        # 发送给 agent 的指令
    # script 类型字段
    script_path: str = ""        # 脚本路径（相对于 heartbeat 目录）
    # 元数据
    created_at_ms: float = 0
    updated_at_ms: float = 0
    state: JobState = field(default_factory=JobState)


@dataclass
class RunLogEntry:
    """单次执行记录。"""
    ts: float
    job_id: str
    status: str        # "ok" | "error"
    error: str | None
    duration_ms: float
    session_id: str
    result: str | None = None    # 最终执行结果
    artifacts: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """任务执行结果。"""
    status: str        # "ok" | "error"
    result: str | None = None
    error: str | None = None
    duration_ms: float = 0
    artifacts: list[str] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Serialization Helpers
# ═══════════════════════════════════════════════════════════════════════════

def job_to_dict(job: HeartbeatJob) -> dict:
    """将 HeartbeatJob 序列化为可 JSON 化的字典。"""
    return {
        "id": job.id,
        "user_id": job.user_id,
        "name": job.name,
        "description": job.description,
        "enabled": job.enabled,
        "type": job.type,
        "instruction": job.instruction,
        "script_path": job.script_path,
        "schedule": asdict(job.schedule),
        "created_at_ms": job.created_at_ms,
        "updated_at_ms": job.updated_at_ms,
        "state": asdict(job.state),
    }


def dict_to_job(d: dict) -> HeartbeatJob:
    """从字典反序列化为 HeartbeatJob。"""
    schedule_data = d.get("schedule", {})
    state_data = d.get("state", {})
    return HeartbeatJob(
        id=d["id"],
        user_id=d.get("user_id", ""),
        name=d.get("name", ""),
        description=d.get("description", ""),
        enabled=d.get("enabled", True),
        type=d.get("type", "agent"),
        instruction=d.get("instruction", ""),
        script_path=d.get("script_path", ""),
        schedule=ScheduleConfig(
            frequency=schedule_data.get("frequency", "daily"),
            time=schedule_data.get("time", "09:00"),
            weekdays=schedule_data.get("weekdays", []),
            monthdays=schedule_data.get("monthdays", []),
            once_at=schedule_data.get("once_at"),
            timezone=schedule_data.get("timezone", "Asia/Shanghai"),
        ),
        created_at_ms=d.get("created_at_ms", 0),
        updated_at_ms=d.get("updated_at_ms", 0),
        state=JobState(
            next_run_at_ms=state_data.get("next_run_at_ms"),
            last_run_at_ms=state_data.get("last_run_at_ms"),
            last_status=state_data.get("last_status"),
            last_error=state_data.get("last_error"),
            last_result=state_data.get("last_result"),
            consecutive_errors=state_data.get("consecutive_errors", 0),
            running=state_data.get("running", False),
        ),
    )


def now_ms() -> float:
    """当前时间戳（毫秒）。"""
    return time.time() * 1000


def generate_job_id() -> str:
    """生成任务 ID。"""
    return uuid.uuid4().hex[:12]
