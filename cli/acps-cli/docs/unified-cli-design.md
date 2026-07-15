# acps-cli 统一命令初步设计

## 设计目标

这次整合的目标不是简单把 `reg-cli`、`reg-admin-cli`、`ca-cli`、`disco-cli` 四个入口拼到一起，而是把命令空间按“用户实际工作流”和“权限边界”重新组织。

目标如下：

- 提供单一入口：统一为 `acps-cli`
- 普通用户命令保持短路径：常见操作不要求先输入 `user`
- 管理员和联调/测试控制面命令集中收敛：统一进入 `admin` 子命令
- 尽量保留现有配置结构：继续复用 `[registry]`、`[ca]`、`[discovery]`
- 统一 `base_url` 语义：始终表示服务根地址，再由 CLI 派生业务前缀
- 允许一次性切换：代码、文档、测试和脚本统一改为新命令树，不保留中间兼容形态

## 核心设计原则

### 1. 普通用户路径优先

普通用户最常做的是：登录、提交 ACS、获取 EAB、申请证书、做 discovery 查询。这些命令应该直接位于 `acps-cli` 根命令之下，避免路径过深。

### 2. 特权命令统一收口到 `admin`

审批、禁用/启用、CRL 刷新、DSP 控制面操作，本质上都不是普通用户日常行为。它们不应散落在多个顶级入口里，而应统一进入：

```text
acps-cli admin ...
```

这样做的价值有两个：

- 从帮助信息上就能明确区分“普通用户能力”和“受限能力”
- 后续要为 `admin` 子命令增加额外鉴权、确认提示、危险操作保护时，边界会很清晰

### 3. 对用户暴露“任务语义”，对管理员保留“领域语义”

普通用户更关心的是“Agent 管理”“证书管理”“查询 discovery”，而不是背后落到 Registry 还是 CA。

管理员/联调人员则更需要知道控制的是哪个服务，所以 `admin` 子命令内仍按 `registry`、`ca`、`discovery` 划分。

### 4. 内部测试用途的命令仍归入 `admin`

像 `hard-reset`、`register-webhook` 这类更偏 e2e / 联调使用的命令，仍然进入 `admin` 树，但不需要再额外抽象出一层 `internal` 命令空间或权限级别。

- 它们在命令树里仍然就是 `admin ...`
- 在 help 和文档里可以补充 `test-only`、`dangerous` 一类提示
- 对危险操作继续保留 `--yes` 或环境变量开关一类额外保护

这样更直接：CLI 对外只表达两层语义，普通用户看根命令，管理员看 `admin` 子树；至于某些 `admin` 命令更偏联调或危险运维，则只作为文案标签处理，而不再影响命令可见性模型。

## 建议的新命令树

建议把新的 `acps-cli` 组织为下面这棵树：

```text
acps-cli
├── auth
│   ├── login
│   └── whoami
├── agent
│   ├── list
│   ├── save
│   ├── submit
│   ├── check
│   ├── sync
│   └── delete
├── entity
│   └── derive
├── cert
│   ├── eab
│   │   └── fetch
│   ├── issue
│   ├── renew
│   ├── revoke
│   ├── status
│   ├── account-key
│   │   └── rollover
│   ├── trust-bundle
│   │   └── update
│   ├── crl
│   │   ├── download
│   │   ├── info
│   │   └── detail
│   └── ocsp
│       ├── check
│       └── cert-status
├── discover
│   ├── status
│   └── query
└── admin
    ├── auth
    │   ├── login
    │   └── whoami
    ├── registry
    │   ├── review
    │   │   ├── list
    │   │   ├── approve
    │   │   └── reject
    │   └── agent
    │       ├── disable
    │       └── enable
    ├── ca
    │   ├── crl
    │   │   ├── list
    │   │   └── refresh
    │   └── ocsp
    │       ├── responder-info
    │       └── stats
    ├── discovery
    │   ├── run-sync
    │   └── dsp
    │       ├── status
    │       ├── registry-info
    │       ├── sync
    │       ├── start
    │       ├── stop
    │       ├── reset
    │       ├── hard-reset
    │       └── register-webhook
    └── mq
        ├── health
        ├── group
        │   ├── add-member
        │   ├── remove-member
        │   ├── delete
        │   └── kick
        └── auth-probe
            ├── user
            ├── vhost
            ├── resource
            └── topic
```

## 命令分配说明

### 一、普通用户命令

#### `auth`

面向 Registry 普通用户账号。

