"""Shell execution engine package.

Provides unified interface and implementations for Shell execution:
- BaseShellEngine: Abstract base class, defines all tool interfaces.
- LocalShellEngine: Native local Python implementation.
"""

from .base_engine import BaseShellEngine
from .local_engine import LocalShellEngine
from .docker_engine import DockerShellEngine
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

__all__ = [
    # Engines
    "BaseShellEngine",
    "LocalShellEngine",
    "DockerShellEngine",
    # Data models
    "FileInfo",
    "GrepMatch",
    "LsOutput",
    "ReadOutput",
    "WriteOutput",
    "EditOutput",
    "GrepOutput",
    "GlobOutput",
    "ExecuteOutput",
    "FileUploadOutput",
    "FileDownloadOutput",
]
