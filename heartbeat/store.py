"""Heartbeat 定时任务管理模块 - 持久化层。

- heartbeat.json: 结构化数据源（原子写入）
- heartbeat.md: 自动生成的人类可读文档
- runs/<job_id>.jsonl: 执行日志
- artifacts/<job_id>/<ts>/: 执行产物
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .models import (
    HeartbeatJob,
    RunLogEntry,
    ScheduleConfig,
    JobState,
    job_to_dict,
    dict_to_job,
    now_ms,
    generate_job_id,
    MAX_RUN_LOG_ENTRIES,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

HEARTBEAT_STORE_FILE = "heartbeat.json"
HEARTBEAT_MD_FILE = "heartbeat.md"
RUNS_DIR_NAME = "runs"


class HeartbeatStore:
    """Heartbeat 任务持久化存储。"""

    def __init__(self, heartbeat_dir: str, user_id: str | None = None, db_instance: Any = None) -> None:
        self._dir = heartbeat_dir
        self._store_path = os.path.join(heartbeat_dir, HEARTBEAT_STORE_FILE)
        self._md_path = os.path.join(heartbeat_dir, HEARTBEAT_MD_FILE)
        self._runs_dir = os.path.join(heartbeat_dir, RUNS_DIR_NAME)
        self.user_id = user_id
        self._db = db_instance
        self._jobs: list[HeartbeatJob] = []
        self._lock = asyncio.Lock()

    @property
    def heartbeat_dir(self) -> str:
        return self._dir

    @property
    def jobs(self) -> list[HeartbeatJob]:
        return self._jobs

    def get_effective_user_id(self) -> str:
        """获取当前有效的 user_id。"""
        if self.user_id:
            return self.user_id
        from service.context import get_current_user_ctx
        ctx = get_current_user_ctx()
        if ctx:
            return ctx.user_id
        from cli.config import get_active_account
        return get_active_account() or "unknown"

    # --- Load / Save ---

    async def load(self) -> None:
        """从磁盘或数据库加载任务数据。"""
        async with self._lock:
            if self._db:
                from service.feature_flags import get_flags
                try:
                    if get_flags().heartbeat_mode == "multi_tenant":
                        # SaaS 模式下：加载所有租户的所有活跃/非活跃任务
                        jobs_data = await asyncio.to_thread(self._db.list_all_heartbeat_jobs)
                    else:
                        # PC 模式下：加载当前租户的任务
                        user_id = self.get_effective_user_id()
                        jobs_data = await asyncio.to_thread(self._db.list_heartbeat_jobs, user_id, include_disabled=True)

                    loaded_jobs = []
                    for j in jobs_data:
                        jd = dict(j)
                        # 解析 JSON 字段
                        if "schedule_json" in jd:
                            try:
                                jd["schedule"] = json.loads(jd["schedule_json"])
                            except Exception:
                                pass
                        if "state_json" in jd:
                            try:
                                jd["state"] = json.loads(jd["state_json"])
                            except Exception:
                                pass
                        loaded_jobs.append(dict_to_job(jd))
                    self._jobs = loaded_jobs
                except Exception as e:
                    logger.warning("Failed to load heartbeat store from DB: %s, falling back to sync file load", e)
                    await asyncio.to_thread(self._load_sync)
            else:
                await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        os.makedirs(self._dir, exist_ok=True)
        os.makedirs(self._runs_dir, exist_ok=True)
        if not os.path.exists(self._store_path):
            self._jobs = []
            return
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._jobs = [dict_to_job(j) for j in data.get("jobs", []) if j]
        except Exception as e:
            logger.warning("Failed to load heartbeat store: %s", e)
            self._jobs = []

    async def save(self) -> None:
        """写入任务数据到磁盘/数据库并重新生成 markdown。"""
        async with self._lock:
            if self._db:
                try:
                    # 将在内存中更新的 job 状态同步保存到数据库中
                    for job in self._jobs:
                        user_id = job.user_id or self.get_effective_user_id()
                        await asyncio.to_thread(
                            self._db.save_heartbeat_job_state,
                            user_id,
                            job.id,
                            asdict(job.state)
                        )
                    
                    # SaaS 模式下不写 markdown，PC 模式继续生成 heartbeat.md
                    from service.feature_flags import get_flags
                    if get_flags().heartbeat_mode != "multi_tenant":
                        self._render_markdown()
                except Exception as e:
                    logger.warning("Failed to save heartbeat states to DB: %s, falling back to sync file save", e)
                    await asyncio.to_thread(self._save_sync)
            else:
                await asyncio.to_thread(self._save_sync)

    def _save_sync(self) -> None:
        os.makedirs(self._dir, exist_ok=True)
        data = {
            "version": 2,
            "jobs": [job_to_dict(j) for j in self._jobs],
        }
        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        # 原子写入
        fd, tmp_path = tempfile.mkstemp(
            dir=self._dir, prefix=".heartbeat_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json_str)
            if os.name == "nt" and os.path.exists(self._store_path):
                os.replace(tmp_path, self._store_path)
            else:
                os.rename(tmp_path, self._store_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        self._render_markdown()

    # --- CRUD ---

    async def add_job(self, job: HeartbeatJob) -> HeartbeatJob:
        """添加任务。"""
        if not job.user_id:
            job.user_id = self.get_effective_user_id()
        self._jobs.append(job)
        
        if self._db:
            try:
                await asyncio.to_thread(self._db.add_heartbeat_job, job.user_id, job_to_dict(job))
                from service.feature_flags import get_flags
                if get_flags().heartbeat_mode != "multi_tenant":
                    self._render_markdown()
            except Exception as e:
                logger.warning("Failed to add heartbeat job to DB: %s, falling back to file save", e)
                await self.save()
        else:
            await self.save()
        logger.info("Heartbeat job added: %s (%s)", job.name, job.id)
        return job

    async def remove_job(self, job_id: str) -> bool:
        """删除任务。"""
        before = len(self._jobs)
        job_to_remove = self.get_job(job_id)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        removed = len(self._jobs) != before
        if removed:
            if self._db and job_to_remove:
                try:
                    user_id = job_to_remove.user_id or self.get_effective_user_id()
                    await asyncio.to_thread(self._db.remove_heartbeat_job, user_id, job_id)
                    from service.feature_flags import get_flags
                    if get_flags().heartbeat_mode != "multi_tenant":
                        self._render_markdown()
                except Exception as e:
                    logger.warning("Failed to remove heartbeat job from DB: %s, falling back to file save", e)
                    await self.save()
            else:
                await self.save()
            logger.info("Heartbeat job removed: %s", job_id)
        return removed

    async def update_job(self, job_id: str, patch: dict) -> HeartbeatJob | None:
        """更新任务字段。"""
        job = self.get_job(job_id)
        if not job:
            return None

        now = now_ms()
        if "name" in patch:
            job.name = patch["name"]
        if "description" in patch:
            job.description = patch["description"]
        if "enabled" in patch:
            job.enabled = patch["enabled"]
            if not job.enabled:
                job.state.next_run_at_ms = None
                job.state.running = False
        if "instruction" in patch:
            job.instruction = patch["instruction"]
        if "script_path" in patch:
            job.script_path = patch["script_path"]
        if "type" in patch:
            job.type = patch["type"]
        if "schedule" in patch:
            sched_data = patch["schedule"]
            if isinstance(sched_data, dict):
                job.schedule = ScheduleConfig(
                    frequency=sched_data.get("frequency", job.schedule.frequency),
                    time=sched_data.get("time", job.schedule.time),
                    weekdays=sched_data.get("weekdays", job.schedule.weekdays),
                    monthdays=sched_data.get("monthdays", job.schedule.monthdays),
                    once_at=sched_data.get("once_at", job.schedule.once_at),
                    timezone=sched_data.get("timezone", job.schedule.timezone),
                )

        job.updated_at_ms = now
        
        if self._db:
            try:
                user_id = job.user_id or self.get_effective_user_id()
                await asyncio.to_thread(self._db.update_heartbeat_job, user_id, job_id, patch)
                from service.feature_flags import get_flags
                if get_flags().heartbeat_mode != "multi_tenant":
                    self._render_markdown()
            except Exception as e:
                logger.warning("Failed to update heartbeat job in DB: %s, falling back to file save", e)
                await self.save()
        else:
            await self.save()
        logger.info("Heartbeat job updated: %s", job_id)
        return job

    def list_jobs(self, include_disabled: bool = False) -> list[HeartbeatJob]:
        """列出任务。"""
        if include_disabled:
            return list(self._jobs)
        return [j for j in self._jobs if j.enabled]

    def get_job(self, job_id: str) -> HeartbeatJob | None:
        """按 ID 查找任务。"""
        for j in self._jobs:
            if j.id == job_id:
                return j
        return None

    # --- Markdown Rendering ---

    def _render_markdown(self) -> None:
        """生成 heartbeat.md 可读文档。"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = [
            "# 🕐 定时任务 (Heartbeat)",
            "",
            f"> 自动生成，请勿手动编辑。最后更新: {now_str}",
            "",
        ]

        agent_jobs = [j for j in self._jobs if j.type == "agent"]
        script_jobs = [j for j in self._jobs if j.type == "script"]

        # Agent 任务
        lines.append("## 🤖 智能体任务")
        lines.append("")
        if agent_jobs:
            lines.append("| 状态 | 名称 | 调度规则 | 下次执行 | 描述 |")
            lines.append("|------|------|----------|----------|------|")
            for j in agent_jobs:
                status = self._status_icon(j)
                rule = j.schedule.human_readable()
                next_run = self._format_next_run(j)
                lines.append(f"| {status} | {j.name} | {rule} | {next_run} | {j.description} |")
        else:
            lines.append("_(暂无智能体任务)_")
        lines.append("")

        # 脚本任务
        lines.append("## 📜 脚本任务")
        lines.append("")
        if script_jobs:
            lines.append("| 状态 | 名称 | 调度规则 | 下次执行 | 脚本 |")
            lines.append("|------|------|----------|----------|------|")
            for j in script_jobs:
                status = self._status_icon(j)
                rule = j.schedule.human_readable()
                next_run = self._format_next_run(j)
                lines.append(f"| {status} | {j.name} | {rule} | {next_run} | {j.script_path} |")
        else:
            lines.append("_(暂无脚本任务)_")
        lines.append("")

        try:
            with open(self._md_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            logger.warning("Failed to render heartbeat.md: %s", e)

    @staticmethod
    def _status_icon(job: HeartbeatJob) -> str:
        if not job.enabled:
            return "⏸️ 已暂停"
        if job.state.running:
            return "🔄 执行中"
        if job.state.last_status == "ok" and job.state.next_run_at_ms is None:
            return "✅ 已完成"
        if job.state.last_status == "error":
            return "❌ 出错"
        return "⏳ 等待中"

    @staticmethod
    def _format_next_run(job: HeartbeatJob) -> str:
        if not job.state.next_run_at_ms:
            return "-"
        try:
            dt = datetime.fromtimestamp(job.state.next_run_at_ms / 1000)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return "-"

    # --- Run Log ---

    async def append_run_log(self, entry: RunLogEntry) -> None:
        """追加一条执行日志。"""
        if self._db:
            try:
                run_dict = asdict(entry)
                run_dict["started_at"] = entry.ts
                run_dict["ended_at"] = entry.ts + entry.duration_ms
                
                job = self.get_job(entry.job_id)
                user_id = job.user_id if job else self.get_effective_user_id()
                
                await asyncio.to_thread(self._db.add_heartbeat_run_log, user_id, run_dict)
            except Exception as e:
                logger.warning("Failed to append run log to DB: %s, falling back to sync file write", e)
                await asyncio.to_thread(self._append_run_log_sync, entry)
        else:
            await asyncio.to_thread(self._append_run_log_sync, entry)

    def _append_run_log_sync(self, entry: RunLogEntry) -> None:
        os.makedirs(self._runs_dir, exist_ok=True)
        log_path = os.path.join(self._runs_dir, f"{entry.job_id}.jsonl")
        line = json.dumps(asdict(entry), ensure_ascii=False)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # 保留最近 MAX_RUN_LOG_ENTRIES 条
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            if len(all_lines) > MAX_RUN_LOG_ENTRIES:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(all_lines[-MAX_RUN_LOG_ENTRIES:])
        except Exception:
            pass

    async def read_run_log(self, job_id: str, limit: int = 20) -> list[dict]:
        """读取某个任务的执行日志（最近 N 条）。"""
        if self._db:
            try:
                job = self.get_job(job_id)
                user_id = job.user_id if job else self.get_effective_user_id()
                
                rows = await asyncio.to_thread(self._db.read_heartbeat_run_logs, user_id, job_id, limit)
                results = []
                for r in rows:
                    artifacts = []
                    if r.get("artifacts_json"):
                        try:
                            artifacts = json.loads(r["artifacts_json"])
                        except Exception:
                            pass
                    results.append({
                        "ts": r.get("started_at") or 0.0,
                        "job_id": r.get("job_id"),
                        "status": r.get("status"),
                        "error": r.get("error"),
                        "duration_ms": r.get("duration_ms", 0.0),
                        "session_id": r.get("session_id"),
                        "result": r.get("result"),
                        "artifacts": artifacts,
                    })
                return results
            except Exception as e:
                logger.warning("Failed to read run logs from DB: %s, falling back to sync file read", e)
                return await asyncio.to_thread(self._read_run_log_sync, job_id, limit)
        else:
            return await asyncio.to_thread(self._read_run_log_sync, job_id, limit)

    def _read_run_log_sync(self, job_id: str, limit: int) -> list[dict]:
        log_path = os.path.join(self._runs_dir, f"{job_id}.jsonl")
        if not os.path.exists(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            entries = []
            for line in lines[-limit:]:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
            entries.reverse()  # 最新的在前
            return entries
        except Exception as e:
            logger.warning("Failed to read run log for %s: %s", job_id, e)
            return []
