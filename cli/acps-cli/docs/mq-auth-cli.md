# acps-cli mq-auth-server 客户端功能设计

## 1. mq-auth-server API 功能分析

### 1.1 两个端口的职责

mq-auth-server 同时监听两个 mTLS 端口：

| 端口 | 服务名    | 调用方        | 职责                                                   |
| ---- | --------- | ------------- | ------------------------------------------------------ |
| 9007 | Group API | Leader Agent  | 群组 ACL 生命周期管理（添加/移除成员、删除群组、踢出） |
| 9008 | Auth API  | RabbitMQ 内部 | RabbitMQ HTTP auth backend，返回 `allow`/`deny` 决策   |

两个端口都暴露 `/health` 和 `/ready` 运维端点，均要求 mTLS。

### 1.2 Group API（9007）端点

```
PUT    /groups/{leader_aic}/{group_id}/members/{member_aic}   # 添加成员
DELETE /groups/{leader_aic}/{group_id}/members/{member_aic}   # 移除成员
DELETE /groups/{leader_aic}/{group_id}                        # 删除整个群组 ACL
DELETE /groups/{leader_aic}/{group_id}/members/{member_aic}/connection  # 断开成员连接
```

调用规则：客户端证书 CN（即 Leader 的 AIC）必须与路径中的 `leader_aic` 完全匹配，否则返回 403。

### 1.3 Auth API（9008）端点

```
POST /auth/user      # 校验用户名（AIC 格式才 allow）
POST /auth/vhost     # 校验 vhost（仅 "acps" vhost allow）
POST /auth/resource  # 校验 exchange/queue 读写权限
POST /auth/topic     # 校验 topic 路由权限
```

