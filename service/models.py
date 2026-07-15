"""
Pydantic Models for Service API
================================
Request and response schemas for the FastAPI application.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ---------------------------------------------------------------------------
# Auth / User
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    """Current user profile."""

    id: str
    oidc_sub: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    roles: list[str] = []
    created_at: str
    last_seen_at: str
    deploy_mode: str = "pc"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=200, description="会话标题（可选）")


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=200, description="新标题")


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: Optional[str] = None
    created_at: str
    updated_at: str
    is_active: bool


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int


# ---------------------------------------------------------------------------
# Chat (Streaming)
# ---------------------------------------------------------------------------


class ChatStreamRequest(BaseModel):
    """Request body for the streaming chat endpoint."""
    content: str = Field(..., min_length=1, max_length=32000, description="用户消息内容")
    attachment_ids: list[str] = Field(default=[], description="本次消息引用的附件ID列表")
    capability: Optional[str] = Field(
        default=None,
        description="当前启用的能力: 'flux' | 'acps' | null（无能力，纯个人助手）",
    )


class MessageItem(BaseModel):
    """A single message for display, extracted from checkpointer state."""
    role: str  # "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[dict] = []
    tool_call_id: Optional[str] = None
    name: Optional[str] = None  # tool name for tool messages


class MessagesResponse(BaseModel):
    """Response for retrieving session messages."""
    messages: list[MessageItem]


# ---------------------------------------------------------------------------
# Webhook / Heartbeat
# ---------------------------------------------------------------------------


class EventPayload(BaseModel):
    """Payload for external event webhook."""
    event_type: str = Field(..., description="Event type identifier (e.g., 'match_success', 'reminder')")
    details: str = Field("", description="Human-readable event details to inject into the conversation")


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class AttachmentResponse(BaseModel):
    """Metadata for a single uploaded attachment."""
    id: str
    original_name: str
    mime_type: str
    size_bytes: int
    uploaded_at: str


class AttachmentListResponse(BaseModel):
    """List of attachments for a session."""
    attachments: list[AttachmentResponse]
    total: int


# ---------------------------------------------------------------------------
# Heartbeat (Scheduled Tasks)
# ---------------------------------------------------------------------------


class HeartbeatJobCreateRequest(BaseModel):
    """创建定时任务的请求体。"""
    name: str = Field(..., max_length=100, description="任务名称")
    description: str = Field("", max_length=500, description="任务描述")
    schedule_type: str = Field(
        ..., description="调度类型: 'at' | 'every' | 'cron'"
    )
    schedule_value: str = Field(
        ..., description="调度值: ISO 时间 | 间隔秒数 | Cron 表达式"
    )
    message: str = Field(..., max_length=2000, description="任务触发时的 agent 指令")
    timezone: str = Field("Asia/Shanghai", description="时区")


class HeartbeatJobUpdateRequest(BaseModel):
    """更新定时任务的请求体。"""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    enabled: Optional[bool] = None
    schedule_type: Optional[str] = None
    schedule_value: Optional[str] = None
    message: Optional[str] = Field(None, max_length=2000)


class HeartbeatScheduleResponse(BaseModel):
    """调度配置响应。"""
    kind: str
    at: Optional[str] = None
    every_seconds: Optional[int] = None
    cron_expr: Optional[str] = None
    timezone: str = "Asia/Shanghai"


class HeartbeatJobStateResponse(BaseModel):
    """任务运行状态响应。"""
    next_run_at_ms: Optional[float] = None
    last_run_at_ms: Optional[float] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    running: bool = False


class HeartbeatJobResponse(BaseModel):
    """单个定时任务的响应体。"""
    id: str
    name: str
    description: str
    enabled: bool
    schedule: HeartbeatScheduleResponse
    payload_text: str
    created_at_ms: float
    updated_at_ms: float
    state: HeartbeatJobStateResponse


class HeartbeatJobListResponse(BaseModel):
    """定时任务列表响应。"""
    jobs: list[HeartbeatJobResponse]
    total: int


class HeartbeatRunLogEntry(BaseModel):
    """单条执行日志。"""
    ts: float
    job_id: str
    status: str
    error: Optional[str] = None
    duration_ms: float
    session_id: str


class HeartbeatRunLogResponse(BaseModel):
    """执行日志列表响应。"""
    entries: list[HeartbeatRunLogEntry]
    total: int


class HeartbeatStatusResponse(BaseModel):
    """调度器状态响应。"""
    enabled: bool
    total_jobs: int
    active_jobs: int
    next_run_at_ms: Optional[float] = None