- `auth login`：原 `reg-cli login`
- `auth whoami`：原 `reg-cli whoami`

说明：虽然登录底层仍然调用 Registry，但用户心智更接近“账号登录”，不必再暴露 `reg-*` 前缀。

#### `agent`

面向普通用户的 Agent 生命周期管理。

- `agent list`：原 `reg-cli list`
- `agent save`：原 `reg-cli upsert`
- `agent submit`：原 `reg-cli submit`
- `agent check`：原 `reg-cli check`
- `agent sync`：原 `reg-cli sync-acs`
- `agent delete`：原 `reg-cli delete`

这里建议在最终 CLI 帮助中直接写成：`agent save`：创建或更新 Agent 草稿；已提交审核的 Agent 不允许再次保存。这样能和 `agent submit` 形成清晰分工：前者负责草稿阶段的创建或更新，后者负责提交审核的状态流转。

同时，`sync-acs` 改名为 `sync`，语义更贴近“把服务端状态同步回本地 ACS”，也避免把内部文件名暴露到命令名里。

建议把 CLI 可执行约束直接收敛为下面这张状态表：

| 状态        | `agent save` | `agent submit` | `agent check` | `agent sync` | `agent delete` | 说明                                 |
| ----------- | ------------ | -------------- | ------------- | ------------ | -------------- | ------------------------------------ |
| `draft`     | 允许         | 允许           | 允许          | 允许         | 允许           | 草稿态，可继续编辑或删除             |
| `submitted` | 禁止         | 禁止           | 允许          | 允许         | 禁止           | 已提交审核，等待管理员处理           |
| `approved`  | 禁止         | 禁止           | 允许          | 允许         | 禁止           | 已审核通过，普通用户只读             |
| `rejected`  | 允许         | 允许           | 允许          | 允许         | 允许           | 已驳回，允许修改后重新提交           |
| `disabled`  | 禁止         | 禁止           | 允许          | 允许         | 禁止           | 已被管理员禁用，只允许查看和同步状态 |

管理员动作也应与上表配套固定下来：

- `admin registry review approve` / `reject` 仅作用于 `submitted`
- `admin registry agent disable` 仅作用于 `approved`
- `admin registry agent enable` 仅作用于 `disabled`

#### `entity`

面向已拥有本体 AIC 的用户。

- `entity derive`：原 `reg-cli register-entity`

说明：这里建议直接使用 `derive`，明确表达“从本体 AIC 派生实体”的领域概念。为了避免只看命令名时误以为它只是本地生成动作，帮助文案和文档应统一写成“派生并注册实体”，明确它仍然会调用 Registry 的 mTLS entity 注册接口执行远端写操作。

#### `cert`

面向证书生命周期管理。

- `cert eab fetch`：原 `reg-cli get-eab`
- `cert issue`：原 `ca-cli new-cert`
- `cert renew`：原 `ca-cli renew-cert`
- `cert revoke`：原 `ca-cli revoke-cert`
- `cert status`：原 `ca-cli status`
- `cert account-key rollover`：原 `ca-cli key-rollover`
- `cert trust-bundle update`：原 `ca-cli update-trust-bundle`
- `cert crl download`：原 `ca-cli download-crl`
- `cert crl info`：原 `ca-cli crl-info`
- `cert crl detail`：原 `ca-cli crl-detail`
- `cert ocsp check`：原 `ca-cli check-ocsp`
- `cert ocsp cert-status`：原 `ca-cli ocsp-cert-status`

这里有一个刻意的调整：`get-eab` 不再放在 Registry 命令下，而是放在 `cert eab fetch`。因为对使用者来说，这个动作是“申请证书前的准备步骤”，放在证书域更顺手。

`crl-detail` 在这版设计里下放到普通 `cert crl detail`。原因是它本质上是只读诊断能力，不是控制面动作；而且管理员身份在 CLI 设计上应视为能力超集，因此即便命令位于普通路径下，管理员在服务端鉴权允许的前提下仍然可以直接使用它进行排障。

#### `discover`

只保留普通用户或普通联调可理解的“只读/业务查询”能力。

- `discover status`：原 `disco-cli status`
- `discover query`：原 `disco-cli query`

## 二、管理员命令

### `admin auth`

管理员单独登录、单独查看身份。

- `admin auth login`：原 `reg-admin-cli login`
- `admin auth whoami`：新增，对应管理员 token 上下文

说明：管理员必须与普通用户使用独立 token 文件，避免上下文串用。

### `admin registry`

