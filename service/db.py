"""
Database Module
================
Persistence for user mapping, sessions, and LLM providers. Can route to SQLite or PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
import sqlite3
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Lazy imports for PostgreSQL to ensure PC-mode compatibility
try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class User:
    """Local user record mapped from an OIDC identity."""
    id: str
    oidc_sub: str
    oidc_issuer: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    roles: str = "[]"  # JSON-encoded list
    created_at: str = ""
    last_seen_at: str = ""

    @property
    def roles_list(self) -> list[str]:
        try:
            return json.loads(self.roles)
        except (json.JSONDecodeError, TypeError):
            return []

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roles"] = self.roles_list
        return d


@dataclass
class Session:
    """A chat session (conversation) belonging to a user."""
    id: str
    user_id: str
    title: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    is_active: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Database Base Interface
# ---------------------------------------------------------------------------

class BaseDatabase(ABC):
    """Database interface abstraction for user and session management."""

    @abstractmethod
    def get_or_create_user(
        self,
        oidc_sub: str,
        oidc_issuer: str,
        display_name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> User:
        """Find an existing user or create a new one."""
        pass

    @abstractmethod
    def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        pass

    @abstractmethod
    def list_users(self) -> list[User]:
        """List all users."""
        pass

    @abstractmethod
    def create_session(self, user_id: str, title: str | None = None) -> Session:
        """Create a new chat session."""
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session by ID."""
        pass

    @abstractmethod
    def list_user_sessions(
        self, user_id: str, active_only: bool = True, limit: int = 50
    ) -> list[Session]:
        """List sessions for a user, most recent first."""
        pass

    @abstractmethod
    def update_session(self, session_id: str, title: str | None = None) -> Optional[Session]:
        """Update session metadata (e.g. title)."""
        pass

    @abstractmethod
    def touch_session(self, session_id: str) -> None:
        """Update the session's updated_at timestamp."""
        pass

    @abstractmethod
    def deactivate_session(self, session_id: str) -> None:
        """Soft-delete a session by setting is_active = 0/false."""
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Hard-delete a session and its checkpoints."""
        pass

    @abstractmethod
    def list_providers(self, user_id: str) -> tuple[list[dict], int]:
        """List all LLM providers for the user and the active index."""
        pass

    @abstractmethod
    def add_provider(self, user_id: str, provider: dict) -> int:
        """Add an LLM provider and return its index."""
        pass

    @abstractmethod
    def update_provider(self, user_id: str, index: int, patch: dict) -> dict:
        """Update the LLM provider at the given index."""
        pass

    @abstractmethod
    def remove_provider(self, user_id: str, index: int) -> bool:
        """Remove the LLM provider at the given index."""
        pass

    @abstractmethod
    def set_active_provider(self, user_id: str, index: int) -> None:
        """Set the active LLM provider index."""
        pass

    @abstractmethod
    def write_audit_log(self, user_id: str, action: str, details: str) -> None:
        """Write a new audit log record."""
        pass

    @abstractmethod
    def record_token_usage(
        self, user_id: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        """Record and accumulate token usage for a user and model."""
        pass

    @abstractmethod
    def get_user_entity(self, user_id: str) -> Optional[dict]:
        """Get user entity credentials."""
        pass

    @abstractmethod
    def save_user_entity(self, user_id: str, entity_id: str, credentials: str) -> None:
        """Save/update user entity credentials."""
        pass

    @abstractmethod
    def add_heartbeat_job(self, user_id: str, job_dict: dict) -> dict:
        """Add a heartbeat job for the user."""
        pass

    @abstractmethod
    def remove_heartbeat_job(self, user_id: str, job_id: str) -> bool:
        """Remove a heartbeat job for the user."""
        pass

    @abstractmethod
    def update_heartbeat_job(self, user_id: str, job_id: str, patch: dict) -> dict:
        """Update a heartbeat job for the user."""
        pass

    @abstractmethod
    def get_heartbeat_job(self, user_id: str, job_id: str) -> dict | None:
        """Get a specific heartbeat job for the user."""
        pass

    @abstractmethod
    def list_heartbeat_jobs(self, user_id: str, include_disabled: bool = False) -> list[dict]:
        """List heartbeat jobs for the user."""
        pass

    @abstractmethod
    def list_all_heartbeat_jobs(self) -> list[dict]:
        """List all heartbeat jobs across all users."""
        pass

    @abstractmethod
    def save_heartbeat_job_state(self, user_id: str, job_id: str, state_dict: dict) -> None:
        """Update/save job execution state in database."""
        pass

    @abstractmethod
    def add_heartbeat_run_log(self, user_id: str, run_dict: dict) -> None:
        """Log a heartbeat job execution run."""
        pass

    @abstractmethod
    def read_heartbeat_run_logs(self, user_id: str, job_id: str, limit: int = 20) -> list[dict]:
        """Read recent execution logs for a heartbeat job."""
        pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# SQLite Database Implementation (PC Mode)
# ---------------------------------------------------------------------------

class SQLiteDatabase(BaseDatabase):
    """SQLite implementation of BaseDatabase for local/PC mode."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info("SQLiteDatabase initialized at %s", self.db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id           TEXT PRIMARY KEY,
                    oidc_sub     TEXT NOT NULL,
                    oidc_issuer  TEXT NOT NULL,
                    display_name TEXT,
                    email        TEXT,
                    roles        TEXT DEFAULT '[]',
                    created_at   TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(oidc_sub, oidc_issuer)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id         TEXT PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    title      TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active  INTEGER DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id           TEXT PRIMARY KEY,
                    user_id      TEXT NOT NULL,
                    action       TEXT NOT NULL,
                    details      TEXT NOT NULL,
                    timestamp    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_token_usages (
                    id            TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL,
                    model         TEXT NOT NULL,
                    input_tokens  INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    updated_at    TEXT NOT NULL,
                    UNIQUE(user_id, model)
                );

                CREATE TABLE IF NOT EXISTS user_entities (
                    user_id      TEXT PRIMARY KEY,
                    entity_id    TEXT NOT NULL,
                    credentials  TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS heartbeat_jobs (
                    id             TEXT PRIMARY KEY,
                    user_id        TEXT NOT NULL,
                    name           TEXT NOT NULL,
                    description    TEXT,
                    enabled        INTEGER DEFAULT 1,
                    type           TEXT NOT NULL,
                    instruction    TEXT,
                    script_path    TEXT,
                    schedule_json  TEXT NOT NULL,
                    created_at_ms  BIGINT NOT NULL,
                    updated_at_ms  BIGINT NOT NULL,
                    state_json     TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS heartbeat_runs (
                    id             TEXT PRIMARY KEY,
                    job_id         TEXT NOT NULL,
                    user_id        TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    error          TEXT,
                    result         TEXT,
                    started_at     BIGINT NOT NULL,
                    ended_at       BIGINT NOT NULL,
                    duration_ms    BIGINT NOT NULL,
                    session_id     TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_oidc
                    ON users(oidc_sub, oidc_issuer);
                CREATE INDEX IF NOT EXISTS idx_sessions_user
                    ON sessions(user_id, is_active);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_user
                    ON audit_logs(user_id);
                CREATE INDEX IF NOT EXISTS idx_heartbeat_jobs_user ON heartbeat_jobs(user_id);
                CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_job ON heartbeat_runs(job_id);
                CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_user ON heartbeat_runs(user_id);
                """
            )

    def get_or_create_user(
        self,
        oidc_sub: str,
        oidc_issuer: str,
        display_name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> User:
        now = self._now()
        roles_json = json.dumps(roles or [])

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE oidc_sub = ? AND oidc_issuer = ?",
                (oidc_sub, oidc_issuer),
            ).fetchone()

            if row:
                conn.execute(
                    """UPDATE users
                       SET last_seen_at = ?,
                           display_name = COALESCE(?, display_name),
                           email        = COALESCE(?, email),
                           roles        = ?
                     WHERE id = ?""",
                    (now, display_name, email, roles_json, row["id"]),
                )
                return User(
                    id=row["id"],
                    oidc_sub=oidc_sub,
                    oidc_issuer=oidc_issuer,
                    display_name=display_name or row["display_name"],
                    email=email or row["email"],
                    roles=roles_json,
                    created_at=row["created_at"],
                    last_seen_at=now,
                )
            else:
                user_id = self._new_id()
                conn.execute(
                    """INSERT INTO users
                       (id, oidc_sub, oidc_issuer, display_name, email, roles, created_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, oidc_sub, oidc_issuer, display_name, email, roles_json, now, now),
                )
                logger.info("Created new user: %s (sub=%s)", display_name or user_id, oidc_sub)
                return User(
                    id=user_id,
                    oidc_sub=oidc_sub,
                    oidc_issuer=oidc_issuer,
                    display_name=display_name,
                    email=email,
                    roles=roles_json,
                    created_at=now,
                    last_seen_at=now,
                )

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return User(**dict(row)) if row else None

    def list_users(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY last_seen_at DESC").fetchall()
            return [User(**dict(r)) for r in rows]

    def create_session(self, user_id: str, title: str | None = None) -> Session:
        now = self._now()
        session_id = self._new_id()

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sessions (id, user_id, title, created_at, updated_at, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (session_id, user_id, title, now, now),
            )
        return Session(
            id=session_id,
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
            is_active=True,
        )

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                return None
            return Session(**{**dict(row), "is_active": bool(row["is_active"])})

    def list_user_sessions(
        self, user_id: str, active_only: bool = True, limit: int = 50
    ) -> list[Session]:
        with self._conn() as conn:
            query = "SELECT * FROM sessions WHERE user_id = ?"
            params: list = [user_id]
            if active_only:
                query += " AND is_active = 1"
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [Session(**{**dict(r), "is_active": bool(r["is_active"])}) for r in rows]

    def update_session(self, session_id: str, title: str | None = None) -> Optional[Session]:
        now = self._now()
        with self._conn() as conn:
            sets = ["updated_at = ?"]
            params: list = [now]
            if title is not None:
                sets.append("title = ?")
                params.append(title)
            params.append(session_id)
            conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_session(session_id)

    def touch_session(self, session_id: str):
        now = self._now()
        with self._conn() as conn:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def deactivate_session(self, session_id: str):
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET is_active = 0, updated_at = ? WHERE id = ?",
                (now, session_id),
            )

    def delete_session(self, session_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))
                except sqlite3.OperationalError as e:
                    if "no such table" not in str(e).lower():
                        raise

    def list_providers(self, user_id: str) -> tuple[list[dict], int]:
        import tomllib
        from cli.config import get_account_config_path
        config_path = get_account_config_path(user_id)
        cfg = {}
        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    cfg = tomllib.load(f)
            except Exception:
                pass
        providers = cfg.get("providers", [])
        if not isinstance(providers, list):
            providers = []
        active_idx = cfg.get("active_provider", 0)
        return providers, active_idx

    def add_provider(self, user_id: str, provider: dict) -> int:
        from cli.config import load_config, save_config
        cfg = load_config(user_id)
        if "providers" not in cfg or not isinstance(cfg["providers"], list):
            cfg["providers"] = []
        entry = {
            "type": provider["type"].strip().lower(),
            "name": provider["name"].strip(),
            "base_url": provider.get("base_url", "").strip(),
            "api_key": provider.get("api_key", "").strip(),
        }
        cfg["providers"].append(entry)
        if len(cfg["providers"]) == 1:
            cfg["active_provider"] = 0
        save_config(cfg, user_id)
        return len(cfg["providers"]) - 1

    def update_provider(self, user_id: str, index: int, patch: dict) -> dict:
        from cli.config import load_config, save_config
        cfg = load_config(user_id)
        providers = cfg.get("providers", [])
        if not isinstance(providers, list) or index < 0 or index >= len(providers):
            raise ValueError(f"Invalid provider index: {index}")
        for key in ("type", "name", "base_url", "api_key"):
            if key in patch:
                providers[index][key] = str(patch[key]).strip()
        cfg["providers"] = providers
        save_config(cfg, user_id)
        return providers[index]

    def remove_provider(self, user_id: str, index: int) -> bool:
        from cli.config import load_config, save_config
        cfg = load_config(user_id)
        providers = cfg.get("providers", [])
        if not isinstance(providers, list) or index < 0 or index >= len(providers):
            return False
        providers.pop(index)
        cfg["providers"] = providers
        active = cfg.get("active_provider", 0)
        if isinstance(active, (int, float)):
            active = int(active)
            if active == index:
                cfg["active_provider"] = 0
            elif active > index:
                cfg["active_provider"] = active - 1
        save_config(cfg, user_id)
        return True

    def set_active_provider(self, user_id: str, index: int) -> None:
        from cli.config import load_config, save_config
        cfg = load_config(user_id)
        providers = cfg.get("providers", [])
        if not isinstance(providers, list) or index < 0 or index >= len(providers):
            raise ValueError(f"Invalid provider index: {index}")
        cfg["active_provider"] = index
        save_config(cfg, user_id)

    def write_audit_log(self, user_id: str, action: str, details: str) -> None:
        now = self._now()
        log_id = self._new_id()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_logs (id, user_id, action, details, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (log_id, user_id, action, details, now),
            )

    def record_token_usage(
        self, user_id: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        now = self._now()
        row_id = self._new_id()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_token_usages (id, user_id, model, input_tokens, output_tokens, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, model) DO UPDATE SET
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens,
                     updated_at = excluded.updated_at""",
                (row_id, user_id, model, input_tokens, output_tokens, now),
            )

    def get_user_entity(self, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM user_entities WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def save_user_entity(self, user_id: str, entity_id: str, credentials: str) -> None:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_entities (user_id, entity_id, credentials, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     entity_id = excluded.entity_id,
                     credentials = excluded.credentials,
                     updated_at = excluded.updated_at""",
                (user_id, entity_id, credentials, now),
            )

    def add_heartbeat_job(self, user_id: str, job_dict: dict) -> dict:
        import time
        now = int(time.time() * 1000)
        job_id = job_dict.get("id") or self._new_id()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO heartbeat_jobs (id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    user_id,
                    job_dict["name"],
                    job_dict.get("description", ""),
                    1 if job_dict.get("enabled", True) else 0,
                    job_dict["type"],
                    job_dict.get("instruction", ""),
                    job_dict.get("script_path", ""),
                    json.dumps(job_dict.get("schedule", {})),
                    int(job_dict.get("created_at_ms") or now),
                    int(job_dict.get("updated_at_ms") or now),
                    json.dumps(job_dict.get("state", {})),
                ),
            )
        job_dict["id"] = job_id
        job_dict["created_at_ms"] = job_dict.get("created_at_ms") or now
        job_dict["updated_at_ms"] = job_dict.get("updated_at_ms") or now
        return job_dict

    def remove_heartbeat_job(self, user_id: str, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM heartbeat_jobs WHERE user_id = ? AND id = ?",
                (user_id, job_id),
            )
            conn.execute(
                "DELETE FROM heartbeat_runs WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
            return cur.rowcount > 0

    def update_heartbeat_job(self, user_id: str, job_id: str, patch: dict) -> dict:
        import time
        now = int(time.time() * 1000)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM heartbeat_jobs WHERE user_id = ? AND id = ?",
                (user_id, job_id),
            ).fetchone()
            if not row:
                raise ValueError(f"Job {job_id} not found")
            
            job = dict(row)
            
            name = patch.get("name", job["name"])
            description = patch.get("description", job["description"])
            
            if "enabled" in patch:
                enabled = 1 if patch["enabled"] else 0
            else:
                enabled = job["enabled"]
                
            instruction = patch.get("instruction", job["instruction"])
            script_path = patch.get("script_path", job["script_path"])
            
            if "schedule" in patch:
                schedule_json = json.dumps(patch["schedule"])
            else:
                schedule_json = job["schedule_json"]
                
            if "state" in patch:
                state_json = json.dumps(patch["state"])
            else:
                state_json = job["state_json"]
                
            conn.execute(
                """UPDATE heartbeat_jobs
                   SET name = ?, description = ?, enabled = ?, instruction = ?, script_path = ?, schedule_json = ?, state_json = ?, updated_at_ms = ?
                   WHERE user_id = ? AND id = ?""",
                (
                    name,
                    description,
                    enabled,
                    instruction,
                    script_path,
                    schedule_json,
                    state_json,
                    now,
                    user_id,
                    job_id,
                ),
            )
            
            updated_row = conn.execute(
                "SELECT * FROM heartbeat_jobs WHERE user_id = ? AND id = ?",
                (user_id, job_id),
            ).fetchone()
            return dict(updated_row) if updated_row else {}

    def get_heartbeat_job(self, user_id: str, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM heartbeat_jobs WHERE user_id = ? AND id = ?",
                (user_id, job_id),
            ).fetchone()
            return dict(row) if row else None

    def list_heartbeat_jobs(self, user_id: str, include_disabled: bool = False) -> list[dict]:
        with self._conn() as conn:
            if include_disabled:
                rows = conn.execute(
                    "SELECT * FROM heartbeat_jobs WHERE user_id = ? ORDER BY created_at_ms DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM heartbeat_jobs WHERE user_id = ? AND enabled = 1 ORDER BY created_at_ms DESC",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def list_all_heartbeat_jobs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM heartbeat_jobs ORDER BY created_at_ms DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def save_heartbeat_job_state(self, user_id: str, job_id: str, state_dict: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE heartbeat_jobs SET state_json = ? WHERE user_id = ? AND id = ?",
                (json.dumps(state_dict), user_id, job_id),
            )

    def add_heartbeat_run_log(self, user_id: str, run_dict: dict) -> None:
        import time
        run_id = run_dict.get("id") or self._new_id()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO heartbeat_runs (id, job_id, user_id, status, error, result, started_at, ended_at, duration_ms, session_id, artifacts_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    run_dict["job_id"],
                    user_id,
                    run_dict["status"],
                    run_dict.get("error", ""),
                    run_dict.get("result", ""),
                    int(run_dict["started_at"] or time.time() * 1000),
                    int(run_dict["ended_at"] or time.time() * 1000),
                    int(run_dict.get("duration_ms", 0)),
                    run_dict.get("session_id", ""),
                    json.dumps(run_dict.get("artifacts", [])),
                ),
            )

    def read_heartbeat_run_logs(self, user_id: str, job_id: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM heartbeat_runs
                   WHERE user_id = ? AND job_id = ?
                   ORDER BY started_at DESC
                   LIMIT ?""",
                (user_id, job_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# PostgreSQL Database Implementation (SaaS Mode)
# ---------------------------------------------------------------------------

class PostgreSQLDatabase(BaseDatabase):
    """PostgreSQL implementation of BaseDatabase for SaaS mode."""

    def __init__(self, dsn: str):
        if not PSYCOPG2_AVAILABLE:
            raise ImportError(
                "PostgreSQL client 'psycopg2' is not installed. "
                "Please run `pip install psycopg2-binary` to enable PostgreSQL mode."
            )
        self.dsn = dsn
        self.pool = ThreadedConnectionPool(minconn=2, maxconn=20, dsn=self.dsn)
        self._init_schema()
        logger.info("PostgreSQLDatabase initialized with connection pool.")

    @contextmanager
    def _conn(self):
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def _init_schema(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id           TEXT PRIMARY KEY,
                        oidc_sub     TEXT NOT NULL,
                        oidc_issuer  TEXT NOT NULL,
                        display_name TEXT,
                        email        TEXT,
                        roles        TEXT DEFAULT '[]',
                        created_at   TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        UNIQUE(oidc_sub, oidc_issuer)
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        id         TEXT PRIMARY KEY,
                        user_id    TEXT NOT NULL,
                        title      TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        is_active  BOOLEAN DEFAULT TRUE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS user_providers (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        name TEXT NOT NULL,
                        base_url TEXT,
                        api_key TEXT,
                        is_active BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id           TEXT PRIMARY KEY,
                        user_id      TEXT NOT NULL,
                        action       TEXT NOT NULL,
                        details      TEXT NOT NULL,
                        timestamp    TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_token_usages (
                        id            TEXT PRIMARY KEY,
                        user_id       TEXT NOT NULL,
                        model         TEXT NOT NULL,
                        input_tokens  INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        updated_at    TEXT NOT NULL,
                        UNIQUE(user_id, model)
                    );

                    CREATE TABLE IF NOT EXISTS user_entities (
                        user_id      TEXT PRIMARY KEY,
                        entity_id    TEXT NOT NULL,
                        credentials  TEXT NOT NULL,
                        updated_at   TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS heartbeat_jobs (
                        id             TEXT PRIMARY KEY,
                        user_id        TEXT NOT NULL,
                        name           TEXT NOT NULL,
                        description    TEXT,
                        enabled        INTEGER DEFAULT 1,
                        type           TEXT NOT NULL,
                        instruction    TEXT,
                        script_path    TEXT,
                        schedule_json  TEXT NOT NULL,
                        created_at_ms  BIGINT NOT NULL,
                        updated_at_ms  BIGINT NOT NULL,
                        state_json     TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS heartbeat_runs (
                        id             TEXT PRIMARY KEY,
                        job_id         TEXT NOT NULL,
                        user_id        TEXT NOT NULL,
                        status         TEXT NOT NULL,
                        error          TEXT,
                        result         TEXT,
                        started_at     BIGINT NOT NULL,
                        ended_at       BIGINT NOT NULL,
                        duration_ms    BIGINT NOT NULL,
                        session_id     TEXT NOT NULL,
                        artifacts_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_users_oidc
                        ON users(oidc_sub, oidc_issuer);
                    CREATE INDEX IF NOT EXISTS idx_sessions_user
                        ON sessions(user_id, is_active);
                    CREATE INDEX IF NOT EXISTS idx_user_providers
                        ON user_providers(user_id);
                    CREATE INDEX IF NOT EXISTS idx_audit_logs_user
                        ON audit_logs(user_id);
                    CREATE INDEX IF NOT EXISTS idx_heartbeat_jobs_user ON heartbeat_jobs(user_id);
                    CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_job ON heartbeat_runs(job_id);
                    CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_user ON heartbeat_runs(user_id);
                    """
                )

    def get_or_create_user(
        self,
        oidc_sub: str,
        oidc_issuer: str,
        display_name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> User:
        now = self._now()
        roles_json = json.dumps(roles or [])

        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM users WHERE oidc_sub = %s AND oidc_issuer = %s",
                    (oidc_sub, oidc_issuer),
                )
                row = cur.fetchone()

                if row:
                    cur.execute(
                        """UPDATE users
                           SET last_seen_at = %s,
                               display_name = COALESCE(%s, display_name),
                               email        = COALESCE(%s, email),
                               roles        = %s
                         WHERE id = %s""",
                        (now, display_name, email, roles_json, row["id"]),
                    )
                    return User(
                        id=row["id"],
                        oidc_sub=oidc_sub,
                        oidc_issuer=oidc_issuer,
                        display_name=display_name or row["display_name"],
                        email=email or row["email"],
                        roles=roles_json,
                        created_at=row["created_at"],
                        last_seen_at=now,
                    )
                else:
                    user_id = self._new_id()
                    cur.execute(
                        """INSERT INTO users
                           (id, oidc_sub, oidc_issuer, display_name, email, roles, created_at, last_seen_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (user_id, oidc_sub, oidc_issuer, display_name, email, roles_json, now, now),
                    )
                    logger.info("Created new SaaS user: %s (sub=%s)", display_name or user_id, oidc_sub)
                    return User(
                        id=user_id,
                        oidc_sub=oidc_sub,
                        oidc_issuer=oidc_issuer,
                        display_name=display_name,
                        email=email,
                        roles=roles_json,
                        created_at=now,
                        last_seen_at=now,
                    )

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                return User(**row) if row else None

    def list_users(self) -> list[User]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users ORDER BY last_seen_at DESC")
                rows = cur.fetchall()
                return [User(**dict(r)) for r in rows]

    def create_session(self, user_id: str, title: str | None = None) -> Session:
        now = self._now()
        session_id = self._new_id()

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sessions (id, user_id, title, created_at, updated_at, is_active)
                       VALUES (%s, %s, %s, %s, %s, TRUE)""",
                    (session_id, user_id, title, now, now),
                )
        return Session(
            id=session_id,
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
            is_active=True,
        )

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return Session(**{**dict(row), "is_active": bool(row["is_active"])})

    def list_user_sessions(
        self, user_id: str, active_only: bool = True, limit: int = 50
    ) -> list[Session]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM sessions WHERE user_id = %s"
                params: list = [user_id]
                if active_only:
                    query += " AND is_active = TRUE"
                query += " ORDER BY updated_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(query, params)
                rows = cur.fetchall()
                return [Session(**{**dict(r), "is_active": bool(r["is_active"])}) for r in rows]

    def update_session(self, session_id: str, title: str | None = None) -> Optional[Session]:
        now = self._now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                sets = ["updated_at = %s"]
                params: list = [now]
                if title is not None:
                    sets.append("title = %s")
                    params.append(title)
                params.append(session_id)
                cur.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = %s", params)
        return self.get_session(session_id)

    def touch_session(self, session_id: str) -> None:
        now = self._now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE sessions SET updated_at = %s WHERE id = %s", (now, session_id))

    def deactivate_session(self, session_id: str) -> None:
        now = self._now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET is_active = FALSE, updated_at = %s WHERE id = %s",
                    (now, session_id),
                )

    def delete_session(self, session_id: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
                for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                    try:
                        cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (session_id,))
                    except Exception as e:
                        logger.debug("Failed to clean PG checkpointer table %s: %s", table, e)

    def list_providers(self, user_id: str) -> tuple[list[dict], int]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM user_providers WHERE user_id = %s ORDER BY id ASC",
                    (user_id,)
                )
                rows = cur.fetchall()
                providers = []
                active_idx = 0
                for i, r in enumerate(rows):
                    providers.append({
                        "type": r["type"],
                        "name": r["name"],
                        "base_url": r["base_url"] or "",
                        "api_key": r["api_key"] or "",
                    })
                    if r["is_active"]:
                        active_idx = i
                return providers, active_idx

    def add_provider(self, user_id: str, provider: dict) -> int:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if this is the first provider to make it active automatically
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM user_providers WHERE user_id = %s",
                    (user_id,)
                )
                count = cur.fetchone()["cnt"]
                is_active = (count == 0)

                cur.execute(
                    """INSERT INTO user_providers (user_id, type, name, base_url, api_key, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        user_id,
                        provider["type"].strip().lower(),
                        provider["name"].strip(),
                        provider.get("base_url", "").strip(),
                        provider.get("api_key", "").strip(),
                        is_active
                    )
                )
                return count

    def update_provider(self, user_id: str, index: int, patch: dict) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Retrieve the provider at the given index
                cur.execute(
                    "SELECT * FROM user_providers WHERE user_id = %s ORDER BY id ASC",
                    (user_id,)
                )
                rows = cur.fetchall()
                if index < 0 or index >= len(rows):
                    raise ValueError(f"Invalid provider index: {index}")
                
                target_id = rows[index]["id"]
                sets = []
                params = []
                for k in ("type", "name", "base_url", "api_key"):
                    if k in patch:
                        sets.append(f"{k} = %s")
                        val = str(patch[k]).strip()
                        if k == "type":
                            val = val.lower()
                        params.append(val)
                
                if sets:
                    params.append(target_id)
                    cur.execute(
                        f"UPDATE user_providers SET {', '.join(sets)} WHERE id = %s",
                        params
                    )
                
                # Fetch updated record
                cur.execute("SELECT * FROM user_providers WHERE id = %s", (target_id,))
                updated_row = cur.fetchone()
                return {
                    "type": updated_row["type"],
                    "name": updated_row["name"],
                    "base_url": updated_row["base_url"] or "",
                    "api_key": updated_row["api_key"] or "",
                }

    def remove_provider(self, user_id: str, index: int) -> bool:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM user_providers WHERE user_id = %s ORDER BY id ASC",
                    (user_id,)
                )
                rows = cur.fetchall()
                if index < 0 or index >= len(rows):
                    return False
                
                target = rows[index]
                was_active = target["is_active"]
                cur.execute("DELETE FROM user_providers WHERE id = %s", (target["id"],))

                # If the active provider was deleted, set the first remaining provider as active
                if was_active:
                    cur.execute(
                        "SELECT id FROM user_providers WHERE user_id = %s ORDER BY id ASC LIMIT 1",
                        (user_id,)
                    )
                    next_active = cur.fetchone()
                    if next_active:
                        cur.execute(
                            "UPDATE user_providers SET is_active = TRUE WHERE id = %s",
                            (next_active["id"],)
                        )
                return True

    def set_active_provider(self, user_id: str, index: int) -> None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM user_providers WHERE user_id = %s ORDER BY id ASC",
                    (user_id,)
                )
                rows = cur.fetchall()
                if index < 0 or index >= len(rows):
                    raise ValueError(f"Invalid provider index: {index}")
                
                target_id = rows[index]["id"]
                cur.execute("UPDATE user_providers SET is_active = FALSE WHERE user_id = %s", (user_id,))
                cur.execute("UPDATE user_providers SET is_active = TRUE WHERE id = %s", (target_id,))

    def write_audit_log(self, user_id: str, action: str, details: str) -> None:
        now = self._now()
        log_id = self._new_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_logs (id, user_id, action, details, timestamp)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (log_id, user_id, action, details, now),
                )

    def record_token_usage(
        self, user_id: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        now = self._now()
        row_id = self._new_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO user_token_usages (id, user_id, model, input_tokens, output_tokens, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT(user_id, model) DO UPDATE SET
                         input_tokens = user_token_usages.input_tokens + EXCLUDED.input_tokens,
                         output_tokens = user_token_usages.output_tokens + EXCLUDED.output_tokens,
                         updated_at = EXCLUDED.updated_at""",
                    (row_id, user_id, model, input_tokens, output_tokens, now),
                )

    def get_user_entity(self, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_entities WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def save_user_entity(self, user_id: str, entity_id: str, credentials: str) -> None:
        now = self._now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO user_entities (user_id, entity_id, credentials, updated_at)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT(user_id) DO UPDATE SET
                         entity_id = EXCLUDED.entity_id,
                         credentials = EXCLUDED.credentials,
                         updated_at = EXCLUDED.updated_at""",
                    (user_id, entity_id, credentials, now),
                )

    def add_heartbeat_job(self, user_id: str, job_dict: dict) -> dict:
        import time
        now = int(time.time() * 1000)
        job_id = job_dict.get("id") or self._new_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO heartbeat_jobs (id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        job_id,
                        user_id,
                        job_dict["name"],
                        job_dict.get("description", ""),
                        1 if job_dict.get("enabled", True) else 0,
                        job_dict["type"],
                        job_dict.get("instruction", ""),
                        job_dict.get("script_path", ""),
                        json.dumps(job_dict.get("schedule", {})),
                        int(job_dict.get("created_at_ms") or now),
                        int(job_dict.get("updated_at_ms") or now),
                        json.dumps(job_dict.get("state", {})),
                    ),
                )
        job_dict["id"] = job_id
        job_dict["created_at_ms"] = job_dict.get("created_at_ms") or now
        job_dict["updated_at_ms"] = job_dict.get("updated_at_ms") or now
        return job_dict

    def remove_heartbeat_job(self, user_id: str, job_id: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM heartbeat_jobs WHERE user_id = %s AND id = %s",
                    (user_id, job_id),
                )
                affected = cur.rowcount
                cur.execute(
                    "DELETE FROM heartbeat_runs WHERE user_id = %s AND job_id = %s",
                    (user_id, job_id),
                )
                return affected > 0

    def update_heartbeat_job(self, user_id: str, job_id: str, patch: dict) -> dict:
        import time
        now = int(time.time() * 1000)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs WHERE user_id = %s AND id = %s",
                    (user_id, job_id),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Job {job_id} not found")
                
                name = patch.get("name", row[2])
                description = patch.get("description", row[3])
                
                if "enabled" in patch:
                    enabled = 1 if patch["enabled"] else 0
                else:
                    enabled = row[4]
                    
                instruction = patch.get("instruction", row[6])
                script_path = patch.get("script_path", row[7])
                
                if "schedule" in patch:
                    schedule_json = json.dumps(patch["schedule"])
                else:
                    schedule_json = row[8]
                    
                if "state" in patch:
                    state_json = json.dumps(patch["state"])
                else:
                    state_json = row[11]
                    
                cur.execute(
                    """UPDATE heartbeat_jobs
                       SET name = %s, description = %s, enabled = %s, instruction = %s, script_path = %s, schedule_json = %s, state_json = %s, updated_at_ms = %s
                       WHERE user_id = %s AND id = %s""",
                    (
                        name,
                        description,
                        enabled,
                        instruction,
                        script_path,
                        schedule_json,
                        state_json,
                        now,
                        user_id,
                        job_id,
                    ),
                )
                
                cur.execute(
                    "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs WHERE user_id = %s AND id = %s",
                    (user_id, job_id),
                )
                r = cur.fetchone()
                if r:
                    return {
                        "id": r[0],
                        "user_id": r[1],
                        "name": r[2],
                        "description": r[3],
                        "enabled": bool(r[4]),
                        "type": r[5],
                        "instruction": r[6],
                        "script_path": r[7],
                        "schedule_json": r[8],
                        "created_at_ms": r[9],
                        "updated_at_ms": r[10],
                        "state_json": r[11]
                    }
                return {}

    def get_heartbeat_job(self, user_id: str, job_id: str) -> dict | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs WHERE user_id = %s AND id = %s",
                    (user_id, job_id),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "user_id": row[1],
                        "name": row[2],
                        "description": row[3],
                        "enabled": bool(row[4]),
                        "type": row[5],
                        "instruction": row[6],
                        "script_path": row[7],
                        "schedule_json": row[8],
                        "created_at_ms": row[9],
                        "updated_at_ms": row[10],
                        "state_json": row[11]
                    }
                return None

    def list_heartbeat_jobs(self, user_id: str, include_disabled: bool = False) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if include_disabled:
                    cur.execute(
                        "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs WHERE user_id = %s ORDER BY created_at_ms DESC",
                        (user_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs WHERE user_id = %s AND enabled = 1 ORDER BY created_at_ms DESC",
                        (user_id,),
                    )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row[0],
                        "user_id": row[1],
                        "name": row[2],
                        "description": row[3],
                        "enabled": bool(row[4]),
                        "type": row[5],
                        "instruction": row[6],
                        "script_path": row[7],
                        "schedule_json": row[8],
                        "created_at_ms": row[9],
                        "updated_at_ms": row[10],
                        "state_json": row[11]
                    })
                return results

    def list_all_heartbeat_jobs(self) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, name, description, enabled, type, instruction, script_path, schedule_json, created_at_ms, updated_at_ms, state_json FROM heartbeat_jobs ORDER BY created_at_ms DESC"
                )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row[0],
                        "user_id": row[1],
                        "name": row[2],
                        "description": row[3],
                        "enabled": bool(row[4]),
                        "type": row[5],
                        "instruction": row[6],
                        "script_path": row[7],
                        "schedule_json": row[8],
                        "created_at_ms": row[9],
                        "updated_at_ms": row[10],
                        "state_json": row[11]
                    })
                return results

    def save_heartbeat_job_state(self, user_id: str, job_id: str, state_dict: dict) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE heartbeat_jobs SET state_json = %s WHERE user_id = %s AND id = %s",
                    (json.dumps(state_dict), user_id, job_id),
                )

    def add_heartbeat_run_log(self, user_id: str, run_dict: dict) -> None:
        import time
        run_id = run_dict.get("id") or self._new_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO heartbeat_runs (id, job_id, user_id, status, error, result, started_at, ended_at, duration_ms, session_id, artifacts_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        run_dict["job_id"],
                        user_id,
                        run_dict["status"],
                        run_dict.get("error", ""),
                        run_dict.get("result", ""),
                        int(run_dict["started_at"] or time.time() * 1000),
                        int(run_dict["ended_at"] or time.time() * 1000),
                        int(run_dict.get("duration_ms", 0)),
                        run_dict.get("session_id", ""),
                        json.dumps(run_dict.get("artifacts", [])),
                    ),
                )

    def read_heartbeat_run_logs(self, user_id: str, job_id: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, job_id, user_id, status, error, result, started_at, ended_at, duration_ms, session_id, artifacts_json FROM heartbeat_runs
                       WHERE user_id = %s AND job_id = %s
                       ORDER BY started_at DESC
                       LIMIT %s""",
                    (user_id, job_id, limit),
                )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row[0],
                        "job_id": row[1],
                        "user_id": row[2],
                        "status": row[3],
                        "error": row[4],
                        "result": row[5],
                        "started_at": row[6],
                        "ended_at": row[7],
                        "duration_ms": row[8],
                        "session_id": row[9],
                        "artifacts_json": row[10]
                    })
                return results

    def close(self):
        """Clean up pool connections."""
        if self.pool:
            self.pool.closeall()
            logger.info("PostgreSQL connection pool closed.")
