"""PC Mirror Workspace Sandbox utility implementation.

Provides support for:
- Copy-on-write sandbox workspace mirroring.
- Git-based version management (auto-commit per agent round).
- Diff calculations (via git or fallback difflib).
- Applying changes.
- Discarding changes.
- Version history listing and rollback.
"""

from __future__ import annotations

import difflib
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories/files excluded from sandbox copy and git tracking
EXCLUDE_NAMES = {".git", ".gitignore", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".DS_Store"}

# File extensions excluded from sandbox (runtime/database files)
EXCLUDE_EXTENSIONS = {".db", ".db-wal", ".db-shm", ".db-journal"}

# Directories that should be symlinked instead of copied
SYMLINK_DEPS = ["node_modules", ".venv"]


def _should_exclude(filename: str) -> bool:
    """Check if a file should be excluded from sandbox operations."""
    if filename in EXCLUDE_NAMES:
        return True
    # Check extension-based exclusions (handles compound extensions like .db-wal)
    for ext in EXCLUDE_EXTENSIONS:
        if filename.endswith(ext):
            return True
    return False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_run(
    *args: str,
    cwd: str | Path,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a git command inside *cwd*, suppressing user-level git config noise."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "yanyu-wit-sandbox",
        "GIT_AUTHOR_EMAIL": "sandbox@yanyu-wit.local",
        "GIT_COMMITTER_NAME": "yanyu-wit-sandbox",
        "GIT_COMMITTER_EMAIL": "sandbox@yanyu-wit.local",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=capture,
        text=True,
        check=check,
    )


def _git_init_sandbox(sandbox_dir: Path) -> None:
    """Initialize a git repo inside *sandbox_dir* and commit everything."""
    _git_run("init", cwd=sandbox_dir)

    # Create .gitignore to skip symlinked deps
    gitignore = sandbox_dir / ".gitignore"
    gitignore.write_text("\n".join(SYMLINK_DEPS) + "\n", encoding="utf-8")

    _git_run("add", "-A", cwd=sandbox_dir)
    _git_run("commit", "-m", "Initial workspace snapshot", "--allow-empty", cwd=sandbox_dir)
    logger.info("Git sandbox initialized at %s", sandbox_dir)


def git_commit_round(sandbox_dir: str | Path, round_number: int, summary: str = "") -> bool:
    """Stage all changes and commit as a numbered round.

    Returns True if a commit was created, False if working tree is clean.
    """
    sandbox_dir = Path(sandbox_dir).resolve()
    if not (sandbox_dir / ".git").exists():
        logger.warning("git_commit_round called on non-git sandbox: %s", sandbox_dir)
        return False

    _git_run("add", "-A", cwd=sandbox_dir)

    # Check if there is anything to commit
    result = _git_run("status", "--porcelain", cwd=sandbox_dir, check=False)
    if not result.stdout.strip():
        logger.debug("No changes to commit for round %d", round_number)
        return False

    msg = f"Round {round_number}"
    if summary:
        msg += f": {summary}"
    _git_run("commit", "-m", msg, "--allow-empty", cwd=sandbox_dir)
    logger.info("Committed sandbox round %d at %s", round_number, sandbox_dir)
    return True


def list_versions(sandbox_dir: str | Path) -> list[dict]:
    """Return the commit history as a list of {hash, message, timestamp} dicts.

    Most recent commit first.
    """
    sandbox_dir = Path(sandbox_dir).resolve()
    if not (sandbox_dir / ".git").exists():
        return []

    # --format: abbreviated hash | subject | ISO timestamp
    result = _git_run(
        "log", "--format=%h|%s|%aI", "--no-walk=unsorted",
        cwd=sandbox_dir, check=False,
    )
    if result.returncode != 0:
        # Try without --no-walk (older git)
        result = _git_run("log", "--format=%h|%s|%aI", cwd=sandbox_dir, check=False)

    if result.returncode != 0 or not result.stdout.strip():
        return []

    versions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            versions.append({
                "hash": parts[0],
                "message": parts[1],
                "timestamp": parts[2],
            })
    return versions


