# acps-cli

`acps-cli` 是 ACPs 的统一命令行工具集，提供 Registry、CA、Discovery 三类客户端能力，面向开发联调、调试验证和日常运维脚本使用。

## 项目概述

本项目是纯 CLI 工具，不启动 FastAPI、数据库或消息队列服务；本地开发时通过外部后端服务完成联调。

主要能力包括：

- Registry 用户端：登录、注册或更新 Agent、提交审核、获取 EAB、同步 ACS
- Registry 管理端：审核、启用、禁用 Agent
- CA 客户端：申请、续期、吊销证书，轮转 ACME 账户密钥，检查证书状态
- Discovery 客户端：触发 DSP 同步、执行查询、检查服务健康状态

## 命令与文档

当前统一入口只有 `acps-cli`。

主要命令域如下：

- `acps-cli auth` / `agent` / `entity`：Registry 用户侧操作
- `acps-cli cert`：证书生命周期与 EAB 相关操作
- `acps-cli discover`：Discovery 查询与状态查看
- `acps-cli admin ...`：Registry、CA、Discovery 管理面命令

所有 CLI 均支持：

- `--config PATH`：显式指定 `acps-cli.toml`
- `--verbose`：输出 DEBUG 日志，默认输出 INFO 及以上日志

其中需要按服务域临时覆盖地址时，可在对应命令组上使用 `--server-url`；Registry 相关命令额外支持 `--timeout`。

## 开发环境搭建

### 1. 准备仓库

本项目通常与以下兄弟仓库一起使用：

```text
acps/
  acps-infra/
  registry-server/
  ca-server/
  discovery-server/
  acps-cli/
```

### 2. 前置条件

