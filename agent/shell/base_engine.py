"""Shell 执行引擎的抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import (
    EditOutput,
    ExecuteOutput,
    FileDownloadOutput,
    FileUploadOutput,
    GlobOutput,
    GrepOutput,
    LsOutput,
    ReadOutput,
    WriteOutput,
)


class BaseShellEngine(ABC):
    """Shell 执行引擎的抽象基类。"""

    @abstractmethod
    def ls(self, path: str) -> LsOutput:
        """列出指定目录下的所有内容。

        类似于 ``ls -la``，返回文件类型、大小、修改时间等结构化信息。

        参数:
            path: 目标目录路径。

        返回:
            LsOutput: 文件条目列表或错误消息。
        """

    @abstractmethod
    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadOutput:
        """读取带有行号的文件内容。

        类似于 ``cat -n``，自动在前面加上行号。
        支持分页读取，以防止大文件导致内存溢出错误。

        参数:
            file_path: 目标文件路径。
            offset: 起始行号（从 0 开始）。
            limit: 最大读取行数，默认为 2000。

        返回:
            ReadOutput: 带有行号的文本内容或错误消息。
        """

    @abstractmethod
    def write(self, file_path: str, content: str) -> WriteOutput:
        """创建新文件并写入内容。

        仅用于创建新文件。如果文件已存在，则返回错误以防止意外覆盖。

        参数:
            file_path: 新文件路径。
            content: 要写入的文本内容。

        返回:
            WriteOutput: 写入的路径或错误消息。
        """

    @abstractmethod
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditOutput:
        """安全地修改文件内容。

        通过精确的字符串替换编辑文件。如果未找到 old_string，则返回错误。
        默认情况下，old_string 必须在文件中唯一出现，否则会报错。

        参数:
            file_path: 要编辑的文件路径。
            old_string: 要查找的精确字符串。
            new_string: 替换的字符串。
            replace_all: 是否替换所有匹配项。默认为 False，要求唯一匹配。

        返回:
            EditOutput: 编辑结果或错误消息。
        """

    @abstractmethod
    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob_pattern: str | None = None,
        is_regex: bool = False,
    ) -> GrepOutput:
        """精确文本搜索。

        通过纯文本匹配或正则表达式，在指定的目录/文件中全局搜索目标字符串。

        参数:
            pattern: 要搜索的字符串或正则表达式模式。
            path: 要搜索的目录路径，默认为当前工作目录。
            glob_pattern: 可选的文件名通配符过滤器，例如 ``*.py``。
            is_regex: 是否将模式视为正则表达式。

        返回:
            GrepOutput: 匹配项列表（文件路径、行号、匹配内容）或错误消息。
        """

    @abstractmethod
    def glob(self, pattern: str, path: str = "/", recursive: bool = False) -> GlobOutput:
        """通配符路径检索。

        查找与指定模式匹配的文件，例如 ``glob('**/*.py')``。

        参数:
            pattern: Glob 通配符模式。
            path: 搜索的起始目录，默认为 ``/``。
            recursive: 是否递归搜索。

        返回:
            GlobOutput: 匹配文件的元数据列表或错误消息。
        """

    @abstractmethod
    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteOutput:
        """执行 Shell 命令。

        参数:
            command: 要执行的完整 Shell 命令字符串。
            timeout: 最大等待时间（秒）。None 使用默认超时时间。

        返回:
            ExecuteOutput: 输出、退出代码和截断标志。
        """

    @abstractmethod
    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadOutput]:
        """批量上传文件。

        将准备好的文件数据（路径 + 二进制内容）推送到当前的后端环境。

        参数:
            files: (目标路径, 文件内容) 元组的列表。

        返回:
            每个文件的上传结果列表，与输入顺序一致。
        """

    @abstractmethod
    def download_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadOutput]:
        """批量下载文件。

        从当前的后端环境中拉取指定文件的二进制内容。

        参数:
            paths: 要下载的文件路径列表。

        返回:
            每个文件的下载结果列表，与输入顺序一致。
        """


__all__ = ["BaseShellEngine"]
