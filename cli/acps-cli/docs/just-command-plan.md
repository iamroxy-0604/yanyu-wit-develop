# acps-cli Just 命令改造计划

## 1. 目标

本计划基于 `/Users/huxiaofeng/Projects/acps/acps-design/Dev-Just-Command-Design.md`，并参考 `registry-server` 与 `ca-server` 已落地的 `Justfile` 实现，目标是在 `acps-cli` 中建立统一的 Just 命令体系。

`acps-cli` 是工具型项目，不启动常驻应用进程，因此应保留统一设计中的五个 domain：`infra`、`doctor`、`prep`、`test`、`qa`，明确不提供 `app`。

首轮目标：

1. 用顶层 `Justfile` 收敛当时散落在 README 与旧本地便捷脚本中的开发入口。
2. 让 `just doctor` 承接“联调环境检查”职责，统一检查工具链、默认配置和后端服务可达性。
3. 让 `just test integration` / `just test e2e` 在进入 pytest 前先做 fail-fast 检查，而不是把“环境未就绪”留给测试层静默 skip。

## 2. 当前现状分析

1. 改造前没有 `Justfile`，开发入口散落在 README 的裸 `uv` 命令和旧本地便捷脚本中。
2. `acps-cli` 是纯 CLI 工具，不启动 FastAPI、数据库或消息队列进程，因此没有也不应引入 `app` domain。
3. 根目录已有 `acps-cli.toml` 作为默认本地配置文件，`.env.example` 只承载登录类命令所需的敏感凭证示例；`.env` 不是所有命令的硬前置条件。
4. 旧本地便捷脚本当时只承担两类能力：共享 PostgreSQL 的 `up/down/status` 代理，以及对 `registry-server`、`ca-server`、`discovery-server` 的健康检查。
5. `tests/integration/conftest.py` 和 `tests/e2e/conftest.py` 目前在服务不可达时会 `pytest.skip(...)`；Just 入口更适合在进入这些测试前先统一做环境检查。

## 3. 设计判断

1. `acps-cli` 应提供 `infra`、`doctor`、`prep`、`test`、`qa` 五个 domain，默认动作分别与设计文档保持一致：`infra=status`、`prep=all`、`test=all`、`qa=all`。
2. `infra` 仍统一代理到 `../acps-infra/dev-infra/dev-infra.sh`，因为本地联调依赖的兄弟服务仍共享该 PostgreSQL 环境；但 `infra` 仅负责共享依赖，不负责启动兄弟服务。
3. `doctor` 不应照搬服务型项目的实现。它的核心职责是“基于 `acps-cli.toml` 与环境变量检查目标后端服务是否可达”，而不是检查某个本地应用进程或数据库迁移状态。
4. `doctor` 中 `.env` 缺失只能给 warning，不能作为硬失败项；否则会错误阻断大量不依赖登录态的 CLI 场景。
5. `doctor` 也不应把 `acps-infra` 是否存在视为所有场景的硬前置条件。对 `acps-cli` 而言，服务可达性比共享 infra 的存在更直接；若用户配置的是外部测试环境，`doctor` 仍应能够独立完成检查。
6. `test bootstrap` 保留为工具型项目的标准入口，但其职责仅限“同步本地依赖并拉起共享 PostgreSQL”；它不负责替用户启动 `registry-server`、`ca-server`、`discovery-server`。

## 4. 目标命令结构

### 4.1 目标 domain

| domain   | 默认动作 | acps-cli 中的目标职责                                                           |
| -------- | -------- | ------------------------------------------------------------------------------- |
| `infra`  | `status` | 统一代理共享 infra 工具                                                         |
| `doctor` | 总检查   | 检查 Python / uv / just / `acps-cli.toml` / `uv sync` 状态 / 默认目标服务可达性 |
| `prep`   | `all`    | 生成 `.env`、同步依赖                                                           |
| `test`   | `all`    | 分层执行 `bootstrap` / `unit` / `integration` / `e2e`，并支持透传 pytest 参数   |
| `qa`     | `all`    | 格式化、类型检查、pre-commit 门禁                                               |

说明：

1. 不提供 `app` domain。
2. 这不是例外，而是工具型项目与服务型项目的刻意边界差异。

### 4.2 目标命令映射

| 现有入口                                         | 目标 Just 命令           | 说明                       |
| ------------------------------------------------ | ------------------------ | -------------------------- |
| 旧脚本 `up`                                      | `just infra up postgres` | 启动共享 PostgreSQL        |
| 旧脚本 `doctor`                                  | `just doctor`            | 统一联调环境检查入口       |
| `uv sync`                                        | `just prep sync`         | 依赖同步入口               |
| `uv run pytest tests/unit/`                      | `just test unit`         | 单元测试入口               |
| `uv run pytest tests/integration/`               | `just test integration`  | 进入前先执行 `just doctor` |
| `uv run pytest tests/e2e/`                       | `just test e2e`          | 进入前先执行 `just doctor` |
| `uv run pytest`                                  | `just test all`          | 全量测试入口               |
| `uv run ruff format .` / `uv run mypy acps_cli/` | `just qa ...`            | 统一到 `qa`                |

## 5. Justfile 改造策略

### 5.1 `infra`

建议统一代理到共享工具：

```just
infra action='status' *args:
    @../acps-infra/dev-infra/dev-infra.sh {{action}} {{args}}
```

说明：

