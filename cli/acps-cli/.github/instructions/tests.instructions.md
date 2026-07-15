---
applyTo: "tests/**/*.py"
---

# 测试编写规范

## 框架与配置

- 测试框架：pytest（`unit` / `integration` / `e2e`）
- 优先编写可离线运行的测试，外部 HTTP 调用统一 mock
- 使用 `responses` 或 `unittest.mock` 模拟网络请求与文件系统副作用

## 测试分层

- `tests/unit/`：单元测试，隔离外部依赖，重点验证参数解析、命令分发、错误处理和输出内容
- `tests/integration/`：集成测试，验证命令到客户端调用链路，允许组合多个模块但仍避免真实外网依赖
- `tests/e2e/`：端到端测试，模拟真实 CLI 调用流程（参数、环境变量、配置文件）

## CLI 测试约定

- Click 命令优先使用 `click.testing.CliRunner` 进行调用
- 断言至少覆盖：退出码、关键输出、副作用（文件写入/请求调用次数）
- 对错误分支断言用户可读错误消息，避免只断言异常类型

## 命名约定

- 测试文件：`test_<功能>.py`
- 测试函数：`test_<场景>()`
- 通用 fixtures 放 `tests/conftest.py`，分层 fixtures 放各目录下 `conftest.py`

## 最佳实践

- 每个测试用例必须独立，不依赖执行顺序
- 使用 `@pytest.mark.parametrize` 覆盖参数边界
- 同时覆盖成功路径、失败路径和网络异常/超时路径
- 本项目不使用 FastAPI `ASGITransport`、Factory Boy、数据库事务回滚等服务端测试约定
