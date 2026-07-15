"""Heartbeat 定时任务管理模块 - 任务执行器。

包含：
- AgentTaskExecutor: 创建精简版 agent 执行 agent 类型任务
- ScriptTaskExecutor: 通过 subprocess 执行 script 类型任务
- report_result 工具定义
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from .models import (
    HeartbeatJob,
    RunLogEntry,
    ExecutionResult,
    now_ms,
)
from .scheduler import compute_next_run_after_execution, error_backoff_ms

if TYPE_CHECKING:
    from .store import HeartbeatStore
    from .scheduler import HeartbeatScheduler

logger = logging.getLogger(__name__)

# 线程安全的定时任务执行上下文 (job_id, started_at)
current_session_var: ContextVar[tuple[str, int] | None] = ContextVar(
    "current_session", default=None
)


def _ms_to_folder_name(ms: float) -> str:
    """将毫秒时间戳转为人类可读的文件夹名，如 2026-06-04_18-51-30。"""
    try:
        dt = datetime.fromtimestamp(ms / 1000)
        return dt.strftime("%Y-%m-%d_%H-%M-%S")
    except (ValueError, OSError):
        return str(int(ms))


def _serialize_message(msg) -> dict:
    """将 LangChain 消息序列化为字典。"""
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

    if isinstance(msg, HumanMessage):
        return {
            "role": "user",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        }
    elif isinstance(msg, AIMessage):
        item = {
            "role": "assistant",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        }
        if msg.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}),
                }
                for tc in msg.tool_calls
            ]
        return item
    elif isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            "tool_call_id": msg.tool_call_id,
            "name": getattr(msg, "name", None),
        }
    elif isinstance(msg, SystemMessage):
        return {
            "role": "system",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        }
    else:
        content = getattr(msg, "content", str(msg))
        return {
            "role": getattr(msg, "type", "unknown"),
            "content": content if isinstance(content, str) else str(content),
        }


class TaskExecutor:
    """任务执行协调器。

    管理 agent 和 script 两种类型的任务执行，
    处理执行结果记录和状态更新。
    """

    def __init__(
        self,
        store: HeartbeatStore,
        scheduler: HeartbeatScheduler,
        workspace_root: str,
        agent_builder: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._workspace_root = workspace_root
        self._agent_builder = agent_builder
        self._run_outcomes: dict[str, dict] = {}  # session_id -> {"result", "artifacts"}
        self._user_task_counts: dict[str, int] = {}  # user_id -> concurrent running count
        self._user_task_lock = asyncio.Lock()

    def set_agent_builder(self, builder: Callable[..., Awaitable[Any]]) -> None:
        """设置 agent 构建回调。"""
        self._agent_builder = builder

    async def execute_job(self, job: HeartbeatJob) -> None:
        """执行到期任务（入口方法，由 scheduler 调用）。"""
        MAX_CONCURRENT_PER_USER = 3
        user_id = job.user_id or self._store.get_effective_user_id()

        # Per-user concurrency limit for heartbeat tasks
        async with self._user_task_lock:
            current = self._user_task_counts.get(user_id, 0)
            if current >= MAX_CONCURRENT_PER_USER:
                logger.warning(
                    "User %s reached max concurrent heartbeat limit (%d), skipping %s",
                    user_id, MAX_CONCURRENT_PER_USER, job.id,
                )
                job.state.last_status = "error"
                job.state.last_error = f"已达到用户最大并发任务数限制 ({MAX_CONCURRENT_PER_USER})"
                await self._store.save()
                return
            self._user_task_counts[user_id] = current + 1

        try:
            started_at = now_ms()
            session_id = f"heartbeat:{job.id}:{int(started_at)}"

            job.state.running = True
            job.state.last_run_at_ms = started_at
            logger.info(
                "Heartbeat executing job: %s (%s) type=%s",
                job.name, job.id, job.type,
            )

            result: ExecutionResult

            from service.feature_flags import get_flags
            if get_flags().heartbeat_mode == "disabled" and job.type == "script":
                logger.warning("Rejected script job execution in current deploy mode: %s (%s)", job.name, job.id)
                result = ExecutionResult(
                    status="error",
                    error="当前部署模式下禁用脚本类型定时任务",
                    result="执行失败：当前部署模式下禁用脚本类型定时任务",
                )
            elif job.type == "script":
                result = await self._execute_script(job, session_id)
            else:
                result = await self._execute_agent(job, session_id, started_at)

            ended_at = now_ms()
            result.duration_ms = ended_at - started_at

            # 更新任务状态
            self._update_job_state(job, result, ended_at)

            # 写入执行日志和产物
            await self._save_run_artifacts(job, session_id, started_at, ended_at, result)

            # 持久化
            await self._store.save()
            logger.info(
                "Heartbeat job %s finished: status=%s duration=%.1fs",
                job.id, result.status, result.duration_ms / 1000,
            )
        finally:
            async with self._user_task_lock:
                self._user_task_counts[user_id] = max(
                    0, self._user_task_counts.get(user_id, 1) - 1
                )

    async def _execute_agent(
        self, job: HeartbeatJob, session_id: str, started_at: float
    ) -> ExecutionResult:
        """执行 agent 类型任务。"""
        if not self._agent_builder:
            return ExecutionResult(
                status="error",
                error="No agent builder configured",
            )

        token = current_session_var.set((job.id, int(started_at)))
        try:
            # 构建精简版 agent 并执行
            callback_res = await self._agent_builder(
                session_id=session_id,
                job=job,
                report_tool=self._create_report_tool(session_id),
            )

            # 提取执行结果
            outcome = self._run_outcomes.pop(session_id, None)
            result_text = None
            artifacts = []

            if outcome:
                result_text = outcome.get("result")
                artifacts = outcome.get("artifacts", [])

            # 兜底：从 agent 最后一条 AI 消息提取
            if not result_text and callback_res and isinstance(callback_res, dict):
                messages = callback_res.get("messages", [])
                for msg in reversed(messages):
                    msg_type = getattr(msg, "type", None) or (
                        msg.get("type") if isinstance(msg, dict) else None
                    )
                    msg_content = getattr(msg, "content", None) or (
                        msg.get("content") if isinstance(msg, dict) else None
                    )
                    if msg_type == "ai" and msg_content:
                        result_text = str(msg_content)[:500]
                        break

            return ExecutionResult(
                status="ok",
                result=result_text,
                artifacts=artifacts,
                messages=callback_res.get("messages", []) if isinstance(callback_res, dict) else [],
            )

        except Exception as e:
            logger.exception("Heartbeat agent job %s failed: %s", job.id, e)
            return ExecutionResult(
                status="error",
                error=str(e)[:500],
                result=f"执行出错: {e}",
            )
        finally:
            current_session_var.reset(token)

    async def _execute_script(
        self, job: HeartbeatJob, session_id: str
    ) -> ExecutionResult:
        """执行 script 类型任务。"""
        script_path = job.script_path
        if not script_path:
            return ExecutionResult(
                status="error",
                error="No script path configured",
                result="未配置脚本路径",
            )

        # 解析脚本路径（相对于 heartbeat 目录）
        if not os.path.isabs(script_path):
            full_path = os.path.join(self._store.heartbeat_dir, script_path)
        else:
            full_path = script_path

        if not os.path.exists(full_path):
            return ExecutionResult(
                status="error",
                error=f"Script not found: {full_path}",
                result=f"脚本文件不存在: {script_path}",
            )

        # 判断脚本类型
        if full_path.endswith(".py"):
            cmd = ["python", full_path]
        elif full_path.endswith(".sh"):
            cmd = ["bash", full_path]
        else:
            cmd = ["bash", full_path]

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟超时
                cwd=self._workspace_root,
            )

            stdout = proc.stdout.strip() if proc.stdout else ""
            stderr = proc.stderr.strip() if proc.stderr else ""

            if proc.returncode == 0:
                result_text = stdout or "脚本执行成功（无输出）"
                return ExecutionResult(
                    status="ok",
                    result=result_text[:1000],
                )
            else:
                error_text = stderr or stdout or f"Exit code: {proc.returncode}"
                return ExecutionResult(
                    status="error",
                    error=error_text[:500],
                    result=f"脚本执行失败: {error_text[:500]}",
                )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                status="error",
                error="Script execution timed out (300s)",
                result="脚本执行超时（300秒）",
            )
        except Exception as e:
            return ExecutionResult(
                status="error",
                error=str(e)[:500],
                result=f"脚本执行异常: {e}",
            )

    def _update_job_state(
        self, job: HeartbeatJob, result: ExecutionResult, ended_at: float
    ) -> None:
        """更新任务状态。"""
        job.state.running = False
        job.state.last_status = result.status
        job.state.last_error = result.error
        job.state.last_result = result.result
        job.updated_at_ms = ended_at

        if result.status == "ok":
            job.state.consecutive_errors = 0
            next_run = compute_next_run_after_execution(job, ended_at)
            job.state.next_run_at_ms = next_run
            if next_run is None:
                job.enabled = False
        else:
            job.state.consecutive_errors += 1
            backoff = error_backoff_ms(job.state.consecutive_errors)
            normal_next = compute_next_run_after_execution(job, ended_at)
            backoff_next = ended_at + backoff
            if normal_next is not None:
                job.state.next_run_at_ms = max(normal_next, backoff_next)
            else:
                if job.state.consecutive_errors <= 3:
                    job.state.next_run_at_ms = backoff_next
                else:
                    job.enabled = False
                    job.state.next_run_at_ms = None

    async def _save_run_artifacts(
        self,
        job: HeartbeatJob,
        session_id: str,
        started_at: float,
        ended_at: float,
        result: ExecutionResult,
    ) -> None:
        """保存执行日志和产物。"""
        from service.feature_flags import get_flags

        # 序列化消息
        serialized_msgs = []
        for msg in result.messages:
            try:
                serialized_msgs.append(_serialize_message(msg))
            except Exception as e:
                logger.warning("Failed to serialize message: %s", e)

        if get_flags().storage_engine != "postgresql":
            archive_dir = os.path.join(
                self._store.heartbeat_dir, "artifacts", job.id, _ms_to_folder_name(started_at)
            )
            os.makedirs(archive_dir, exist_ok=True)

            # 写入 run_log.json
            run_log_path = os.path.join(archive_dir, "run_log.json")
            try:
                with open(run_log_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "job_id": job.id,
                        "session_id": session_id,
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "duration_ms": result.duration_ms,
                        "status": result.status,
                        "error": result.error,
                        "result": result.result,
                        "messages": serialized_msgs,
                    }, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.exception("Failed to write run_log.json: %s", e)

        # 追加到 JSONL 日志
        log_entry = RunLogEntry(
            ts=started_at,
            job_id=job.id,
            status=result.status,
            error=result.error,
            duration_ms=result.duration_ms,
            session_id=session_id,
            result=result.result,
            artifacts=result.artifacts,
        )
        await self._store.append_run_log(log_entry)

    def _create_report_tool(self, session_id: str):
        """创建 report_result 工具（注入到 agent 任务中）。"""
        from pydantic import BaseModel, Field
        from langchain_core.tools import StructuredTool

        executor = self

        class ReportResultSchema(BaseModel):
            """提交定时任务的执行成果。"""
            summary: str = Field(description="本次任务执行的成果摘要和最终结论。")
            artifacts: list[str] = Field(
                default_factory=list,
                description="本次运行产生的最终产物文件路径列表（相对于工作区的相对路径）。",
            )

        def sync_fn(**kwargs) -> str:
            raise RuntimeError("Use async version")

        async def async_fn(summary: str, artifacts: list[str] = []) -> str:
            archived_files = []
            for item in artifacts:
                rel_path = os.path.normpath(item)
                if rel_path.startswith("..") or os.path.isabs(rel_path):
                    continue
                src_path = os.path.join(executor._workspace_root, rel_path)
                if not os.path.isfile(src_path):
                    continue
                # 将在 _save_run_artifacts 中归档
                archived_files.append(os.path.basename(rel_path))

            executor._run_outcomes[session_id] = {
                "result": summary,
                "artifacts": archived_files,
            }

            return (
                f"✅ 成果汇报成功\n"
                f"- 成果摘要: {summary}\n"
                f"- 已记录文件: {', '.join(archived_files) if archived_files else '无'}"
            )

        return StructuredTool.from_function(
            name="report_result",
            description=(
                "汇报本次定时任务的执行结果和生成的文件产物。"
                "在任务结束前，必须且仅限调用一次此工具。"
            ),
            func=sync_fn,
            coroutine=async_fn,
            infer_schema=False,
            args_schema=ReportResultSchema,
        )

    def build_scheduled_prompt(self, job: HeartbeatJob) -> str:
        """为定时任务构建带 <scheduled_task> 标签的 prompt。"""
        # 获取上次执行信息
        last_status = job.state.last_status or "无"
        last_log_path = "无"
        from service.feature_flags import get_flags
        if get_flags().storage_engine == "postgresql":
            last_log_path = "数据库 (PostgreSQL)"
        elif job.state.last_run_at_ms:
            last_log_path = os.path.join(
                self._store.heartbeat_dir,
                "artifacts",
                job.id,
                _ms_to_folder_name(job.state.last_run_at_ms),
                "run_log.json",
            )

        scheduled_tag = (
            "<scheduled_task>\n"
            "  <context>这是系统自动发起的定时任务调用，不是用户在对话中发送的消息。</context>\n"
            "  <task_info>\n"
            f"    <name>{job.name}</name>\n"
            f"    <job_id>{job.id}</job_id>\n"
            "  </task_info>\n"
            "  <previous_run>\n"
            f"    <status>{last_status}</status>\n"
            f"    <log_path>{last_log_path}</log_path>\n"
            "  </previous_run>\n"
            "  <output_instructions>\n"
            "    完成任务后，你必须调用 report_result 工具汇报执行结果。\n"
            "    不要创建新的定时任务或修改现有任务。\n"
            "  </output_instructions>\n"
            "</scheduled_task>\n\n"
        )
        return scheduled_tag + job.instruction
