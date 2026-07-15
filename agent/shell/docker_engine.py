"""Docker Shell execution engine implementation for SaaS mode.

All file operations translate paths to physical host paths and run on host.
Command execution runs inside the Docker sandbox container.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import List, Tuple

from .local_engine import LocalShellEngine
from .models import (
    EditOutput,
    ExecuteOutput,
    FileDownloadOutput,
    FileUploadOutput,
    GlobOutput,
    GrepOutput,
    LsOutput,
    WriteOutput,
)

logger = logging.getLogger(__name__)


class DockerShellEngine(LocalShellEngine):
    """Docker-isolated Shell execution engine.

    Translates `/workspace` logical paths to host physical paths for file IO.
    Executes commands in a Docker container sandbox.
    """

    def __init__(
        self,
        physical_workspace_dir: str | Path,
        *,
        timeout: int = 120,
        max_output_bytes: int = 10 * 1024 * 1024,
        max_file_size_bytes: int = 30 * 1024 * 1024,
        env: dict[str, str] | None = None,
        inherit_env: bool = True,
    ) -> None:
        """Initialize Docker Shell engine.

        Args:
            physical_workspace_dir: The physical workspace directory on the host.
        """
        # CWD in container is logically /workspace
        super().__init__(
            root_dir="/workspace",
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            max_file_size_bytes=max_file_size_bytes,
            env=env,
            inherit_env=inherit_env,
        )
        self.physical_workspace_dir = str(Path(physical_workspace_dir).resolve())
        self._engine_id = f"docker-dynamic"
        self._ws_limit_last_check: float = 0  # Timestamp for periodic workspace limit check

    @property
    def container_id(self) -> str:
        """Dynamically resolve container_id from the active UserContext."""
        from service.context import get_current_user_ctx
        ctx = get_current_user_ctx()
        if not ctx or not ctx.container_id:
            raise ValueError("No active sandbox container found in current user context.")
        return ctx.container_id

    def _resolve_path(self, key: str) -> Path:
        """Resolve a container logical path to a host physical path."""
        p = Path(key)
        if p.is_absolute():
            logical_resolved = p
        else:
            logical_resolved = self.cwd / p

        normalized_str = os.path.normpath(str(logical_resolved))

        # Check boundary
        if not normalized_str.startswith("/workspace"):
            raise PermissionError(
                f"Access denied: Path '{key}' is outside workspace boundary."
            )

        # Get relative path
        rel_path = os.path.relpath(normalized_str, "/workspace")
        if rel_path.startswith(".."):
            raise PermissionError(
                f"Access denied: Path '{key}' escapes workspace boundary."
            )

        # Map to physical directory on host
        physical_resolved = Path(self.physical_workspace_dir) / rel_path

        # Use PathValidator to check containment against physical workspace
        from agent.shell.path_validator import PathValidator
        return PathValidator.validate_path(physical_resolved, self.physical_workspace_dir)

    def _to_logical_path(self, physical_path: str | Path) -> str:
        """Convert a physical host path back to a logical container path starting with /workspace."""
        phys_abs = str(Path(physical_path).resolve())
        phys_root = str(Path(self.physical_workspace_dir).resolve())
        if phys_abs.startswith(phys_root):
            rel = os.path.relpath(phys_abs, phys_root)
            if rel == ".":
                return "/workspace"
            return os.path.join("/workspace", rel)
        return phys_abs

    # --- Workspace soft limits (SaaS only) ---

    MAX_WORKSPACE_FILES = 10000
    MAX_WORKSPACE_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB

    def _check_workspace_limits(self) -> None:
        """Periodic soft-limit check on workspace file count and total size (runs at most once per 5 min)."""
        import time
        now = time.time()
        if now - self._ws_limit_last_check < 300:
            return
        self._ws_limit_last_check = now

        ws = Path(self.physical_workspace_dir)
        if not ws.exists():
            return

        skip = {'.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache'}
        file_count = 0
        total_size = 0
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in skip]
            file_count += len(files)
            for f in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
            if file_count > self.MAX_WORKSPACE_FILES:
                break

        if file_count > self.MAX_WORKSPACE_FILES:
            raise PermissionError(
                f"工作区文件数量超过限制 ({self.MAX_WORKSPACE_FILES})，当前 {file_count} 个文件。请清理不必要的文件。"
            )
        if total_size > self.MAX_WORKSPACE_SIZE_BYTES:
            limit_gb = self.MAX_WORKSPACE_SIZE_BYTES / (1024 ** 3)
            raise PermissionError(
                f"工作区总大小超过限制 ({limit_gb:.0f} GB)，当前 {total_size / (1024 ** 3):.1f} GB。请清理不必要的文件。"
            )

    # --- Override file APIs to map physical output paths back to logical paths ---

    def ls(self, path: str) -> LsOutput:
        res = super().ls(path)
        if res.entries:
            for entry in res.entries:
                entry["path"] = self._to_logical_path(entry["path"])
        return res

    def glob(self, pattern: str, path: str = "/", recursive: bool = False) -> GlobOutput:
        res = super().glob(pattern, path, recursive)
        if res.matches:
            for match in res.matches:
                match["path"] = self._to_logical_path(match["path"])
        return res

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob_pattern: str | None = None,
        is_regex: bool = False,
    ) -> GrepOutput:
        res = super().grep(pattern, path, glob_pattern, is_regex)
        if res.matches:
            for match in res.matches:
                match["path"] = self._to_logical_path(match["path"])
        return res

    def write(self, file_path: str, content: str) -> WriteOutput:
        self._check_workspace_limits()
        res = super().write(file_path, content)
        if res.path:
            # Log audit
            from service.context import get_current_user_ctx
            ctx = get_current_user_ctx()
            if ctx:
                try:
                    from service.app import db
                    db.write_audit_log(ctx.user_id, "write_file", f"Created file: {self._to_logical_path(res.path)}")
                except Exception as ae:
                    logger.warning(f"Failed to write audit log: {ae}")
            res.path = self._to_logical_path(res.path)
        return res

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditOutput:
        res = super().edit(file_path, old_string, new_string, replace_all)
        if res.path:
            # Log audit
            from service.context import get_current_user_ctx
            ctx = get_current_user_ctx()
            if ctx:
                try:
                    from service.app import db
                    db.write_audit_log(ctx.user_id, "edit_file", f"Edited file: {self._to_logical_path(res.path)} (occurrences={res.occurrences})")
                except Exception as ae:
                    logger.warning(f"Failed to write audit log: {ae}")
            res.path = self._to_logical_path(res.path)
        return res

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadOutput]:
        self._check_workspace_limits()
        res = super().upload_files(files)
        for r in res:
            if r.path:
                r.path = self._to_logical_path(r.path)
        return res

    def download_files(self, paths: list[str]) -> list[FileDownloadOutput]:
        res = super().download_files(paths)
        for r in res:
            if r.path:
                r.path = self._to_logical_path(r.path)
        return res

    # --- Override execute to run command inside the Docker sandbox container ---

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteOutput:
        """Execute command in the Docker container sandbox."""
        if not command or not isinstance(command, str):
            return ExecuteOutput(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
            )

        # Log command execution to audit logs
        from service.context import get_current_user_ctx
        ctx = get_current_user_ctx()
        if ctx:
            try:
                from service.app import db
                db.write_audit_log(ctx.user_id, "execute_command", f"Executed command in container: {command}")
            except Exception as ae:
                logger.warning(f"Failed to write execute audit log: {ae}")

        effective_timeout = timeout if timeout is not None else self._default_timeout

        import docker
        import shlex

        try:
            client = docker.from_env()
            container = client.containers.get(self.container_id)

            # Wrap with timeout inside the container using standard Linux command 'timeout'
            if effective_timeout:
                wrapped_command = f"timeout {effective_timeout} /bin/sh -c {shlex.quote(command)}"
            else:
                wrapped_command = command

            # Exec run in the container as non-privileged UID 1000
            exec_result = container.exec_run(
                cmd=["/bin/sh", "-c", wrapped_command],
                workdir="/workspace",
                user="1000",
                environment=self._env,
            )

            exit_code = exec_result.exit_code
            output = exec_result.output.decode("utf-8", errors="ignore") if exec_result.output else ""

            # Double-path translation for stdout/stderr content references
            phys_root = str(Path(self.physical_workspace_dir).resolve())
            if phys_root in output:
                output = output.replace(phys_root, "/workspace")

            # Handle timeout exit code
            if exit_code == 124:
                return ExecuteOutput(
                    output=f"Error: Command execution timed out ({effective_timeout} seconds).\n\n{output}",
                    exit_code=124,
                )

            # Output truncation
            truncated = False
            output_bytes = output.encode("utf-8")
            if len(output_bytes) > self._max_output_bytes:
                output = output_bytes[: self._max_output_bytes].decode("utf-8", errors="ignore")
                output += f"\n\n... Output truncated (limit {self._max_output_bytes} bytes)"
                truncated = True

            if exit_code != 0:
                output = f"{output.rstrip()}\n\nExit code: {exit_code}"

            return ExecuteOutput(
                output=output,
                exit_code=exit_code,
                truncated=truncated,
            )

        except Exception as e:
            return ExecuteOutput(
                output=f"Error executing command in container ({type(e).__name__}): {e}",
                exit_code=1,
            )
