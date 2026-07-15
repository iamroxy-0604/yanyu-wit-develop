"""Path and command validation utility for enforcing security boundaries.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that must be protected from writes when they are symlinks
PROTECTED_SYMLINK_DIRS = {"node_modules", ".venv"}


class PathValidator:
    """Validates paths and command executions to prevent escaping the workspace."""

    @staticmethod
    def validate_path(path: str | Path, workspace_root: str | Path) -> Path:
        """Resolve symbolic links and ensure path is strictly within workspace_root."""
        ws_root = Path(workspace_root).resolve()
        p = Path(path)
        
        # If absolute, resolve directly; if relative, resolve from workspace root
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (ws_root / p).resolve()

        # Check directory tree containment
        try:
            resolved.relative_to(ws_root)
        except ValueError:
            raise PermissionError(
                f"Access denied: Path '{resolved}' is outside workspace '{ws_root}'"
            )

        # Check symlink dependency directory protection
        PathValidator._check_symlink_protection(resolved, ws_root)

        return resolved

    @staticmethod
    def validate_command(command: str, workspace_root: str | Path) -> None:
        """Analyze shell command string for directory traversal or absolute path leaks."""
        if not command:
            return

        # 1. Block explicit parent traversal
        if ".." in command:
            raise PermissionError(
                "Access denied: Directory traversal ('..') detected in command."
            )

        # 2. Block absolute paths pointing outside workspace or system binaries
        ws_root = str(Path(workspace_root).resolve())
        # Regex to locate Unix absolute path tokens
        matches = re.findall(r'(?:^|\s)(/[^\s]+)', command)

        # SaaS mode: strict whitelist (essential system dirs only)
        # PC mode: include local package managers and macOS system paths
        from service.feature_flags import get_flags
        if get_flags().sandbox_type == "docker":
            _allowed_prefixes = ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin")
        else:
            _allowed_prefixes = ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin",
                                 "/opt/anaconda", "/opt/homebrew", "/System")

        for path_str in matches:
            clean_path = path_str.strip('"\'(),;{}')
            # Bypass validation for standard system directories (executables)
            if clean_path.startswith(_allowed_prefixes):
                continue
            
            try:
                p = Path(clean_path).resolve()
                p.relative_to(Path(ws_root))
            except ValueError:
                raise PermissionError(
                    f"Access denied: Command references absolute path '{clean_path}' outside workspace."
                )

        # 3. Block commands that explicitly target protected symlink directories
        PathValidator._check_command_symlink_protection(command, Path(workspace_root))

    @staticmethod
    def _check_symlink_protection(target_path: Path, workspace_root: Path) -> None:
        """Check if the target path falls within a protected symlink directory.

        Protected directories (node_modules, .venv) are symlinked into the sandbox
        from the physical workspace. Writing to them would pollute the host environment.
        """
        # Walk up the path to see if it passes through a protected symlink
        current = target_path
        while current != workspace_root and current != current.parent:
            if current.name in PROTECTED_SYMLINK_DIRS:
                # Check if this directory is actually a symlink
                if current.is_symlink():
                    real_target = current.resolve()
                    raise PermissionError(
                        f"Access denied: Path '{target_path}' targets protected "
                        f"symlink directory '{current.name}' (→ {real_target}). "
                        f"Writing to symlinked dependency directories is forbidden "
                        f"to prevent polluting the host environment."
                    )
            current = current.parent

    @staticmethod
    def _check_command_symlink_protection(command: str, workspace_root: Path) -> None:
        """Check if a shell command targets protected symlink directories.

        Blocks write-like operations (rm, mv, cp to, touch, mkdir, etc.) that
        explicitly reference node_modules or .venv paths.
        """
        # Write-like command prefixes that modify the filesystem
        write_patterns = [
            r'\brm\b.*(?:node_modules|\.venv)',
            r'\bmv\b.*(?:node_modules|\.venv)',
            r'\bcp\b.*(?:node_modules|\.venv)',
            r'\btouch\b.*(?:node_modules|\.venv)',
            r'\bmkdir\b.*(?:node_modules|\.venv)',
            r'\bchmod\b.*(?:node_modules|\.venv)',
            r'\bchown\b.*(?:node_modules|\.venv)',
            r'\b(?:pip|pip3)\s+install\b',      # pip install modifies .venv
            r'\bnpm\s+install\b',                 # npm install modifies node_modules
            r'\byarn\s+(?:add|install)\b',        # yarn install modifies node_modules
            r'\bpnpm\s+(?:add|install)\b',        # pnpm install modifies node_modules
        ]

        for pattern in write_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                # Verify the directory is actually a symlink in the workspace
                for dep_name in PROTECTED_SYMLINK_DIRS:
                    dep_path = workspace_root / dep_name
                    if dep_path.is_symlink():
                        raise PermissionError(
                            f"Access denied: Command attempts to modify protected "
                            f"symlink dependency directory '{dep_name}'. "
                            f"Install/modify operations on symlinked '{dep_name}' "
                            f"are forbidden to prevent polluting the host environment."
                        )
                # Even if not currently a symlink, warn about the pattern
                break