Registry 审核和开关控制面集中在这里。

- `admin registry review list`：原 `reg-admin-cli list`
- `admin registry review approve`：原 `reg-admin-cli approve`
- `admin registry review reject`：原 `reg-admin-cli reject`
- `admin registry agent disable`：原 `reg-admin-cli disable`
- `admin registry agent enable`：原 `reg-admin-cli enable`

这里建议显式引入 `review` 层级，把“审核”和“Agent 生命周期状态控制”拆开，避免所有动作都堆在 `registry` 下面。

### `admin ca`

CA 的管理员可见能力集中在这里。

- `admin ca crl refresh`：原 `ca-cli refresh-crl`
- `admin ca crl list`：原 `ca-cli crl-list`
- `admin ca ocsp responder-info`：原 `ca-cli ocsp-responder-info`
- `admin ca ocsp stats`：原 `ca-cli ocsp-stats`

说明：

- `download-crl` 和 `crl-info` 仍保留在普通用户侧，因为它们更偏只读消费
- `crl-detail` 也下放到普通 `cert crl detail`，因为它同样是只读排障能力
- `refresh-crl`、`crl-list`、`ocsp-stats` 明显更偏管理和诊断，应进入 `admin`
- `ocsp responder-info` 保留在 `admin`，因为它更接近服务端运行态诊断信息

### `admin discovery`

Discovery 与 DSP 的控制面统一进入 `admin`。

- `admin discovery run-sync`：面向联调的一次完整同步入口
- `admin discovery dsp status`：原 `disco-cli dsp status`
- `admin discovery dsp registry-info`：原 `disco-cli dsp registry-info`
- `admin discovery dsp sync`：原 `disco-cli dsp sync`
- `admin discovery dsp start`：原 `disco-cli dsp start`
- `admin discovery dsp stop`：原 `disco-cli dsp stop`
- `admin discovery dsp reset`：原 `disco-cli dsp reset`
- `admin discovery dsp hard-reset`：原 `disco-cli dsp hard-reset`
- `admin discovery dsp register-webhook`：原 `disco-cli dsp register-webhook`

这里建议保留两层抽象：

- `admin discovery run-sync`：给日常联调和脚本使用，继续提供“一次完整同步”的便捷入口
- `admin discovery dsp ...`：给需要精细控制 DSP 状态机的管理员和 e2e 场景使用

建议把 `run-sync` 的职责边界写死为一个“单次同步编排器”，行为固定为：

1. 先读取 `admin discovery dsp status` 与 `admin discovery dsp registry-info` 做前置检查。
2. 前置检查通过后，触发一次 `admin discovery dsp sync`。
3. 轮询 `admin discovery dsp status`，直到进入成功、失败或超时终态。
4. 输出本次同步摘要，并按结果返回退出码。

同时明确下面这些约束，避免 `run-sync` 与底层 DSP 命令职责重叠：

- `run-sync` 不隐式执行 `dsp start`、`dsp stop`、`dsp reset`、`dsp hard-reset`、`dsp register-webhook`
- 如果 DSP 当前不具备可同步前置条件，`run-sync` 直接失败，并提示使用明确的 `dsp` 子命令处理
- `run-sync` 只承担“一次同步”的编排职责，不承担破坏性修复或长期运维控制面职责

### `admin mq`

面向 mq-auth-server 的运维与授权诊断控制面（Group API 端口 9007，Auth API 端口 9008）。

mTLS 证书分两类：

- **group_cert_file / group_key_file**：Leader 专属证书，CN 须等于 `leader_aic`；`group` 子命令使用。
- **probe_cert_file / probe_key_file**：任意合法 ACPs 证书；`health` 和 `auth-probe` 子命令使用。

#### health

- `admin mq health`：探测 Group API 和 Auth API 是否就绪；`--json` 返回 `{"group_api": {"status": "ok"|"error", "detail": "..."}, "auth_api": {...}}`。
- 两端口独立探测，一个不可达不影响另一个的报告。
- `--cert-file` / `--key-file` 命令行覆盖 toml 中的 `probe_cert_file`。

#### group

- `admin mq group add-member --leader-aic AIC --group-id GID --member-aic AIC`：PUT `/groups/{leader}/{gid}/members/{member}`；幂等，重复添加不报错。
- `admin mq group remove-member --leader-aic AIC --group-id GID --member-aic AIC`：DELETE `/groups/{leader}/{gid}/members/{member}`。
- `admin mq group delete --leader-aic AIC --group-id GID [--yes]`：DELETE `/groups/{leader}/{gid}`；无 `--yes` + 非 TTY → exit 1；TTY → 交互确认。
- `admin mq group kick --leader-aic AIC --group-id GID --member-aic AIC`：DELETE `.../connection`，断开 AMQP 长连接；502/503 → 提示 RabbitMQ Management API 不可达。