1. `acps-cli` 本身不启动服务，但其本地联调依赖的兄弟服务需要共享 PostgreSQL，因此 `infra` 仍然有价值。
2. `infra` 只管共享依赖，不管服务启动，不检查 CLI 配置。

### 5.2 `doctor`

`just doctor` 建议检查以下内容：

1. `python3` 是否存在，且版本满足 `pyproject.toml` 的 `>=3.10` 要求。
2. `uv` 是否存在，且 `uv sync --check --locked` 通过。
3. `just` 是否存在。
4. 根目录 `acps-cli.toml` 是否存在。
5. 根据以下优先级解析待检查的服务基地址，并对 `/health` 端点做连通性检查：
   - 优先读取环境变量 `REGISTRY_URL` / `CA_URL` / `DISCO_URL`
   - 若未设置，则从 `acps-cli.toml` 的 `[registry].server_base_url`、`[ca].server_base_url`、`[discovery].server_base_url` 推导
   - 若配置中带有路径前缀（如 `/api/v1`、`/acps-atr-v2`），则健康检查 URL 统一回退到 `scheme://host[:port]/health`
6. `.env` 缺失时仅给出 warning，提示“登录相关命令可能不可用”。

说明：

1. `doctor` 的结论是“默认联调目标是否可用”，不是“本地所有依赖都按推荐方式搭好了”。
2. `doctor` 不自动拉起共享 infra，不自动启动兄弟项目，也不自动生成 `.env`。

### 5.3 `prep`

建议动作如下：

1. `just prep env`：缺失时从 `.env.example` 复制生成 `.env`。
2. `just prep sync`：执行 `uv sync`。
3. `just prep all`：执行 `env + sync`。

说明：

1. `acps-cli` 无数据库迁移，因此不提供 `prep migrate`。
2. `prep` 只准备项目自身开发环境，不负责启动外部服务。

### 5.4 `test`

建议采用标准分层，并支持 pytest 参数透传：

1. `just test bootstrap`：执行 `just infra up postgres` → `just prep env` → `just prep sync`。
2. `just test unit`：执行 `uv run pytest tests/unit/`；若带参数，则允许覆盖默认路径或附加 pytest 选项。
3. `just test integration`：先执行 `just doctor`，再执行 `uv run pytest tests/integration/`。
4. `just test e2e`：先执行 `just doctor`，再执行 `uv run pytest tests/e2e/`。
5. `just test all`：顺序执行 `unit -> integration -> e2e`。

说明：

1. `integration` 与 `e2e` 不再隐式执行 `test bootstrap`；和服务型项目一样，它们只负责“检查后执行本次测试”。
2. `test bootstrap` 的作用是建立共享前置环境，不等价于“所有兄弟服务已经启动”。
3. 测试层现有的 `pytest.skip(...)` 可以暂时保留作为第二层保护，但主路径应由 `just doctor` 先 fail-fast。

### 5.5 `qa`

建议动作映射如下：

1. `just qa fmt` → `uv run ruff format .`
2. `just qa type` → `uv run mypy acps_cli/`
3. `just qa fix` → `uv run ruff format .` + `uv run ruff check . --fix`
4. `just qa precommit` → `uv run pre-commit run --all-files`
5. `just qa all` → `just qa fix` → `just qa precommit`

说明：

1. `bandit` 已由 pre-commit 承接，因此首轮不单独新增 `qa security` 动作。
2. `qa` 保持与 `registry-server` / `ca-server` 相同的语义模型。

## 6. 实施步骤

1. 更新本计划，明确 `acps-cli` 的工具型边界与 `doctor` / `test bootstrap` 的语义。
2. 创建顶层 `Justfile`，先实现 `help`、`infra`、`doctor`、`prep`、`test`、`qa`。
3. 优先落地 `doctor`，确保其能从当前配置推导服务健康检查地址。
4. 再实现 `prep`、`test`、`qa`，使主要开发入口收敛为 `just prep`、`just test ...`、`just qa ...`。
5. 完成后按命令逐项验证 `help`、`doctor`、`prep`、`infra`、`test`、`qa` 的行为。
6. 在 Just 工作流稳定后，再同步 README 与旧提示文案。

## 7. 验收标准

1. `acps-cli` 根目录新增可用的 `Justfile`，且不暴露空壳 `app` domain。
2. `just doctor` 能明确区分“工具链未就绪”和“后端服务不可达”两类问题，并显示对应服务名与健康检查地址。
3. `just prep` 仅完成 `.env` 初始化与依赖同步，不误导用户认为它会启动兄弟服务。
4. `just test integration` 与 `just test e2e` 在环境未准备好时，由 `doctor` 先显式失败。
5. `just qa` 能覆盖当前本地自动修复与门禁动作。

## 8. 本项目特有风险与注意事项

1. `acps-cli` 没有 `app` domain，这是设计要求，不应补空壳命令来追求表面一致。
2. `acps-cli.toml` 是本项目的默认联调配置锚点；若 `doctor` 忽略它而只写死 localhost 端口，会让自定义配置场景失效。
3. `.env` 不是绝对硬前置条件；把它升级成失败条件会误伤大量不依赖账号密码的命令与测试场景。
4. `test bootstrap` 只能保证共享 infra 和项目依赖已准备好，不能替代兄弟服务启动。
5. 测试层当前仍保留“服务不可达就 skip”的旧保护逻辑；Just 工作流会先在入口侧 fail-fast，但后续仍建议同步测试提示文案，避免保留历史入口提示。
