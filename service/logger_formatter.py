"""JSON structured logging formatter for SaaS mode.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JSONLogFormatter(logging.Formatter):
    """Formats log records as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        user_id = ""
        deploy_mode = ""
        
        # Try to extract context dynamically
        try:
            from service.context import get_current_user_ctx
            ctx = get_current_user_ctx()
            if ctx:
                user_id = ctx.user_id
                deploy_mode = ctx.deploy_mode
        except Exception:
            pass

        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "user_id": user_id,
            "deploy_mode": deploy_mode,
        }

        # Check for context values attached directly to the log record
        for field in ("session_id", "request_id", "thread_id"):
            if hasattr(record, field):
                log_data[field] = getattr(record, field)

        # Include exception traceback if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)