#### auth-probe

- `admin mq auth-probe user --username AIC`：POST `/auth/user`，验证用户身份；响应 `allow`/`deny`。
- `admin mq auth-probe vhost --username AIC --vhost VHOST`：POST `/auth/vhost`，验证 vhost 访问权限。
- `admin mq auth-probe resource --username AIC --vhost VHOST --resource exchange|queue --name NAME --permission configure|write|read`：POST `/auth/resource`。
- `admin mq auth-probe topic --username AIC --vhost VHOST --name EXCHANGE --permission write|read --routing-key KEY`：POST `/auth/topic`。
- 所有 auth-probe 请求体均为 `application/x-www-form-urlencoded`（RabbitMQ HTTP auth backend 协议要求）。
- `--json` 输出：`{"result": "allow"|"deny", ...extra_fields}`。

## 权限分层建议

建议在设计文档和 help 中明确标记两种级别：

| 级别    | 含义               | 示例                                                                                      |
| ------- | ------------------ | ----------------------------------------------------------------------------------------- |
| `user`  | 普通用户日常命令   | `agent save`、`cert issue`、`discover query`                                              |
| `admin` | 管理员或运维控制面 | `admin registry review approve`、`admin ca crl refresh`、`admin discovery dsp hard-reset` |

建议规则：

- `admin` 命令默认显示在帮助中
- 更偏联调或危险运维的 `admin` 命令，可以在 help 中补充 `[test-only]`、`[dangerous]` 之类标签
- 对 `hard-reset`、`refresh` 一类命令增加二次确认或 `--yes`
- 命令树表达的是“最小预期权限”，不是客户端硬编码隔离；管理员身份在服务端鉴权允许的前提下，仍可执行普通用户路径下的命令

## 配置与凭证设计

为保持配置模型清晰，建议保留现有服务配置 section：

这里建议顺带收敛一个历史问题：`base_url` 永远表示“服务基准地址”，也就是它下面应直接存在 `/health`、`/ready`、`/metrics` 这一类服务级端点，而不是直接携带 `/api/v1` 这样的业务前缀。

对应地，业务基础地址由 CLI 内部派生，或在少数需要时允许显式覆盖；但 `registry.mtls_base_url` 例外，它应被视为独立的一等配置项，不从 `registry.base_url` 推导。

- `registry.base_url` -> `registry_api_base_url = {base_url}/api/v1`
- `registry.base_url` -> `registry_atr_base_url = {base_url}/acps-atr-v2`
- `registry.mtls_base_url`：独立配置，用于本体证书 mTLS listener，不做推导
- `ca.base_url` -> `ca_atr_base_url = {base_url}/acps-atr-v2`
- `discovery.base_url` 直接作为 `/health`、`/query`、`/admin/dsp/*` 等路径的共同根地址

这样区分的原因是：`api_base_url` 和 `atr_base_url` 只是同一服务根地址下的业务前缀，而 `mtls_base_url` 对应的是另一条 listener / trust plane，它可能同时改变 scheme、host、port 和证书校验材料，语义上不应退化为一个“默认推导值”。

这意味着 `registry` 配置需要做一次附带改造：当前历史上的“把 `/api/v1` 直接塞进 `server_base_url`”应收敛为“服务根地址 + 派生 `registry_api_base_url`”；同时把 `mtls_base_url` 明确保留为独立配置项。

```toml
[registry]
base_url = "http://localhost:9001"
# api_base_url = "http://localhost:9001/api/v1"
# atr_base_url = "http://localhost:9001/acps-atr-v2"
mtls_base_url = "http://localhost:9002"
# production example: https://registry.example.com:9002

[ca]
base_url = "http://localhost:9003"

[discovery]
base_url = "http://localhost:9005"
```

主要调整点在“凭证与 token 存放方式”：

```toml
[auth]
user_token_file = "./.acps-cli/tokens/registry-user.json"
admin_token_file = "./.acps-cli/tokens/registry-admin.json"
```

### 认证矩阵

为了避免“`admin auth login` 是否能顺带登录所有 `admin` 子树”这种歧义，建议把默认认证上下文明确成下面这张表：