这四个端点由 **RabbitMQ 内部**在连接/操作时主动调用，遵循 [RabbitMQ HTTP auth backend](https://www.rabbitmq.com/docs/access-control#http-auth-backend) 协议，请求体为 `application/x-www-form-urlencoded`，响应体为纯文本 `allow`/`deny`。

### 1.4 功能定性

> **结论与用户直觉一致**：mq-auth-server 没有面向普通用户的功能。
>
> - Auth API 是 RabbitMQ 内部协议，人工干预没有意义。
> - Group API 是 Leader Agent **程序化**调用（通过 acps-sdk），人工 CLI 用途集中在运维管理和联调调试。

因此 mq-auth-server 的所有 CLI 能力均归入 `acps-cli admin mq` 子树。

---

## 2. CLI 命令设计

### 2.1 命令树

```text
acps-cli admin mq
├── health                          # 探活 Group API 和 Auth API
├── group
│   ├── add-member                  # 为指定 leader/group 添加成员
│   ├── remove-member               # 移除成员
│   ├── delete                      # 删除整个群组 ACL
│   └── kick                        # 断开成员与 RabbitMQ 的连接
└── auth-probe
    ├── user                        # 探测 /auth/user 决策
    ├── vhost                       # 探测 /auth/vhost 决策
    ├── resource                    # 探测 /auth/resource 决策
    └── topic                       # 探测 /auth/topic 决策
```

### 2.2 `admin mq health`

```bash
acps-cli --config acps-cli.toml admin mq health [--json]
```

- 分别向 Group API（9007）和 Auth API（9008）的 `/health` 发起 mTLS GET 请求
- 输出两个端口的状态及 `/ready` 结果（含 Redis 可达性）
- 客户端证书使用 `[mq].probe_cert_file` / `[mq].probe_key_file`；若未配置，回退到 `[ca].certs_dir` / `[ca].private_keys_dir` 按 AIC 自动推导路径
- `--json` 输出格式参见 §2.6

### 2.3 `admin mq group` 子命令

所有 group 子命令均使用 mTLS 向 Group API（9007）发起请求，客户端证书必须是 Leader 持有的证书（CN = leader_aic）。证书优先级：`--cert-file`/`--key-file` 命令行参数 > `[mq].group_cert_file`/`[mq].group_key_file` 配置项。

```bash
# 添加群组成员（caller cert CN 必须是 leader_aic）
acps-cli admin mq group add-member \
    --leader-aic <LEADER_AIC> \
    --group-id <GROUP_ID> \
    --member-aic <MEMBER_AIC> \
    [--cert-file <PATH>] [--key-file <PATH>] \
    [--json]

# 移除群组成员
acps-cli admin mq group remove-member \
    --leader-aic <LEADER_AIC> \
    --group-id <GROUP_ID> \
    --member-aic <MEMBER_AIC> \
    [--cert-file <PATH>] [--key-file <PATH>] \
    [--json]

# 删除整个群组 ACL（危险操作）
# - 不加 --yes：交互提示 "This will delete all ACL for group <GROUP_ID>. Proceed? [y/N]"
# - 非 TTY 环境（CI）且未加 --yes：自动取消并以 exit(1) 退出
acps-cli admin mq group delete \
    --leader-aic <LEADER_AIC> \
    --group-id <GROUP_ID> \
    [--yes] [--cert-file <PATH>] [--key-file <PATH>] \
    [--json]

# 断开指定成员与 RabbitMQ 的所有连接
# 注：该操作依赖 mq-auth-server 能够访问 RabbitMQ Management API；
#     若 RabbitMQ Management 不可达，返回 502/503（非权限问题，需区分 403 与 5xx）。
acps-cli admin mq group kick \
    --leader-aic <LEADER_AIC> \
    --group-id <GROUP_ID> \
    --member-aic <MEMBER_AIC> \
    [--cert-file <PATH>] [--key-file <PATH>] \
    [--json]
```

### 2.4 `admin mq auth-probe` 子命令

面向运维/联调人员，用于调试 RabbitMQ 授权决策。

Auth API（9008）同样设置了 `ssl.CERT_REQUIRED`，连接时**必须提供 mTLS 客户端证书**；与 Group API 的区别在于：Auth API 的授权决策只看请求体中的 `username` 等表单字段，**不读取 peer cert CN** 做业务校验，因此任何已签发的合法 ACPs 证书均可用于连接，无需专门使用 Leader 的证书。客户端证书使用 `[mq].probe_cert_file` / `[mq].probe_key_file`；若未配置，回退到 `[ca].certs_dir` / `[ca].private_keys_dir`。各子命令支持 `--cert-file` / `--key-file` 命令行覆盖。

```bash
# 探测 /auth/user
acps-cli admin mq auth-probe user --username <AIC_OR_NAME> [--json]

# 探测 /auth/vhost
acps-cli admin mq auth-probe vhost --username <AIC> --vhost <VHOST> [--json]

# 探测 /auth/resource
acps-cli admin mq auth-probe resource \
    --username <AIC> --vhost <VHOST> \
    --resource <exchange|queue> --name <NAME> \
    --permission <configure|write|read> \
    [--json]

# 探测 /auth/topic
acps-cli admin mq auth-probe topic \
    --username <AIC> --vhost <VHOST> \
    --resource <topic> --name <NAME> \
    --permission <write|read> \
    --routing-key <ROUTING_KEY> \
    [--json]
```

### 2.5 代码模块位置

```text
acps_cli/
└── mq/
    ├── __init__.py
    ├── unified.py          # 顶层 admin_mq_group（Click Group 入口）
    ├── client.py           # MqAuthClient：封装 httpx mTLS 请求
    ├── config.py           # MqConfig 数据类，从 RootCliRuntime.toml_data["mq"] 加载
    ├── group_cmd.py        # group 子命令实现
    └── auth_probe_cmd.py   # auth-probe 子命令实现
```

**`main.py` 注册路径**：

```python
# acps_cli/main.py — 顶部导入（与其他模块并列）
from acps_cli.mq.unified import admin_mq_group

# _build_admin_group() 的 children 列表中添加：
def _build_admin_group() -> click.Group:
    return _build_group(
        "admin",
        "Administrative and control-plane commands.",
        [
            admin_auth_group,
            admin_registry_group,
            admin_ca_group,
            admin_discovery_group,
            admin_mq_group,       # ← 新增
        ],
    )
```

### 2.6 `--json` 输出 schema

`--json` 标志将人类可读输出替换为结构化 JSON，便于脚本处理。操作成功以 exit 0 退出，错误以非零 exit code 退出。

| 命令                  | 成功时 `--json` 输出结构                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `health`              | `{"group_api": {"status": "ok"\|"error", "detail": "..."}, "auth_api": {"status": "ok"\|"error", "detail": "..."}}` |
| `group add-member`    | `{"status": "ok", "leader_aic": "...", "group_id": "...", "member_aic": "..."}`                                     |
| `group remove-member` | `{"status": "ok", "leader_aic": "...", "group_id": "...", "member_aic": "..."}`                                     |
| `group delete`        | `{"status": "ok", "leader_aic": "...", "group_id": "..."}`                                                          |
| `group kick`          | `{"status": "ok", "leader_aic": "...", "group_id": "...", "member_aic": "..."}`                                     |
| `auth-probe user`     | `{"result": "allow"\|"deny", "username": "..."}`                                                                    |
| `auth-probe vhost`    | `{"result": "allow"\|"deny", "username": "...", "vhost": "..."}`                                                    |
| `auth-probe resource` | `{"result": "allow"\|"deny", "username": "...", "resource": "...", "name": "...", "permission": "..."}`             |
| `auth-probe topic`    | `{"result": "allow"\|"deny", "username": "...", "resource": "...", "name": "...", "routing_key": "..."}`            |

错误时统一输出 `{"status": "error", "message": "..."}` 并以非零 exit code 退出。

---

## 3. 配置设计

在 `acps-cli.toml` 增加 `[mq]` 节：

```toml
[mq]
# Group API 地址（mTLS，端口 9007）
group_api_url = "https://localhost:9007"
# Auth API 地址（mTLS，端口 9008）
auth_api_url = "https://localhost:9008"

# group 子命令：Leader 专属客户端证书（CN 必须等于路径中的 leader_aic）
# 若留空，group 子命令必须通过 --cert-file / --key-file 显式指定
# group_cert_file = "./keyfiles/certs/<LEADER_AIC>.crt"
# group_key_file  = "./keyfiles/private/<LEADER_AIC>.key"

# health / auth-probe 探测用客户端证书（任意合法 ACPs 证书均可）
# 若留空，回退到 [ca].certs_dir / [ca].private_keys_dir 按 AIC 自动推导路径
# probe_cert_file = "./keyfiles/certs/<ANY_AIC>.crt"
# probe_key_file  = "./keyfiles/private/<ANY_AIC>.key"

# 校验 mq-auth-server 服务端证书的 CA 文件
# ca_cert_file = "./certs/acps-root-ca.pem"

# 请求超时（秒，默认 10）
# timeout_seconds = 10
```

对应的 Python 数据类（`acps_cli/mq/config.py`）：

```python
@dataclass(frozen=True)
class MqConfig:
    group_api_url: str
    auth_api_url: str
    group_cert_file: str | None  # group 命令 Leader 证书
    group_key_file: str | None
    probe_cert_file: str | None  # health / auth-probe 证书
    probe_key_file: str | None
    ca_cert_file: str | None
    timeout_seconds: int

    @classmethod
    def from_toml(cls, data: dict[str, Any], config_dir: Path | None) -> "MqConfig":
        """从 toml_data["mq"] 加载；相对路径相对于 config_dir 解析。"""
        ...
```

---

## 4. mq-auth-server 证书预置流程

mq-auth-server 启动需要服务端 mTLS 证书。证书通过与 registry-server / ca-server 的标准 ATR 流程签发，与 registry-server 9002 端口的证书预置路径完全对称。

### 4.1 ACS 文件位置

mq-auth-server 项目内已准备好 ACS 文件：

```
mq-auth-server/app/acs/mq-auth-server-acs.json
```

该文件声明：

- `name`: `ACPs MQ Auth Service`
- `securitySchemes`: `mtls`（mutualTLS）
- `endPoints`: 空（auth backend 不对外声明 AIP 端点）
- `certificate.altNames.dns`: `["mq-auth-server", "localhost", "host.docker.internal"]`
- `certificate.requestedValidity`: 1825 天（5 年）

### 4.2 预置步骤（管理员操作）

```bash
# 1. admin 登录 registry-server
acps-cli --config admin.toml auth login --username admin --password <PASS>

# 2. 提交 mq-auth-server ACS 草稿
acps-cli --config admin.toml agent save \
    --acs-file path/to/mq-auth-server-acs.json

# 3. admin 审批该 agent
AGENT_ID=$(acps-cli --config admin.toml admin registry agent list --json \
    | jq -r '.items[] | select(.name=="ACPs MQ Auth Service") | .id')
acps-cli --config admin.toml admin registry agent approve --agent-id $AGENT_ID

# 4. 获取 AIC（审批后由 registry 写入 ACS）
acps-cli --config admin.toml agent sync \
    --acs-file path/to/mq-auth-server-acs.json
MQ_AUTH_AIC=$(jq -r .aic path/to/mq-auth-server-acs.json)

# 5. 获取 EAB 凭证
acps-cli --config admin.toml cert eab fetch \
    --aic $MQ_AUTH_AIC \
    --output keyfiles/private/mq-auth-eab.json

# 6. 签发服务器证书（usage = serverAuth）
acps-cli --config ca.toml cert issue \
    --aic $MQ_AUTH_AIC \
    --eab-file keyfiles/private/mq-auth-eab.json \
    --usage serverAuth

# 7. 将生成的证书和私钥配置到 mq-auth-server 的 TLS_CERT_FILE / TLS_KEY_FILE 环境变量
#    然后启动 mq-auth-server，Group API（9007）和 Auth API（9008）即可提供 mTLS 服务
```

### 4.3 与 registry-server 9002 的对比

| 维度           | registry-server 9002 mTLS       | mq-auth-server 9007/9008 mTLS     |
| -------------- | ------------------------------- | --------------------------------- |
| 证书用途       | serverAuth + clientAuth         | serverAuth（服务端证书）          |
| 调用方证书要求 | 本体客户端证书（clientAuth）    | Leader Agent 证书（clientAuth）   |
| ACS 文件位置   | 内嵌在项目中                    | `app/acs/mq-auth-server-acs.json` |
| CLI 签发命令   | `cert issue --usage clientAuth` | `cert issue --usage serverAuth`   |

---

## 5. 测试边界设计

### 5.1 边界原则

| 测试层级        | 归属项目       | 测试内容                                                                           | mTLS 要求                                                           |
| --------------- | -------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| unit            | mq-auth-server | 路由、服务逻辑、校验工具函数（全部 mock/fake，无外部依赖）                         | 无                                                                  |
| integration     | mq-auth-server | 真实 Redis 的 GroupAclService；mock HTTP 的 RabbitMqManagementClient               | 无（HTTP）                                                          |
| e2e             | mq-auth-server | 调用已部署实例；cert 通过环境变量外部注入，自动 skip 若未配置                      | 是（env var）                                                       |
| **integration** | **acps-cli**   | 使用 CLI 命令操作预置了 cert 的运行中 mq-auth-server；验证 group CRUD + auth-probe | **是**（group 命令用 Leader cert；auth-probe 用任意合法 ACPs cert） |
| **e2e**         | **acps-cli**   | 完整生命周期：从 ACS 注册 → 审批 → 签发证书 → 启动服务 → 群组操作 → 健康检查       | **是**                                                              |

### 5.2 acps-cli integration 测试设计

**mq-auth-server 实例启动策略（两条路径）**：

| 路径         | 触发条件                                                | 行为                                                                                                                                   |
| ------------ | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 快速路径     | `MQ_GROUP_API_URL` / `MQ_AUTH_API_URL` 已配置且服务可达 | 直接使用已有实例，跳过证书签发与子进程管理                                                                                             |
| 自动托管路径 | 环境变量未配置                                          | fixture 用 Root CA 签发服务端证书后启动 mq-auth-server 子进程（参考 `_local_services.py` 中 `_ensure_registry_mtls_artifacts` 的模式） |

自动托管路径需要：Redis 本地 6379 可达；`../mq-auth-server` 已安装依赖。

**前提条件**（满足其一即可）：

- 外部提供：`MQ_GROUP_API_URL` + `MQ_AUTH_API_URL` + `MQ_SERVER_CERT_FILE` 均已配置
- 本地托管：registry-server / ca-server 可达；Redis 本地可达

**测试文件**：`tests/integration/test_mq_auth_group_workflow.py`

```python
# 测试场景（均通过 Click runner 调用 acps-cli）
class TestMqGroupWorkflow:
    def test_health_returns_ok_for_both_ports(...)
    def test_add_member_returns_success(...)
    def test_remove_member_returns_success(...)
    def test_delete_group_returns_success(...)
    def test_kick_member_closes_connection(...)
    def test_group_member_denied_when_not_in_acl(...)

class TestMqAuthProbe:
    def test_probe_user_allow_for_valid_aic(...)
    def test_probe_user_deny_for_invalid_username(...)
    def test_probe_vhost_allow_for_acps_vhost(...)
    def test_probe_resource_allow_for_inbox_queue(...)
```

**conftest 中的关键 fixture**：

```python
@pytest.fixture(scope="session")
def mq_server_certs(work_dir: Path) -> MqServerCerts:
    """
    mq-auth-server 服务端证书（用于启动本地托管实例）。
    优先从 MQ_SERVER_CERT_FILE / MQ_SERVER_KEY_FILE / MQ_CA_FILE 环境变量读取；
    若未配置，用 Root CA（ca-server/certs/root-ca.crt+key）直接签发 localhost 服务端证书
    （与 _ensure_registry_mtls_artifacts 的实现模式相同）。
    """
    ...

@pytest.fixture(scope="session")
def leader_client_cert(work_dir: Path) -> LeaderClientCerts:
    """
    调用 Group API 所需的 Leader 客户端证书（CN = leader_aic）。
    优先从 MQ_LEADER_CERT_FILE / MQ_LEADER_KEY_FILE 读取；
    若未配置，通过完整 ATR 流程（_complete_ontology_certificate_flow）
    为测试专用 Leader Agent 签发 clientAuth 证书。
    """
    ...

@pytest.fixture(scope="session")
def mq_integration_conf(tmp_path_factory, mq_server_certs, leader_client_cert) -> Path:
    """
    生成包含 [mq] 节的测试专用 acps-cli.toml：
    - group_cert_file / group_key_file  → Leader 客户端证书
    - probe_cert_file / probe_key_file  → 可复用 Leader 证书（CN 无约束）
    - ca_cert_file                      → Root CA
    """
    ...
```

### 5.3 acps-cli e2e 测试设计

**测试文件**：`tests/e2e/test_mq_auth_provision_workflow.py`

完整端到端流程，不依赖外部预置证书：

```
Phase 1 — 证书预置（借用已有 ATR 基础设施）
  1. admin 登录 registry-server
  2. 提交 mq-auth-server ACS（动态生成 AIC）
  3. admin 审批
  4. 获取 EAB
  5. 通过 ca-cli cert issue --usage serverAuth 签发服务端证书

Phase 2 — 启动 mq-auth-server（子进程）
  6. 用签发的证书启动 mq-auth-server（与 local_services 模式对称）
  7. 等待 /health 就绪

Phase 3 — Group API 验证
  8. 用 Leader 的 clientAuth 证书调用 admin mq group add-member
  9. 验证 add → remove 生命周期
  10. 验证 delete group 清理

Phase 4 — Auth probe 验证
  11. admin mq auth-probe user → allow（合法 AIC）
  12. admin mq auth-probe user → deny（非 AIC）
  13. admin mq auth-probe vhost → allow（acps vhost）

Phase 5 — 清理
  14. 停止 mq-auth-server 子进程
  15. 删除测试 Agent（通过 admin registry）
```

**测试函数命名规范**：

```python
class TestMqAuthProvisionWorkflow:
    def test_phase1_provision_server_cert(...)
    def test_phase2_service_starts_and_health_ok(...)
    def test_phase3_group_crud_lifecycle(...)
    def test_phase4_auth_probe_decisions(...)
```

### 5.4 mq-auth-server 项目自身 e2e 测试与 acps-cli e2e 测试的边界

mq-auth-server 项目自身的 e2e 测试（`mq-auth-server/tests/e2e/`）：

- 假设证书已由外部环境提供（通过 `TLS_CERT_FILE` 等环境变量）
- 专注验证单个服务实例的接口行为（类型安全、ACL 校验、决策逻辑）
- **不覆盖** 证书预置流程

acps-cli 的 e2e 测试：

- 拥有完整的证书预置流程（Phase 1–2）
- 验证 CLI 工具与多个真实服务的联调正确性
- 覆盖 CLI 输出格式、错误处理、`--json` 输出结构

两者互补，不重复。

---

## 6. 环境变量与测试参数

### 6.1 acps-cli integration 测试

| 环境变量              | 用途                                                 | 默认值                      |
| --------------------- | ---------------------------------------------------- | --------------------------- |
| `MQ_GROUP_API_URL`    | Group API 地址                                       | `https://localhost:9007`    |
| `MQ_AUTH_API_URL`     | Auth API 地址                                        | `https://localhost:9008`    |
| `MQ_SERVER_CERT_FILE` | mq-auth-server 服务端证书（跳过自动签发）            | 空（触发自动签发）          |
| `MQ_SERVER_KEY_FILE`  | mq-auth-server 服务端私钥                            | 空                          |
| `MQ_CA_FILE`          | CA 证书（校验服务端 + 签发测试证书 trust anchor）    | 空（使用 Root CA 文件路径） |
| `MQ_LEADER_CERT_FILE` | Leader 客户端证书（group 命令用，CN = Leader AIC）   | 空（触发 ATR 自动签发）     |
| `MQ_LEADER_KEY_FILE`  | Leader 客户端证书私钥                                | 空                          |
| `MQ_PROBE_CERT_FILE`  | auth-probe / health 探测用证书（任意合法 ACPs cert） | 空（复用 Leader 证书）      |
| `MQ_PROBE_KEY_FILE`   | auth-probe / health 探测用私钥                       | 空                          |

### 6.2 acps-cli e2e 测试（已有变量复用）

e2e 测试复用 `conftest.py` 中的 `REGISTRY_URL`、`CA_URL`，以及 `make_acs_file()` 动态生成 ACS。mq-auth-server 子进程由测试 fixture 自动管理，无需手动配置端口。

---

## 7. 实施优先级

| 步骤 | 内容                                           | 前置条件                                     |
| ---- | ---------------------------------------------- | -------------------------------------------- |
| 1    | `acps_cli/mq/` 模块骨架 + `admin mq health`    | 无                                           |
| 2    | `admin mq group` 四个子命令                    | 步骤 1                                       |
| 3    | `admin mq auth-probe` 四个子命令               | 步骤 1                                       |
| 4    | `[mq]` 配置节支持                              | 步骤 1                                       |
| 5    | acps-cli integration 测试                      | 步骤 2–4，mq-auth-server 本地可启动          |
| 6    | acps-cli e2e 测试（provision + group + probe） | 步骤 2–4，registry-server + ca-server 可联调 |
| 7    | `docs/unified-cli-design.md` 更新命令树        | 步骤 1–3                                     |
| 8    | `docs/joint-e2e-execution-ledger.md` 补充台账  | 步骤 5–6 验收通过后                          |
