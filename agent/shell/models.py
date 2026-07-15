"""Shell 执行引擎的数据模型定义。"""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import NotRequired, TypedDict

class FileInfo(TypedDict):
    """文件/目录条目信息。

    用于 ls / glob 的返回值，以描述单个文件或目录。
    """

    path: str
    """文件或目录的路径。"""

    is_dir: NotRequired[bool]
    """是否为目录。"""

    size: NotRequired[int]
    """文件大小（字节）。"""

    modified_at: NotRequired[str]
    """最后修改时间，ISO 8601 格式。"""


class GrepMatch(TypedDict):
    """单个 grep 搜索匹配结果。"""

    path: str
    """包含匹配项的文件路径。"""

    line: int
    """从 1 开始的行号。"""

    text: str
    """匹配行的内容。"""


@dataclass
class LsOutput:
    """ls 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        entries: 成功时的文件条目列表，失败时为 None。
    """

    error: str | None = None
    entries: list[FileInfo] | None = None


@dataclass
class ReadOutput:
    """read 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        content: 成功时的文件内容（带有行号的文本），失败时为 None。
    """

    error: str | None = None
    content: str | None = None


@dataclass
class WriteOutput:
    """write 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        path: 成功时写入的文件路径，失败时为 None。
    """

    error: str | None = None
    path: str | None = None


@dataclass
class EditOutput:
    """edit 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        path: 成功时编辑的文件路径，失败时为 None。
        occurrences: 替换发生的次数，失败时为 None。
    """

    error: str | None = None
    path: str | None = None
    occurrences: int | None = None


@dataclass
class GrepOutput:
    """grep 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        matches: 成功时的匹配项列表，失败时为 None。
    """

    error: str | None = None
    matches: list[GrepMatch] | None = None


@dataclass
class GlobOutput:
    """glob 操作返回值。

    属性:
        error: 失败时的错误消息，成功时为 None。
        matches: 成功时的匹配文件信息列表，失败时为 None。
    """

    error: str | None = None
    matches: list[FileInfo] | None = None


@dataclass
class ExecuteOutput:
    """命令执行返回值。

    属性:
        output: 合并后的 stdout + stderr 输出。
        exit_code: 进程退出代码，0 表示成功。
        truncated: 输出是否因长度限制而被截断。
    """

    output: str
    exit_code: int | None = None
    truncated: bool = False


@dataclass
class FileUploadOutput:
    """单个文件上传结果。

    属性:
        path: 目标文件路径。
        error: 失败时的错误消息，成功时为 None。
    """

    path: str
    error: str | None = None


@dataclass
class FileDownloadOutput:
    """单个文件下载结果。

    属性:
        path: 请求下载的文件路径。
        content: 成功时的文件二进制内容，失败时为 None。
        error: 失败时的错误消息，成功时为 None。
    """

    path: str
    content: bytes | None = None
    error: str | None = None


__all__ = [
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