- `uv`（[安装文档](https://docs.astral.sh/uv/getting-started/installation/)）—— `uv` 会根据 `.python-version` 自动下载并管理 Python 3.14，无需手动安装 Python
- `just`（[官方安装文档](https://just.systems/man/en/packages.html)）
- Docker Desktop（仅用于启动 `acps-infra/dev-infra` 依赖）

补充说明：本仓库开发与测试统一使用 Python `3.14`，并通过仓库根目录 `.python-version` 固定版本请求；`just dev bootstrap` / `just test bootstrap` 会通过 `uv` 强制使用 managed Python `3.14` 创建与同步 `.venv`。

### 3. 建立开发环境

```bash
just dev bootstrap
```

`just dev bootstrap` 是本仓库的开发主路径，负责执行 `infra up postgres + prep env + prep sync + prep hooks`。

### 4. 准备配置

- 仓库根目录已提供 `acps-cli.toml` 作为本地开发默认配置
- 如需使用 Registry 登录相关命令，可执行 `just prep env` 生成 `.env`，再填写账号密码
- 具体命令树可通过 `acps-cli --help`、`acps-cli cert --help`、`acps-cli admin --help` 查看

### 5. 启动本地联调环境

acps-cli 本身是纯 CLI 工具，因此**没有 `just app` domain**。本仓库改用 `just dev bootstrap` 作为开发主路径，用来准备 CLI 自身环境；`just test bootstrap` 则对应测试环境准备。`prep` 只保留为维护与局部修复动作。

基础设施（PostgreSQL）由 `acps-infra/dev-infra/compose.yml` 提供。推荐先在 `acps-cli` 仓库中执行：

```bash
just dev bootstrap
```

如果你准备运行测试，再额外执行：

```bash
just test bootstrap
```

当前 `just dev bootstrap` 与 `just test bootstrap` 会复用同一段共享准备逻辑；两者的区别主要在于语义表达：前者面向开发联调，后者面向测试入口。

然后在**独立终端**中分别启动三个后端服务：

```bash
# 终端 1
cd ../registry-server && APP_ENV=development CA_SERVER_MOCK=false just app bootstrap && APP_ENV=development CA_SERVER_MOCK=false just app   # 端口 9001 public + 9002 mTLS/dev listener

# 终端 2
cd ../ca-server && APP_ENV=development REGISTRY_SERVER_MOCK=false just app bootstrap && APP_ENV=development REGISTRY_SERVER_MOCK=false just app         # 端口 9003

# 终端 3
cd ../discovery-server && just app bootstrap && just app         # 端口 9005
```

为了让 `just test` 的全量集成 / e2e 工作流稳定通过：

- `registry-server` 联调时建议使用 `APP_ENV=development CA_SERVER_MOCK=false`，以启用真实 CA 吊销通知链路；当前 `registry-server` 的 development 配置已关闭认证限流，避免 `acps-cli` 的 e2e / 全量测试在高频登录步骤上触发 `429 Too Many Requests`。
- `registry-server` 与 `ca-server` 需要使用同一个 `REGISTRY_SERVER_INTERNAL_API_TOKEN`，上面的示例统一使用 `local-registry-server-internal-api-token`。

三个服务启动完成后，回到 `acps-cli` 仓库执行：

```bash
just doctor
```

如果 `doctor` 失败，它会明确告诉你缺的是哪个服务，并打印对应仓库的启动命令。

本地常用地址：

| 服务             | 地址                    | 说明                             |
| ---------------- | ----------------------- | -------------------------------- |
| registry-server  | `http://localhost:9001` | acps-cli.toml `[registry]` 直连  |
| ca-server        | `http://localhost:9003` | acps-cli.toml `[ca]` 直连        |
| discovery-server | `http://localhost:9005` | acps-cli.toml `[discovery]` 直连 |

### 6. 联调示例

```bash
uv run acps-cli auth login --username alice --password 'S3cret!'
uv run acps-cli agent save --acs-file acs.json
uv run acps-cli cert status --aic <AIC>
uv run acps-cli discover query "北京旅游推荐"
```

## 测试

`acps-cli` 是四个仓库里唯一承载真实跨服务联调 e2e 的仓库。

边界约定如下：

- `registry-server`、`ca-server`、`discovery-server` 各自负责本服务的 `unit`、`integration` 和 self-contained `e2e`。
- 只要测试需要同时验证多个 sibling 服务的真实交互，就应该归到 `acps-cli/tests/e2e/`，而不是继续留在 server 仓库。
- 典型联调场景包括：ATR / EAB / 证书申请主链路、证书生命周期状态传播、discovery snapshot / incremental / webhook / runtime 协作、forwarder / fallback / 410 恢复等跨服务工作流。
- 少数明确标注为未来工作的场景允许保留 `skip`；除此之外，联调测试的目标是通过自动准备前置条件实现尽可能全绿。

| 层级       | 命令                    | 说明                                                                                |
| ---------- | ----------------------- | ----------------------------------------------------------------------------------- |
| 单元测试   | `just test unit`        | 纯 mock，无外部服务依赖                                                             |
| 集成测试   | `just test integration` | 以 CLI 自身参数、配置、输出和单服务契约验证为主；默认本地地址缺服务时由夹具自动托管 |
| 端到端测试 | `just test e2e`         | 真实跨服务联调主入口；默认本地地址缺服务时由夹具自动托管                            |
| 全量测试   | `just test`             | 运行全部测试                                                                        |

联调测试的推荐执行顺序：

1. 在 `acps-cli` 仓库执行 `just dev bootstrap`。
2. 如需运行测试，再执行 `just test bootstrap`。
3. 若要手工联调或使用自定义服务地址，在三个 sibling 服务仓库分别按上文命令启动本地联调实例。
4. 回到 `acps-cli` 执行 `just doctor`，确认三个服务都可达。
5. 若使用默认 `localhost:9001/9003/9005` 且端口空闲，可直接运行 `just test integration`、`just test e2e` 或 `just test`；测试夹具会自动托管所需 sibling 服务。

`just doctor` 的角色是对手工联调前置条件做集中检查；如果它失败，优先修复启动矩阵、端口、token 或证书问题。对默认本地地址的测试路径，`tests/_local_services.py` 会在端口空闲时按测试模式受管启动 sibling 服务，因此不再要求 `just test integration` / `just test e2e` 先显式通过 `doctor`。

当前职责划分建议：

- `just test integration`：侧重 CLI 命令面、配置解析、输出格式、单服务命令契约。
- `just test e2e`：侧重跨服务用户旅程、真实状态传播、联调拓扑协作。
- `just test`：顺序执行 CLI 的 unit / integration / e2e；默认本地地址缺服务时由测试夹具补齐。

与三个 server 仓库的对应关系：

- `registry-server` / `ca-server` / `discovery-server` 各自的 `integration` 与 `e2e` 负责本服务自闭环验证。
- `acps-cli/tests/e2e/` 负责把三个服务串起来做真实联调回归。
- 如果某个场景必须同时启动多个 sibling 服务，它应优先进入 `acps-cli/tests/e2e/`，而不是回流到 server 仓库。

当前稳定基线：

- 在 README 约定的联调启动方式下，`just test` 应能跑通 unit / integration / e2e。

当前仍允许保留 `skip` 的场景，应限于明确标注的未来工作，例如尚未实现的多实例 discovery forwarder/fallback 联调与 fanout 聚合能力；凡是由于环境未准备而触发的 `skip`，都属于待清理对象，而不是长期设计目标。

## 开发常用命令

```bash
just dev bootstrap
just doctor
just test bootstrap
just test integration
just qa
just qa type
```