| 命令域            | 默认身份上下文                | 默认凭证来源                                                                 | 说明                                              |
| ----------------- | ----------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------- |
| `auth`            | Registry 普通用户             | `[auth].user_token_file`                                                     | `auth login` 写入，`auth whoami` 读取             |
| `agent`           | Registry 普通用户             | `[auth].user_token_file`                                                     | 普通用户 Agent 管理默认复用 Registry user token   |
| `entity`          | Registry 普通用户 + mTLS 材料 | `[auth].user_token_file` + `[registry].mtls_base_url` + 命令参数中的证书材料 | `entity derive` 仍是 Registry mTLS 写操作         |
| `cert eab fetch`  | Registry 普通用户             | `[auth].user_token_file`                                                     | EAB 仍由 Registry 发放                            |
| `cert` 其余命令   | CA 服务上下文                 | `[ca]` 下的 account key / mTLS / service credentials                         | 不继承 `auth login` 或 `admin auth login`         |
| `discover`        | Discovery 服务上下文          | `[discovery]` 下的 service credentials                                       | 不继承 Registry token                             |
| `admin auth`      | Registry 管理员               | `[auth].admin_token_file`                                                    | `admin auth login` 写入，`admin auth whoami` 读取 |
| `admin registry`  | Registry 管理员               | `[auth].admin_token_file`                                                    | Registry 管理面默认读取管理员 token               |
| `admin ca`        | CA 管理上下文                 | `[ca]` 下的 admin/service credentials                                        | 与 Registry admin token 相互独立                  |
| `admin discovery` | Discovery 管理上下文          | `[discovery]` 下的 admin/service credentials                                 | 与 Registry admin token 相互独立                  |

建议：

- 普通用户 token 与管理员 token 必须物理分离
- `auth login` 默认写 `user_token_file`
- `admin auth login` 默认写 `admin_token_file`
- `admin auth login` 只建立 Registry 管理员上下文，不应隐式影响 `admin ca` 或 `admin discovery`
- `cert` 与 `discover` 命令不引入新的登录体系，继续沿用服务级配置与 Bearer token / mTLS 材料
- Bearer token 覆盖参数建议统一为 `--token-file`；mTLS 材料覆盖参数建议统一为 `--cert-file` / `--key-file`
- 配置字段层面优先统一为 `[service].base_url`；`registry.mtls_base_url` 作为独立高级配置保留，其他派生地址则尽量收敛为内部属性或少量高级覆盖项

### 配置优先级

建议把配置解析顺序明确为：

1. 命令行显式参数
2. 环境变量
3. TOML 配置中的新键
4. 由 `base_url` 派生出的默认业务地址

同时把下面几条规则写死，避免实现阶段再次发散：

- 运行时不再兼容旧 `server_base_url`、`REGISTRY_API_BASE_URL` 一类历史键；仓库内脚本、测试和文档一次性切换到新键
- 当同时提供显式业务地址和 `base_url` 时，显式业务地址优先，CLI 不再回推或混合计算
- `registry.mtls_base_url` 始终要求显式配置，不参与 `base_url` 派生
- 配置加载器遇到旧键时直接报错，并给出迁移到新键的明确提示

## 旧命令到新命令映射

