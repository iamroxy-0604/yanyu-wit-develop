# Copilot 项目指令

## 项目概述

acps-cli 是 ACPs 系统的统一命令行工具集，整合 Registry、CA 与 Discovery 三个子系统能力。项目通过 Click 提供多个可执行命令，面向运维和开发人员完成证书管理、注册管理与服务发现相关操作。

## 技术栈

- Python 3.10+
- Click（CLI 框架）
- httpx / requests（HTTP 客户端）
- cryptography（证书与密钥相关能力）
- python-dotenv（环境变量加载）
- 依赖管理：uv、uv.lock
- 测试：pytest（unit / integration / e2e）+ responses
- Lint/格式化：Ruff（line-length 120、双引号）
- 类型检查：mypy

## 项目结构

```text
acps_cli/
  registry/            # Registry CLI（用户端与管理员端）
  ca/                  # CA CLI（证书申请与管理）
  discovery/           # Discovery CLI
  shared/              # 共享输出与通用能力
tests/
  unit/                # 单元测试
  integration/         # 集成测试
  e2e/                 # 端到端测试
```

## 特殊说明

- 这是 CLI 项目，不是 FastAPI 服务项目
- 不包含 SQLAlchemy、Alembic、Redis、Celery 等服务端基础设施
- 通过 `[project.scripts]` 暴露命令入口：`reg-cli`、`reg-admin-cli`、`ca-cli`、`disco-cli`

## 编码约定

- 遵循 PEP 8，每行最大 120 字符
- 兼容 Python 3.10（避免使用 3.12+ / PEP 695 专属语法）
- 注释和文档使用中文，代码标识符英文
- 字符串统一双引号

## Python 环境

- 默认使用工作区内虚拟环境 `.venv`
- VS Code 可通过 `${workspaceFolder}/.venv` 自动识别解释器

## 常用命令

```bash
uv sync                     # 安装依赖
uv run ruff check .         # lint
uv run ruff format .        # 格式化
uv run mypy acps_cli/       # 类型检查
uv run pytest               # 运行全部测试
uv run pytest tests/unit/   # 运行单元测试
```