def revert_to_version(sandbox_dir: str | Path, commit_hash: str) -> bool:
    """Hard-reset the sandbox working tree to *commit_hash*.

    Returns True on success.
    """
    sandbox_dir = Path(sandbox_dir).resolve()
    if not (sandbox_dir / ".git").exists():
        return False

    result = _git_run("checkout", commit_hash, "--", ".", cwd=sandbox_dir, check=False)
    if result.returncode != 0:
        logger.error("Failed to revert sandbox to %s: %s", commit_hash, result.stderr)
        return False

    # Clean untracked files that were added after the target commit
    _git_run("clean", "-fd", cwd=sandbox_dir, check=False)
    logger.info("Reverted sandbox to version %s", commit_hash)
    return True


def get_sandbox_diff_via_git(sandbox_dir: str | Path) -> str:
    """Return a unified diff string of uncommitted changes using git diff."""
    sandbox_dir = Path(sandbox_dir).resolve()
    if not (sandbox_dir / ".git").exists():
        return ""

    # Stage everything first so we see new files too
    _git_run("add", "-A", cwd=sandbox_dir, check=False)
    result = _git_run("diff", "--cached", cwd=sandbox_dir, check=False)
    return result.stdout or ""


# ---------------------------------------------------------------------------
# Core sandbox operations
# ---------------------------------------------------------------------------

def prepare_sandbox(workspace_dir: str | Path, sandbox_dir: str | Path) -> Path:
    """Prepares a sandbox workspace copy of the physical workspace.

    Excludes version control, dependencies, and caching directories.
    On first creation, initializes a git repository for version tracking.
    """
    workspace_dir = Path(workspace_dir).resolve()
    sandbox_dir = Path(sandbox_dir).resolve()

    is_new = not sandbox_dir.exists()

    if is_new:
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Creating new sandbox at %s for workspace %s", sandbox_dir, workspace_dir)
    else:
        logger.debug("Refreshing existing sandbox at %s", sandbox_dir)

    for root, dirs, files in os.walk(workspace_dir):
        # Filter directories in-place to prevent traversing excluded ones
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]

        rel_path = Path(root).relative_to(workspace_dir)
        dest_dir = sandbox_dir / rel_path
        dest_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            if _should_exclude(file):
                continue
            src_file = Path(root) / file
            dest_file = dest_dir / file

            # Check if dest file exists and is identical
            if dest_file.exists():
                try:
                    src_stat = src_file.stat()
                    dest_stat = dest_file.stat()
                    if src_stat.st_size == dest_stat.st_size and abs(src_stat.st_mtime - dest_stat.st_mtime) < 0.01:
                        continue
                except Exception:
                    pass

            try:
                shutil.copy2(src_file, dest_file)
            except Exception as e:
                logger.warning(f"Failed to copy file {src_file} to sandbox: {e}")

    # Symlink node_modules and .venv
    for dep in SYMLINK_DEPS:
        src_dep = workspace_dir / dep
        if src_dep.exists():
            dest_dep = sandbox_dir / dep
            if not dest_dep.exists():
                try:
                    os.symlink(src_dep, dest_dep, target_is_directory=True)
                except Exception as e:
                    logger.warning(f"Failed to symlink {src_dep} to sandbox: {e}")

    # Initialize git on first sandbox creation
    if is_new:
        try:
            _git_init_sandbox(sandbox_dir)
        except Exception as e:
            logger.warning("Failed to initialize git in sandbox: %s", e)

    return sandbox_dir