| 旧命令                           | 新命令                                          | 级别    |
| -------------------------------- | ----------------------------------------------- | ------- |
| `reg-cli login`                  | `acps-cli auth login`                           | `user`  |
| `reg-cli whoami`                 | `acps-cli auth whoami`                          | `user`  |
| `reg-cli list`                   | `acps-cli agent list`                           | `user`  |
| `reg-cli upsert`                 | `acps-cli agent save`                           | `user`  |
| `reg-cli submit`                 | `acps-cli agent submit`                         | `user`  |
| `reg-cli check`                  | `acps-cli agent check`                          | `user`  |
| `reg-cli sync-acs`               | `acps-cli agent sync`                           | `user`  |
| `reg-cli delete`                 | `acps-cli agent delete`                         | `user`  |
| `reg-cli register-entity`        | `acps-cli entity derive`                        | `user`  |
| `reg-cli get-eab`                | `acps-cli cert eab fetch`                       | `user`  |
| `reg-admin-cli login`            | `acps-cli admin auth login`                     | `admin` |
| `reg-admin-cli list`             | `acps-cli admin registry review list`           | `admin` |
| `reg-admin-cli approve`          | `acps-cli admin registry review approve`        | `admin` |
| `reg-admin-cli reject`           | `acps-cli admin registry review reject`         | `admin` |
| `reg-admin-cli disable`          | `acps-cli admin registry agent disable`         | `admin` |
| `reg-admin-cli enable`           | `acps-cli admin registry agent enable`          | `admin` |
| `ca-cli new-cert`                | `acps-cli cert issue`                           | `user`  |
| `ca-cli renew-cert`              | `acps-cli cert renew`                           | `user`  |
| `ca-cli key-rollover`            | `acps-cli cert account-key rollover`            | `user`  |
| `ca-cli revoke-cert`             | `acps-cli cert revoke`                          | `user`  |
| `ca-cli update-trust-bundle`     | `acps-cli cert trust-bundle update`             | `user`  |
| `ca-cli download-crl`            | `acps-cli cert crl download`                    | `user`  |
| `ca-cli crl-info`                | `acps-cli cert crl info`                        | `user`  |
| `ca-cli crl-detail`              | `acps-cli cert crl detail`                      | `user`  |
| `ca-cli crl-list`                | `acps-cli admin ca crl list`                    | `admin` |
| `ca-cli refresh-crl`             | `acps-cli admin ca crl refresh`                 | `admin` |
| `ca-cli check-ocsp`              | `acps-cli cert ocsp check`                      | `user`  |
| `ca-cli ocsp-cert-status`        | `acps-cli cert ocsp cert-status`                | `user`  |
| `ca-cli ocsp-responder-info`     | `acps-cli admin ca ocsp responder-info`         | `admin` |
| `ca-cli ocsp-stats`              | `acps-cli admin ca ocsp stats`                  | `admin` |
| `ca-cli status`                  | `acps-cli cert status`                          | `user`  |
| `disco-cli status`               | `acps-cli discover status`                      | `user`  |
| `disco-cli query`                | `acps-cli discover query`                       | `user`  |
| `disco-cli sync`                 | `acps-cli admin discovery run-sync`             | `admin` |
| `disco-cli dsp status`           | `acps-cli admin discovery dsp status`           | `admin` |
| `disco-cli dsp registry-info`    | `acps-cli admin discovery dsp registry-info`    | `admin` |
| `disco-cli dsp sync`             | `acps-cli admin discovery dsp sync`             | `admin` |
| `disco-cli dsp start`            | `acps-cli admin discovery dsp start`            | `admin` |
| `disco-cli dsp stop`             | `acps-cli admin discovery dsp stop`             | `admin` |
| `disco-cli dsp reset`            | `acps-cli admin discovery dsp reset`            | `admin` |
| `disco-cli dsp hard-reset`       | `acps-cli admin discovery dsp hard-reset`       | `admin` |
| `disco-cli dsp register-webhook` | `acps-cli admin discovery dsp register-webhook` | `admin` |

## 落地策略建议

既然当前旧入口没有对外发布历史包袱，建议直接做一次性切换，不保留中间兼容态：

1. 入口层只保留 `acps-cli = acps_cli.main:main`，旧四个 console script 直接删除。
2. README、命令文档、测试脚本、联调脚本全部同步切换到新命令树。
3. 配置模型直接切换到 `[service].base_url`、`[auth].user_token_file`、`[auth].admin_token_file` 等新键，不再运行时兼容旧键。
4. 命令帮助、退出码和鉴权行为以本设计文档中的命令树、认证矩阵、状态机和配置优先级为准，不再为旧形态保留额外分支。

## 当前已确认的结论

CLI 只保留 `user` 与 `admin` 两级语义，不再单独引入 `internal` 级别。像 `hard-reset`、`register-webhook` 这类命令仍然属于 `admin`，只是在 help 和文档中按需要补充 `test-only` 或 `dangerous` 提示。

本次改造采用一次性切换方案：不保留旧命令入口兼容壳，也不保留旧配置键的运行时兼容逻辑。

## 推荐结论

如果只做一版“初步设计”，我建议先采用下面这个最稳妥的方向：

- 普通用户默认命令保留在根下：`auth`、`agent`、`entity`、`cert`、`discover`
- 所有管理员和测试控制面命令统一收敛到 `admin`
- 保留当前三类服务配置 section，但直接切换到统一的 `base_url` 与新凭证键
- 代码、文档、测试和脚本一次性切换到 `acps-cli`，不保留旧四个脚本兼容壳

这套方案最大的优点是：命令路径、鉴权边界和配置模型可以一次性收敛，不再为中间兼容态增加额外复杂度。
