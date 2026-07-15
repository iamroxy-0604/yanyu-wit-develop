"""Local host Shell execution engine implementation.

All file operations use native Python APIs.
Command execution uses subprocess.run. No sandbox isolation.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from .base_engine import BaseShellEngine
from .models import (
    EditOutput,
    ExecuteOutput,
    FileDownloadOutput,
    FileInfo,
    FileUploadOutput,
    GlobOutput,
    GrepMatch,
    GrepOutput,
    LsOutput,
    ReadOutput,
    WriteOutput,
)


class LocalShellEngine(BaseShellEngine):
    """Local host Shell execution engine.

    Uses os/pathlib for files, subprocess for commands.
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        physical_root: str | Path | None = None,
        timeout: int = 120,
        max_output_bytes: int = 10 * 1024 * 1024,
        max_file_size_bytes: int = 30 * 1024 * 1024,
        env: dict[str, str] | None = None,
        inherit_env: bool = True,
    ) -> None:
        """Initialize local Shell engine.

        Args:
            root_dir: Working directory, defaults to current.
            physical_root: User's physical workspace root for bi-directional mapping.
            timeout: Default timeout in seconds.
            max_output_bytes: Output truncation limit.
            max_file_size_bytes: Max file size allowed to read/process.
            env: Custom environment variables.
            inherit_env: Inherit system environment variables.
        """
        if timeout <= 0:
            msg = f"timeout must be positive, got {timeout}"
            raise ValueError(msg)

        self.physical_root = Path(physical_root).resolve() if physical_root else None
        self.sandbox_root = Path(root_dir).resolve() if root_dir else None
        self.workspace_root = self.physical_root if self.physical_root else self.sandbox_root
        self.cwd = self.sandbox_root if self.sandbox_root else (self.physical_root if self.physical_root else Path.cwd())
        self._default_timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._max_file_size_bytes = max_file_size_bytes
        self._engine_id = f"local-{uuid.uuid4().hex[:8]}"

        # Build environment variables
        if inherit_env:
            self._env = os.environ.copy()
            if env is not None:
                self._env.update(env)
        else:
            self._env = env if env is not None else {}

    @property
    def id(self) -> str:
        """Unique engine instance identifier."""
        return self._engine_id

    def _to_physical_str(self, path_str: str) -> str:
        """Convert a sandbox path string back to physical path string."""
        if self.physical_root and self.sandbox_root:
            s1 = str(self.sandbox_root)
            s2 = s1[8:] if s1.startswith("/private/") else s1
            physical_str = str(self.physical_root)
            if path_str.startswith(s1):
                return path_str.replace(s1, physical_str, 1)
            if path_str.startswith(s2):
                return path_str.replace(s2, physical_str, 1)
        return path_str

    def ls(self, path: str) -> LsOutput:
        """List all contents in the specified directory.

        Args:
            path: Target directory path.

        Returns:
            LsOutput: Directory contents.
        """
        try:
            dir_path = self._resolve_path(path)
        except PermissionError as e:
            return LsOutput(error=str(e))

        if not dir_path.exists() or not dir_path.is_dir():
            return LsOutput(error=f"Directory does not exist: '{path}'")

        entries: list[FileInfo] = []
        try:
            for child in sorted(dir_path.iterdir(), key=lambda p: p.name):
                try:
                    is_dir = child.is_dir()
                    st = child.stat()
                    path_str = self._to_physical_str(str(child))
                    entry: FileInfo = {
                        "path": path_str,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else int(st.st_size),
                        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    }
                    if is_dir:
                        entry["path"] = path_str + "/"
                    entries.append(entry)
                except OSError:
                    # Skip inaccessible entries
                    continue
        except (OSError, PermissionError):
            return LsOutput(error=f"Cannot access directory: '{path}'")

        return LsOutput(entries=entries)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadOutput:
        """Read file content with line numbers.

        Args:
            file_path: File path.
            offset: Starting line index.
            limit: Max lines to read.

        Returns:
            ReadOutput: Read content.
        """
        try:
            resolved = self._resolve_path(file_path)
        except PermissionError as e:
            return ReadOutput(error=str(e))

        if not resolved.exists() or not resolved.is_file():
            return ReadOutput(error=f"File does not exist: '{file_path}'")

        # Check file size
        try:
            if resolved.stat().st_size > self._max_file_size_bytes:
                return ReadOutput(
                    error=f"File is too large to read ({resolved.stat().st_size} bytes). Limit is {self._max_file_size_bytes} bytes."
                )
        except OSError as e:
            return ReadOutput(error=f"Failed to access file stat: {e}")

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ReadOutput(error=f"File is not a text file: '{file_path}'")
        except OSError as e:
            return ReadOutput(error=f"Failed to read file '{file_path}': {e}")

        if not content:
            return ReadOutput(content="(File is empty)")

        lines = content.splitlines()
        total_lines = len(lines)

        if offset >= total_lines:
            return ReadOutput(
                error=f"Line offset {offset} exceeds file length (total {total_lines} lines)"
            )

        end_idx = min(offset+limit, total_lines)
        selected = lines[offset:end_idx]

        # Add line numbers, format: " 1: content"
        numbered_lines = []
        idx_width = len(str(end_idx))
        for i, line in enumerate(selected, start=offset+1):
            numbered_lines.append(f"{i:>{idx_width}}: {line}")

        return ReadOutput(content="\n".join(numbered_lines))

    def write(self, file_path: str, content: str) -> WriteOutput:
        """Create a new file.

        Args:
            file_path: New file path.
            content: Content to write.

        Returns:
            WriteOutput: Write result.
        """
        try:
            resolved = self._resolve_path(file_path)
        except PermissionError as e:
            return WriteOutput(error=str(e))

        if resolved.exists():
            return WriteOutput(
                error=f"File already exists: '{file_path}'. Please use edit to modify it or write to a different path."
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return WriteOutput(path=file_path)
        except OSError as e:
            return WriteOutput(error=f"Failed to write file '{file_path}': {e}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditOutput:
        """Edit file via search and replace.

        Args:
            file_path: File path.
            old_string: Exact string to replace.
            new_string: Replacement string.
            replace_all: Replace all occurrences.

        Returns:
            EditOutput: Edit result.
        """
        try:
            resolved = self._resolve_path(file_path)
        except PermissionError as e:
            return EditOutput(error=str(e))

        if not resolved.exists() or not resolved.is_file():
            return EditOutput(error=f"File does not exist: '{file_path}'")

        # Check file size
        try:
            if resolved.stat().st_size > self._max_file_size_bytes:
                return EditOutput(
                    error=f"File is too large to edit ({resolved.stat().st_size} bytes). Limit is {self._max_file_size_bytes} bytes."
                )
        except OSError as e:
            return EditOutput(error=f"Failed to access file stat: {e}")

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return EditOutput(error=f"File is not a text file: '{file_path}'")
        except OSError as e:
            return EditOutput(error=f"Failed to read file '{file_path}': {e}")

        # Unify line endings
        old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
        new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")

        count = content.count(old_string)
        if count == 0:
            return EditOutput(error=f"Target string not found: '{old_string}'")
        if count > 1 and not replace_all:
            return EditOutput(
                error=f"String '{old_string}' occurred {count} times."
                f"Please use replace_all=True to replace all occurrences."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        try:
            resolved.write_text(new_content, encoding="utf-8")
            return EditOutput(path=file_path, occurrences=count)
        except OSError as e:
            return EditOutput(error=f"Failed to write file '{file_path}': {e}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob_pattern: str | None = None,
        is_regex: bool = False,
    ) -> GrepOutput:
        """Exact text search.

        Args:
            pattern: Plain text string or regex pattern.
            path: Search directory.
            glob_pattern: File glob pattern.
            is_regex: Whether to use regex search.

        Returns:
            GrepOutput: Match results.
        """
        try:
            search_path = self._resolve_path(path or ".")
        except PermissionError as e:
            return GrepOutput(error=str(e))

        if not search_path.exists():
            return GrepOutput(matches=[])

        # Try system grep
        result = self._system_grep(pattern, search_path, glob_pattern, is_regex)
        if result is not None:
            for m in result:
                m["path"] = self._to_physical_str(m["path"])
            return GrepOutput(matches=result)

        # Python fallback
        python_matches = self._python_grep(pattern, search_path, glob_pattern, is_regex)
        for m in python_matches:
            m["path"] = self._to_physical_str(m["path"])
        return GrepOutput(matches=python_matches)

    def glob(self, pattern: str, path: str = "/", recursive: bool = False) -> GlobOutput:
        """Glob path search.

        Args:
            pattern: Glob pattern.
            path: Starting directory.
            recursive: Whether to search recursively.

        Returns:
            GlobOutput: Match results.
        """
        if pattern.startswith("/"):
            pattern = pattern.lstrip("/")

        try:
            search_path = (
                self.cwd if path == "/" else self._resolve_path(path)
            )
        except PermissionError as e:
            return GlobOutput(error=str(e))
        if not search_path.exists() or not search_path.is_dir():
            return GlobOutput(matches=[])

        results: list[FileInfo] = []
        try:
            iterator = search_path.rglob(pattern) if recursive else search_path.glob(pattern)
            for matched in iterator:
                if not matched.is_file():
                    continue
                try:
                    st = matched.stat()
                    results.append(
                        {
                            "path": self._to_physical_str(str(matched)),
                            "is_dir": False,
                            "size": int(st.st_size),
                            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                        }
                    )
                except OSError:
                    results.append({"path": self._to_physical_str(str(matched)), "is_dir": False})
        except (OSError, ValueError):
            pass

        results.sort(key=lambda x: x.get("path", ""))
        return GlobOutput(matches=results)


    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteOutput:
        """Execute shell command on the host.

        Args:
            command: Shell command string.
            timeout: Timeout in seconds.

        Returns:
            ExecuteOutput: Output and status.
        """
        if not command or not isinstance(command, str):
            return ExecuteOutput(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
            )

        if self.workspace_root:
            try:
                from agent.shell.path_validator import PathValidator
                PathValidator.validate_command(command, self.workspace_root)
            except PermissionError as e:
                return ExecuteOutput(
                    output=f"Error: {e}",
                    exit_code=1,
                )

        # Replace physical root paths with sandbox root paths in the command string (handle optional macOS /private prefix)
        if self.physical_root and self.sandbox_root:
            p1 = str(self.physical_root)
            p2 = p1[8:] if p1.startswith("/private/") else p1
            sandbox_str = str(self.sandbox_root)
            command = command.replace(p1, sandbox_str)
            if p2 != p1:
                command = command.replace(p2, sandbox_str)

        effective_timeout = timeout if timeout is not None else self._default_timeout

        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        try:
            result = subprocess.run(
                command,
                check=False,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=self._env,
                cwd=str(self.cwd),
            )

            # Merge output, prepend [stderr] to stderr lines
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                output_parts.extend(
                    f"[stderr] {line}" for line in stderr_lines
                )

            output = (
                "\n".join(output_parts) if output_parts else "<No output>"
            )

            # Map back sandbox paths to physical workspace paths in command output (handle optional macOS /private prefix)
            if self.physical_root and self.sandbox_root:
                s1 = str(self.sandbox_root)
                s2 = s1[8:] if s1.startswith("/private/") else s1
                physical_str = str(self.physical_root)
                output = output.replace(s1, physical_str)
                if s2 != s1:
                    output = output.replace(s2, physical_str)

            # Output truncation
            truncated = False
            output_bytes = output.encode("utf-8")
            if len(output_bytes) > self._max_output_bytes:
                output = output_bytes[: self._max_output_bytes].decode(
                    "utf-8", errors="ignore"
                )
                output += (
                    f"\n\n... Output truncated (limit {self._max_output_bytes} bytes)"
                )
                truncated = True

            # Append info for non-zero exit code
            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

            return ExecuteOutput(
                output=output,
                exit_code=result.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            return ExecuteOutput(
                output=f"Error: Command execution timed out ({effective_timeout} seconds).",
                exit_code=124,
            )
        except (OSError, UnicodeDecodeError) as e:
            return ExecuteOutput(
                output=f"Error executing command ({type(e).__name__}): {e}",
                exit_code=1,
            )

    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadOutput]:
        """Write files to local disk.

        Args:
            files: Path and content tuples.

        Returns:
            Upload results.
        """
        results: list[FileUploadOutput] = []
        for file_path, content in files:
            try:
                resolved = self._resolve_path(file_path)
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_bytes(content)
                results.append(FileUploadOutput(path=file_path))
            except OSError as e:
                results.append(
                    FileUploadOutput(path=file_path, error=str(e))
                )
        return results

    def download_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadOutput]:
        """Read files from local disk.

        Args:
            paths: File paths.

        Returns:
            Download results.
        """
        results: list[FileDownloadOutput] = []
        for file_path in paths:
            try:
                resolved = self._resolve_path(file_path)
                content = resolved.read_bytes()
                results.append(
                    FileDownloadOutput(path=file_path, content=content)
                )
            except FileNotFoundError:
                results.append(
                    FileDownloadOutput(
                        path=file_path, error=f"File does not exist: '{file_path}'"
                    )
                )
            except OSError as e:
                results.append(
                    FileDownloadOutput(path=file_path, error=str(e))
                )
        return results

    def _resolve_path(self, key: str) -> Path:
        """Resolve path. Uses absolute or joins with cwd."""
        p = Path(key)
        if p.is_absolute():
            resolved = p
        else:
            base = self.physical_root if self.physical_root else self.cwd
            resolved = base / p

        if self.workspace_root:
            from agent.shell.path_validator import PathValidator
            resolved = PathValidator.validate_path(resolved, self.workspace_root)
        else:
            resolved = resolved.resolve()

        if self.physical_root and self.sandbox_root:
            try:
                relative = resolved.relative_to(self.physical_root)
                resolved = self.sandbox_root / relative
            except ValueError:
                pass
        return resolved

    def _system_grep(
        self,
        pattern: str,
        search_path: Path,
        glob_pattern: str | None,
        is_regex: bool = False,
    ) -> list[GrepMatch] | None:
        """Search using system grep. Return None if not found."""

        cmd = ["grep", "-rHnE" if is_regex else "-rHnF"]
        if glob_pattern:
            cmd.extend(["--include", glob_pattern])
        cmd.extend(["--", pattern, str(search_path)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        if not proc.stdout.strip():
            return []

        matches: list[GrepMatch] = []
        for line in proc.stdout.strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) >= 3:
                try:
                    matches.append(
                        {
                            "path": parts[0],
                            "line": int(parts[1]),
                            "text": parts[2],
                        }
                    )
                except ValueError:
                    continue
        return matches

    def _python_grep(
        self,
        pattern: str,
        search_path: Path,
        glob_pattern: str | None,
        is_regex: bool = False,
    ) -> list[GrepMatch]:
        """Fallback Python search, iterating files."""
        matches: list[GrepMatch] = []
        root = search_path if search_path.is_dir() else search_path.parent

        regex = None
        if is_regex:
            try:
                regex = re.compile(pattern)
            except re.error:
                # If invalid regex, return no matches
                return []

        for fp in root.rglob("*"):
            if not fp.is_file():
                continue
            if glob_pattern and not fp.match(glob_pattern):
                continue
            try:
                if fp.stat().st_size > self._max_file_size_bytes:
                    continue
            except OSError:
                continue

            try:
                text = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_num, line in enumerate(text.splitlines(), 1):
                match_found = False
                if is_regex and regex:
                    if regex.search(line):
                        match_found = True
                else:
                    if pattern in line:
                        match_found = True
                        
                if match_found:
                    matches.append(
                        {
                            "path": str(fp),
                            "line": line_num,
                            "text": line,
                        }
                    )
        return matches


__all__ = ["DEFAULT_EXECUTE_TIMEOUT", "LocalShellEngine"]