def get_sandbox_diff(workspace_dir: str | Path, sandbox_dir: str | Path) -> list[dict]:
    """Calculates difference between host workspace and sandbox workspace."""
    workspace_dir = Path(workspace_dir).resolve()
    sandbox_dir = Path(sandbox_dir).resolve()

    workspace_files = set()
    if workspace_dir.exists():
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
            for file in files:
                if _should_exclude(file):
                    continue
                workspace_files.add((Path(root) / file).relative_to(workspace_dir))

    sandbox_files = set()
    if sandbox_dir.exists():
        for root, dirs, files in os.walk(sandbox_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
            for file in files:
                if _should_exclude(file):
                    continue
                sandbox_files.add((Path(root) / file).relative_to(sandbox_dir))

    added = sandbox_files - workspace_files
    deleted = workspace_files - sandbox_files
    common = workspace_files & sandbox_files

    modified = set()
    for rel in common:
        src = workspace_dir / rel
        dst = sandbox_dir / rel
        try:
            src_stat = src.stat()
            dst_stat = dst.stat()
            if src_stat.st_size != dst_stat.st_size or abs(src_stat.st_mtime - dst_stat.st_mtime) > 0.01:
                # Avoid false positives using binary comparison
                if src.read_bytes() != dst.read_bytes():
                    modified.add(rel)
        except Exception:
            pass

    diffs = []

    def is_text_file(path: Path) -> bool:
        try:
            chunk = path.read_bytes()[:1024]
            return b'\x00' not in chunk
        except Exception:
            return False

    # Added files
    for rel in sorted(added):
        dst = sandbox_dir / rel
        is_text = is_text_file(dst)
        diff_text = "Binary file added"
        if is_text:
            try:
                content = dst.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines(keepends=True)
                diff_text = "".join(difflib.unified_diff(
                    [], lines,
                    fromfile="/dev/null",
                    tofile=f"b/{rel}"
                ))
            except Exception as e:
                diff_text = f"Error reading added file: {e}"
        diffs.append({
            "type": "added",
            "path": str(rel),
            "is_text": is_text,
            "diff": diff_text
        })

    # Deleted files
    for rel in sorted(deleted):
        src = workspace_dir / rel
        is_text = is_text_file(src)
        diff_text = "Binary file deleted"
        if is_text:
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines(keepends=True)
                diff_text = "".join(difflib.unified_diff(
                    lines, [],
                    fromfile=f"a/{rel}",
                    tofile="/dev/null"
                ))
            except Exception as e:
                diff_text = f"Error reading deleted file: {e}"
        diffs.append({
            "type": "deleted",
            "path": str(rel),
            "is_text": is_text,
            "diff": diff_text
        })

    # Modified files
    for rel in sorted(modified):
        src = workspace_dir / rel
        dst = sandbox_dir / rel
        is_text = is_text_file(src) and is_text_file(dst)
        diff_text = "Binary file modified"
        if is_text:
            try:
                src_lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                dst_lines = dst.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                diff_text = "".join(difflib.unified_diff(
                    src_lines, dst_lines,
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}"
                ))
            except Exception as e:
                diff_text = f"Error generating diff: {e}"
        diffs.append({
            "type": "modified",
            "path": str(rel),
            "is_text": is_text,
            "diff": diff_text
        })

    return diffs


def apply_sandbox_changes(workspace_dir: str | Path, sandbox_dir: str | Path) -> None:
    """Applies all modifications from the sandbox to the host workspace, then cleans it."""
    workspace_dir = Path(workspace_dir).resolve()
    sandbox_dir = Path(sandbox_dir).resolve()

    diffs = get_sandbox_diff(workspace_dir, sandbox_dir)
    logger.info("Applying %d sandbox changes from %s to %s", len(diffs), sandbox_dir, workspace_dir)
    for d in diffs:
        rel = d["path"]
        src = workspace_dir / rel
        dst = sandbox_dir / rel
        if d["type"] in ("added", "modified"):
            src.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(dst, src)
            except Exception as e:
                logger.error(f"Failed to copy file {dst} back to workspace {src}: {e}")
        elif d["type"] == "deleted":
            if src.exists():
                try:
                    src.unlink()
                except Exception as e:
                    logger.error(f"Failed to delete file {src} in workspace: {e}")

    discard_sandbox_changes(sandbox_dir)


def discard_sandbox_changes(sandbox_dir: str | Path) -> None:
    """Discards the sandbox directory completely."""
    sandbox_dir = Path(sandbox_dir).resolve()
    if sandbox_dir.exists():
        try:
            shutil.rmtree(sandbox_dir)
        except Exception as e:
            logger.error(f"Failed to clean up sandbox directory {sandbox_dir}: {e}")
