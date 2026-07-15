# ATR 改造设计（registry-server / ca-server / acps-cli）

状态：Draft

设计日期：2026-05-02

来源：由原有 ATR 审计/缺口分析文档重构而来，作为下一阶段跨仓改造的实施设计输入。

相关文档：

- 设计基线：`ATR-DESIGN.md`、`ATR-v2.1.0-Improvement.md`、`ATR-Registry-Server.md`、`ATR-CA-Server.md`、`ATR-CA-Client.md`、`ATR-CA-Challenge.md`
- 执行计划：`atr-execution-plan.md`
- 适用实现：`registry-server`、`ca-server`、`acps-cli`
- 已同步修正的设计文档：`ATR-Registry-Server.md`、`ATR-DESIGN.md`

## 1. 文档目标

本文档不再以“审计结论”组织，而是直接给出下一阶段 ATR 改造的目标架构、关键决策、模块设计、实施顺序和待确认事项。

本文档要解决的问题只有三类：

1. 把当前已经跑通的 EAB 主链路保留下来，不重复设计已经稳定的部分。
2. 把尚未落地的安全边界、接口契约和 CLI 行为收敛成明确的目标实现。
3. 为后续代码改造提供一个可执行、可验证、可继续拆分的设计基线。

## 2. 设计输入

### 2.1 保留的既有基线

以下能力已视为稳定基线，本设计在此基础上继续演进：

1. `registry-server` 已实现 `GET /acs/{agent_aic}`、一次性 EAB 凭证生成与消费、实体 AIC 派生和端点冲突校验。
2. `ca-server` 已实现 ACME `directory`、`new-account`、`new-order`、`finalize`、`cert`、`revoke-cert`、`key-change` 等主链路，且 `new-order` 已进入“无 challenge、直接 ready”的 v2.1.0 模式。
3. `acps-cli` 已实现 `reg-cli get-eab`、`ca-cli new-cert/renew-cert/revoke-cert/key-rollover/status/update-trust-bundle`，主链路不再依赖 challenge。

### 2.2 当前必须解决的问题

1. `/entity` 仍缺少真正的本体证书身份绑定，当前安全语义不足。
2. `ca-server` 内部/管理接口认证保护不足，且保护面与实际路径不一致。
3. `acps-cli` 还不能以设计要求的方式完成实体注册。
4. 跨仓在基础 URL、命令参数、密钥模型、HTTP 头与错误语义上存在漂移。

## 3. 设计目标

1. 保留 EAB 驱动的 ATR 主链路，不重做 ACME / EAB 基础流程。
2. 为 `registry-server` 落地明确的双平面模型：`9001` 业务 HTTPS，`9002` 本体证书 mTLS HTTPS。
3. 为 `/entity` 落地“Provider 登录态 + 本地 CA 签发的本体证书 + 归属授权”三层安全模型，并将 `9002` 收敛为 Provider 侧本体证书 mTLS 平面。
4. 为 `ca-server` 落地最小可用的内部/管理面认证方案。
5. 为 `acps-cli` 定义清晰、可实施的实体注册命令、多本体证书选择规则和配置模型。
6. 收敛跨仓契约，减少文档、实现和集成脚本之间的偏差。

## 4. 非目标

1. 本轮不重新设计 ACME 协议本身，不改变 EAB 替代 challenge 的总体路线。
2. 本轮不强制把所有内部接口都改成 mTLS；能用 HTTPS + 应用层认证解决的问题，不额外引入新 listener。
3. 本轮不引入前置网关、Service Mesh 或额外的统一入口编排层。
4. 本轮不立即删除所有 challenge 兼容字段和兼容模型，只要求明确“只读兼容、不再增强”。

## 5. 关键设计决策

| 编号 | 决策                                                                                | 说明                                                                                           |
| ---- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| D-01 | `registry-server` 拆分为 `9001` 与 `9002` 两个 HTTPS listener                       | `9001` 承载普通业务与 bootstrap，`9002` 作为 Provider 侧本体证书 mTLS 平面，首期挂载 `/entity` |
| D-02 | `/entity` 的目标认证模型为 `Bearer access token + 本地 CA 本体证书 mTLS + 业务授权` | token 证明“谁在操作”，mTLS 证明“谁持有本体私钥”，授权证明“是否有权操作该本体”                  |
| D-03 | `9002` 不承担登录职责                                                               | `9002` 只复用并校验由 `9001` 体系签发的 access token                                           |
| D-04 | `registry-server /internal/eab/consume` 在本轮维持“服务令牌 + IP allowlist”         | 长期若升级为 mTLS，应单独设计服务间 mTLS 通道，不默认并入 `9002`                               |
| D-05 | `ca-server` 本轮保持单 HTTPS listener                                               | 通过路径分类、应用层认证和 IP 保护面完成最小闭环，不新增第二套 CA listener                     |
| D-06 | challenge 相关结构维持兼容只读状态                                                  | 不再新增 challenge 交互和挑战服务器依赖                                                        |
| D-07 | `9002` 只接受本地 CA 签发的本体证书                                                 | `9002` 的客户端证书校验只信任 registry 所属本地 CA 链，不使用 Trust Bundle 做客户端认证        |
| D-08 | `acps-cli` 必须支持按 `ontology_aic` 选择本体证书                                   | Provider 可同时管理多个本体 AIC，CLI 不再假设全局单一客户端证书                                |

## 6. 目标架构

| 组件              | 入口/平面                  | 主要职责                                                                  | 主要认证方式                                          |
| ----------------- | -------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------- |
| `registry-server` | `9001` 业务 HTTPS          | 登录、用户态 API、EAB、ACS 查询、内部 token 接口                          | Bearer Token、IP allowlist、角色权限                  |
| `registry-server` | `9002` 本体证书 mTLS HTTPS | 首期处理 `/entity`，后续仅承接同类“Provider 持本地 CA 本体证书接入”的路由 | Bearer access token + 本地 CA 本体证书 + AIC 归属授权 |
| `ca-server`       | `9003` ATR HTTPS           | ACME 主链路、Trust Bundle、CRL、OCSP、内部/管理 API                       | ACME JWS/EAB、公开读取、服务认证、管理员认证          |
| `acps-cli`        | `reg-cli`                  | 登录、获取 EAB、实体注册                                                  | 9001 登录态复用到 9002                                |
| `acps-cli`        | `ca-cli`                   | EAB 驱动的 ACME 账户与证书操作                                            | ACME JWS + 本地密钥管理                               |

所有入口都必须是 HTTPS。本设计中的“是否需要 mTLS”讨论只针对客户端证书，不针对是否允许明文 HTTP。

`9002` 在本文中的“mTLS 平面”不是“任意双向 TLS 接口”的统称，而是特指“要求 Provider 使用本地 CA 签发的本体证书接入的受限平面”。服务间 mTLS 如未来确有需要，应单独设计，不默认并入 `9002`。

## 7. `registry-server` 设计

### 7.1 路由与 listener 分配

| 路由                           | 目标 listener | 认证方式                                           | 说明                                                                  |
| ------------------------------ | ------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| `/api/v1/auth/**`              | `9001`        | 登录/刷新                                          | Provider 登录入口                                                     |
| `/api/v1/account/**`           | `9001`        | Bearer Token                                       | 普通用户态 API                                                        |
| `/api/v1/verification/**`      | `9001`        | Bearer Token + 角色/状态校验                       | 实名、机构认证相关流程                                                |
| `/api/v1/agent/**`             | `9001`        | Bearer Token + 角色/状态校验                       | 普通 Agent 提交、审批、查询                                           |
| `/acps-atr-v2/acs/{aic}`       | `9001`        | HTTPS + IP allowlist / 内部网络信任                | 供 CA、Discovery、内部服务查询                                        |
| `/acps-atr-v2/eab/{agent_aic}` | `9001`        | Bearer Token                                       | EAB bootstrap 入口                                                    |
| `/internal/eab/consume`        | `9001`        | 服务令牌 + IP allowlist                            | 当前仍走公共业务平面；后续若升级为 mTLS，应进入独立的服务间 mTLS 设计 |
| `/acps-atr-v2/entity`          | `9002`        | Bearer access token + 本地 CA 本体证书 mTLS + 授权 | 当前由本地 CA 本体证书 mTLS 平面承载的首个业务路由                    |

设计约束：`9001` 不得继续暴露 `/entity` 的平行入口。`9002` 作为本体证书 mTLS 平面，不得挂载 `/auth`、`/eab`、`/verification` 等普通业务路由，也不默认承载服务间 mTLS API；后续若新增路由，也必须满足“Provider 持本地 CA 签发的本体证书接入”的同类约束。

`9002` 的客户端证书信任边界只面向 registry 所属本地 CA 签发的本体证书，不面向跨 CA Trust Bundle，也不面向通用服务证书。

### 7.2 `/entity` 的认证与授权链

`/entity` 的目标流程如下：

1. TLS 握手阶段要求客户端必须提供本地 CA 签发、用途满足 `clientAuth` 的本体证书。
2. `9002` 复用 `9001` 的 token 校验依赖，验证 `Authorization: Bearer ...`。
3. 从客户端证书中解析本体 AIC，并确认该证书绑定的是本体主体，而不是实体证书或服务证书。
4. 校验证书中的本体 AIC 与请求体 `ontologyAic` 一致。
5. 校验 token 对应 Provider 用户是否拥有或被授权管理该本体 AIC。
6. 校验本体状态为 `active`，且允许派生实体。
7. 执行实体序列号分配、AIC 生成、ACS 派生和冲突检测。

补充说明：同一 Provider 可以分别持有多个本体 AIC，并多次调用 `/entity` 为不同本体派生实体。每次请求都必须使用与 `ontologyAic` 对应的本体证书；`registry-server` 不维护“当前 Provider 只允许绑定一个本体”的隐式会话状态。

失败语义收敛如下：

| 场景                                   | 返回码 | 说明                                                         |
| -------------------------------------- | ------ | ------------------------------------------------------------ |
| 客户端证书缺失                         | `401`  | mTLS 认证失败                                                |
| 客户端证书不是本地 CA 签发或证书链无效 | `401`  | `9002` 只接受本地 CA 本体证书，不接受跨 CA Trust Bundle 证书 |
| access token 缺失或无效                | `401`  | Provider 认证失败                                            |
| 客户端证书不是本体证书                 | `403`  | 实体证书、服务证书或用途不符合的证书不得调用 `/entity`       |
| 证书 AIC 与请求体不一致                | `403`  | 证书身份绑定失败                                             |
| token 用户不拥有该本体 AIC             | `403`  | Provider 与 ontology AIC 归属不匹配                          |
| 本体已禁用、吊销或配额超限             | `403`  | 业务授权失败                                                 |
| 本体不存在                             | `404`  | 资源不存在                                                   |
| endpoint 冲突                          | `409`  | 现有实体冲突                                                 |

### 7.3 应用装配方式

`registry-server` 应重构为 app factory，而不是一个全量 app 同时监听两个端口。

建议结构：

1. `create_public_app()`：绑定 `9001`，挂载所有普通业务路由，不挂 `/entity`。
2. `create_mtls_app()`：绑定 `9002`，首期挂 `/entity` 和最小健康检查，后续按需扩展其它同类本体证书路由。
3. 共享装配层统一管理：
   - 异常处理器
   - 公共数据库会话与 lifespan
   - 公共日志/OTel 初始化
   - 公共认证依赖注册

建议重点改造文件：

- `app/main.py`
- `app/main_mtls.py`
- `app/app_factory.py`
- `app/agent/api_atr.py`
- `app/agent/service_atr.py`
- `app/eab/api.py`
- `app/eab/service.py`

### 7.4 功能改造项

1. 为 `app/agent/api_atr.py` 增加证书提取依赖。
2. 为 `app/agent/api_atr.py` 复用 `9001` 的 Provider token 校验依赖。
3. 新增 `authorize_entity_registration(user, ontology_aic)` 之类的授权依赖。
4. 在 `app/agent/service_atr.py` 增加实体配额与上限控制。
5. 在 `app/eab/service.py` 把实名、机构、必要时的域名归属治理接入 EAB 发放。

### 7.5 配置模型

建议新增配置：

| 配置项                                        | 含义                                                                        |
| --------------------------------------------- | --------------------------------------------------------------------------- |
| `server.public_port`                          | 公共业务 HTTPS 端口，默认 `9001`                                            |
| `server.mtls_port`                            | 独立本体证书 mTLS 平面端口，默认 `9002`                                     |
| `server.public_tls_certfile`                  | `9001` 服务端证书                                                           |
| `server.public_tls_keyfile`                   | `9001` 服务端私钥                                                           |
| `server.mtls_tls_certfile`                    | `9002` 服务端证书                                                           |
| `server.mtls_tls_keyfile`                     | `9002` 服务端私钥                                                           |
| `server.mtls_tls_client_local_ca_bundle_file` | `9002` 用于校验客户端本体证书的本地 CA 证书链，仅包含本地 root/intermediate |
| `server.mtls_tls_verify_mode`                 | `9002` 客户端证书校验模式                                                   |

`9002` 与 `9001` 必须共享同一套 JWT 验签配置：

- `jwt.secret_key`
- `jwt.algorithm`
- `access_token_expire_minutes`

技术约束：`9002` 直接复用 `9001` 现有的 token 校验依赖与用户解析逻辑，不单独引入 token 刷新、特殊 audience 或独立 token 格式。`9002` 只负责“校验当前 access token 是否有效，以及当前用户是否有权操作证书对应的本体 AIC”。

推荐实现方式：`create_public_app()` 与 `create_mtls_app()` 必须共享同一个 `settings` 对象和同一套 `app.core.auth` 依赖模块。`9002` 不重新实现 Bearer 解析器，也不复制一套 JWT 校验逻辑，而是直接复用 `get_current_user()` / `check_user_role()` 所依赖的配置与用户解析流程。

补充约束：`server.mtls_tls_client_local_ca_bundle_file` 不是 CA Server 下发的 Trust Bundle。Trust Bundle 仍用于 Agent 间跨 CA mTLS；`9002` 只用于验证 registry 所属本地 CA 签发的本体证书。

## 8. `ca-server` 设计

### 8.1 端点分层与认证模型

本轮不新增 CA 的第二 listener，而是在单一 HTTPS listener 内按路径收敛认证模型。

| 端点类别                            | 目标认证方式                            | 说明                        |
| ----------------------------------- | --------------------------------------- | --------------------------- |
| ACME 主链路：`/acps-atr-v2/acme/**` | ACME JWS + EAB                          | bootstrap 入口，不要求 mTLS |
| Trust Bundle / CRL / OCSP 读取      | 公开 HTTPS + 缓存头 + 限流              | 供客户端和代理读取          |
| `revoke-notify`、`retrieve/*`       | 服务认证 + IP allowlist                 | 内部服务能力                |
| `crl/list`、`crl/refresh`           | 管理员认证或内部服务认证 + IP allowlist | 运维控制面                  |
| `/admin/certificates/**`            | 管理员认证 + IP allowlist               | 管理员证书管理面            |

### 8.2 必要改造项

1. 扩大 `ATRManagementIPFilterMiddleware` 的默认保护面，覆盖实际管理与内部路径。
2. 为 `api_ext.py` 与 `api.py` 增加真正的服务认证或管理员认证依赖。
3. 为 Trust Bundle、CRL、OCSP 补齐缓存头与限流语义。
4. 本轮明确从账户响应中移除尚未实现的 `orders` 链接，不再继续暴露无对应路由的 URL；若未来确需支持账户订单列表，再作为独立能力补充完整路由、文档与测试。
5. 本轮维持 `registry-server /internal/eab/consume` 的服务令牌调用方式；后续若升级为 mTLS，应单独设计服务间 mTLS 通道，而不是并入 `9002` 本体证书平面。
6. 明确 CA Server 不采用“每 Provider 一个 ACME 账户”的隐式模型。一个 Provider 可以基于多个 AIC 分别获取 EAB、分别建立独立 ACME 账户并申请证书；CA Server 侧只按 AIC/EAB/账户密钥的绑定关系处理，不维护 Provider 级的单账户假设。

CA Server 多账户存储模型约束：

1. ACME 账户的主体绑定粒度为 `AIC + account key`，而不是 `Provider + account key`。
2. 同一 Provider 若控制多个 AIC，则这些 AIC 对应的 EAB、ACME 账户和账户密钥彼此独立。
3. CA Server 侧不需要引入 Provider 级聚合主键来限制账户数量；只需要确保账户、EAB 消费记录和后续订单请求沿着 AIC 绑定关系一致。
4. Phase 3 的实现若需要持久化辅助索引，应优先围绕 AIC 建立，而不是围绕 Provider 建立默认单账户约束。

建议重点改造文件：

- `app/core/atr_ip_filter.py`
- `app/main.py`
- `app/certificates/api_ext.py`
- `app/certificates/api.py`
- `app/acme/registry_client.py`
- `app/ocsp/api.py`
- `app/crl/api.py`

### 8.3 路径契约决策

本设计明确采用“外部一个根，内部一个派生根”的模型。

其中：

1. `CA_SERVER_BASE_URL` 是唯一的外部配置，语义固定为 CA Server 的服务根地址。
2. `CA_SERVER_ATR_BASE_URL` 是内部派生值，语义固定为 ATR API 前缀根地址。
3. `CA_SERVER_ATR_BASE_URL` 由 `CA_SERVER_BASE_URL` 与固定 ATR 前缀 `/acps-atr-v2` 组合而成，不作为第二个独立外部配置暴露。
4. `/health`、`/metrics` 等服务级端点应基于 `CA_SERVER_BASE_URL` 访问；`/acme`、`/crl`、`/ocsp`、`/ca` 等 ATR 业务端点应基于 `CA_SERVER_ATR_BASE_URL` 访问。

示例：

- `CA_SERVER_BASE_URL = https://ca.example.com`
- `CA_SERVER_ATR_BASE_URL = https://ca.example.com/acps-atr-v2`

基于此约束：

1. `ca-cli` 的外部运行时配置统一使用 `CA_SERVER_BASE_URL`。
2. `ca-cli` 内部配置对象应派生出 `CA_SERVER_ATR_BASE_URL`，后续在其后拼接 `/acme`、`/crl`、`/ocsp`、`/ca` 等子路径。
3. 新设计和新实现中，`CA_SERVER_BASE_URL` 不再表示 ATR 根，而只表示服务根。
4. 旧文档或旧脚本中若直接把 `CA_SERVER_ATR_BASE_URL` 当成外部输入，应逐步迁移为 `CA_SERVER_BASE_URL`；实现内部若需要 ATR 根，可继续保留派生属性。

## 9. `acps-cli` 设计

### 9.1 `reg-cli`

`reg-cli` 的目标行为如下：

1. `login` 只对 `9001` 执行一次，得到的 access token 持久化后同时供 `9001` 和 `9002` 使用。
2. 新增显式命令 `register-entity`，用于基于本体 AIC 注册实体；`submit` 只保留草稿提交语义，不再承担实体注册语义。
3. `register-entity` 必须显式指定 `--ontology-aic`，每次调用只针对一个本体 AIC 发起实体注册。
4. CLI 必须支持多本体证书场景：默认按 `ontology_aic` 从本地材料目录解析对应证书和私钥，不再假设全局单一客户端证书；仅在迁移脚本或人工排障场景下允许命令行显式覆盖证书路径。
5. 实体注册请求必须同时携带：
   - `Authorization: Bearer <token>`
   - 客户端证书
   - 客户端私钥
   - `9002` 服务端证书验证 CA

执行前提：`register-entity` 不是 bootstrap 命令。调用它之前，Provider 必须先通过普通 `9001` 流程完成本体注册与审批，并已经为目标本体 AIC 申请到可用证书和私钥。若同一 Provider 同时管理多个本体 AIC，则每次调用 `register-entity` 都必须显式选择本次要使用的 `ontology_aic`，并解析出与之对应的本体证书材料。随后复用同一次 `login` 获得的 access token，携带该本体证书接入 `9002` 本体证书 mTLS 平面，才能自动注册实体并拿到实体 AIC。

建议配置项：

| 配置项                                 | 含义                                                 |
| -------------------------------------- | ---------------------------------------------------- |
| `registry.server_base_url`             | `9001` 业务入口                                      |
| `registry.atr_base_url`                | `9001` ATR 普通入口                                  |
| `registry.mtls_base_url`               | `9002` 本体证书 mTLS 平面入口                        |
| `registry.ontology_mtls_materials_dir` | 本体 mTLS 材料目录，按 `ontology_aic` 组织证书与私钥 |
| `registry.mtls_server_ca_file`         | 校验 `9002` 服务端证书的 CA                          |

建议重点改造文件：

- `acps_cli/registry/config.py`
- `acps_cli/registry/client.py`
- `acps_cli/registry/commands.py`

推荐目录布局：`{registry.ontology_mtls_materials_dir}/{ontology_aic}/certificate.pem` 与 `{registry.ontology_mtls_materials_dir}/{ontology_aic}/private-key.pem`。`register-entity` 默认按 `--ontology-aic` 查找；仅在脚本迁移或人工排障场景下允许 `--mtls-cert-file` / `--mtls-key-file` 显式覆盖。CLI 在发起请求前应校验所选证书中的本体 AIC 与 `--ontology-aic` 一致，避免误用错误证书。

说明：这里的 `submit --ontology-aic ...` 指的是 `acps-cli` 当前已经存在的 `reg-cli submit` 命令分支，而不是新的协议参数。现状是同一个 `submit` 命令同时承担两类语义：

1. `submit --agent-id <UUID>`：提交草稿进入人工审核。
2. `submit --ontology-aic <AIC> [--payload-file ...]`：基于本体 AIC 直接注册实体。

本设计现已明确收口为：不再保留这条旧 CLI 用法。后续实现应按以下规则落地：

1. `reg-cli submit --agent-id <UUID>` 保留，专用于草稿提交和人工审核流程。
2. `reg-cli submit --ontology-aic ...` 删除，不保留兼容窗口。
3. 基于本体 AIC 注册实体统一改为 `reg-cli register-entity ...`。
4. 现有文档、测试和自动化脚本同步迁移到 `register-entity`。

### 9.2 `ca-cli`

`ca-cli` 的主链路保持 EAB + ACME 模型不变，但本轮需补齐以下契约：

1. 固化“外部 `CA_SERVER_BASE_URL`、内部派生 `CA_SERVER_ATR_BASE_URL`”的运行时语义，并在 CLI 与文档中完成迁移。
2. 补齐 `ed25519` 支持，并保留 `ec`、`rsa` 兼容能力。
3. 收敛失败退出码，避免自动化脚本误判。
4. 采用“每 AIC 一把”的 ACME 账户密钥模型：每个 AIC 独立持有和复用自己的 ACME 账户密钥。
5. 与 CA Server 侧契约保持一致：`ca-cli` 必须允许同一 Provider 同时为多个 AIC 分别维护独立的 EAB、账户密钥、证书和信任包更新流程，而不是假设单 Provider 只有一个默认 ACME 账户。

多本体操作示例：

1. Provider 完成本体 `AIC-1` 的普通注册与审批，并为 `AIC-1` 获取 EAB 与证书。
2. Provider 再完成本体 `AIC-2` 的普通注册与审批，并为 `AIC-2` 获取另一套 EAB 与证书。
3. `ca-cli` 为 `AIC-1` 与 `AIC-2` 分别维护各自的账户密钥与证书材料。
4. `reg-cli register-entity --ontology-aic <AIC-1>` 时，只允许使用 `AIC-1` 对应的本体证书接入 `9002`。
5. `reg-cli register-entity --ontology-aic <AIC-2>` 时，必须切换到 `AIC-2` 对应的本体证书；`registry-server` 只根据当前请求中的证书 AIC 与 `ontologyAic` 一致性进行判定，不依赖 Provider 级默认本体上下文。

建议重点改造文件：

- `acps_cli/ca/config.py`
- `acps_cli/ca/commands.py`
- `acps_cli/ca/keys.py`
- `acps_cli/ca/acme.py`

## 10. 跨仓契约与兼容策略

### 10.1 安全边界模型

目标安全边界收敛为：

1. Provider 普通业务入口：HTTPS + Bearer Token。
2. 实体注册入口：HTTPS + Bearer Token + 本地 CA 本体证书 mTLS + 授权。
3. 内部服务入口：HTTPS + 服务认证 + IP allowlist；如未来升级到 mTLS，应使用独立的服务间证书模型，不与 `9002` 共用语义。
4. 管理入口：HTTPS + 管理员认证 + IP allowlist。

### 10.2 Challenge 兼容层

以下兼容项短期保留，但必须标记为“只读兼容，不再增强”：

1. `registry-server` ACS schema 中的 `x-caChallengeBaseUrl`
2. `ca-server` 中的 `AcmeChallenge`、`ChallengeService` 与 challenge 响应结构
3. 旧设计文档中的 Challenge Server 存档说明

标记落点要求：Phase 4.3 落地时，至少要在设计文档、相关代码注释和测试说明中统一标记“兼容层 / 只读 / 不再增强”，避免后续实现继续把 challenge 视为可扩展主链路。

推荐标记格式：

1. 设计文档章节标题或段落中显式标注“兼容层（只读，不再增强）”。
2. 代码注释统一使用 `兼容层：只读，不再增强（Phase 4.3）`。
3. 测试说明中显式区分“主链路测试”与“兼容层保留测试”，避免后续把 challenge 测试误当成主链路能力继续扩展。

### 10.3 设计文档同步状态

本轮已同步到设计基线：

1. `ATR-Registry-Server.md`
2. `ATR-DESIGN.md`

建议后续再补：

1. `ATR-v2.1.0-Improvement.md`

## 11. 分阶段实施计划

### Phase 1：先收敛 `registry-server` 的本体证书 mTLS 平面与 `/entity` 安全模型

目标：让 `9002` 成为独立的本地 CA 本体证书 mTLS 平面，并先在 `/entity` 落地 `token + mTLS + 授权`。

输出：

1. `registry-server` app factory 与双 listener 骨架。
2. `9002` 本体证书 mTLS 平面骨架与 `/entity` 的证书提取、token 复用、AIC 归属授权。
3. `9001` 不再暴露平行 `/entity` 路由。

### Phase 2：补齐 `acps-cli` 的实体注册能力

目标：让 CLI 真正能以设计要求调用 `9002`。

输出：

1. `register-entity` 命令。
2. 多本体证书场景下的 mTLS 客户端配置项与证书选择规则。
3. access token 复用策略。
4. 删除旧的 `submit --ontology-aic` 分支，并将 `submit` 收敛为纯草稿提交命令。

### Phase 3：补齐 `ca-server` 的内部/管理面保护

目标：为 CA 管理面建立最小可用的真实认证边界。

输出：

1. 管理/内部路径保护面修正。
2. 服务认证与管理员认证依赖。
3. Trust Bundle、CRL、OCSP 的 HTTP 契约修正。

### Phase 4：治理、契约与兼容层收口

目标：减少后续维护成本，避免实现和文档再次分叉。

输出：

1. EAB 发放前置治理接入。
2. 完成“外部 `CA_SERVER_BASE_URL` + 内部派生 `CA_SERVER_ATR_BASE_URL`”的命名迁移与脚本收口。
3. 完成“每 AIC 一把”ACME 账户密钥模型的配置、存储和命令收口。
4. challenge 兼容层正式标记和约束。

## 12. 验证计划

### 12.1 `registry-server`

1. 单元测试：证书解析、token 校验、AIC 归属授权、实体配额。
2. 集成测试：`9002` 本体证书 mTLS 平面下的 `/entity` 成功与失败场景，包括“本地 CA 本体证书通过、跨 CA 证书拒绝、实体/服务证书拒绝”。
3. 回归测试：`9001` 普通业务 API、EAB 发放、ACS 查询不受影响。

### 12.2 `ca-server`

1. 集成测试：内部/管理路径认证失败与成功场景。
2. 协议测试：ACME 主链路、Trust Bundle、CRL、OCSP 的缓存头和错误语义。

### 12.3 `acps-cli`

1. CLI 集成测试：`login -> 本体普通注册/审批 -> get-eab --aic <ontology-aic> -> new-cert --aic <ontology-aic> -> register-entity -> get-eab --aic <entity-aic> -> new-cert --aic <entity-aic>`，并覆盖“单 Provider 管理多个本体 AIC 时，按 `ontology_aic` 选择对应证书”的场景。
2. CLI 端到端测试：覆盖“单次 login，`9001/9002` 共用同一 token；使用目标本体证书接入 `9002` 注册实体；实体拿到 AIC 后再申请实体证书”的完整主链路，并验证错误证书不会被误发往 `9002`。
3. 回归测试：现有 `ca-cli` 主链路和退出码行为。

## 13. CLI 收口决策

本轮已确认以下 CLI 设计决策：

1. `reg-cli submit` 只保留草稿提交语义，对应 `--agent-id <UUID>`。
2. 基于本体 AIC 注册实体的能力从 `submit` 中拆出，改为独立子命令 `reg-cli register-entity`。
3. 不保留 `submit --ontology-aic ...` 的兼容窗口，相关文档、测试和自动化脚本一并迁移。

基于该决策，后续实施可直接按以下顺序推进：先实现 `registry-server /entity` 双平面与 CLI 实体注册，再处理 CA 路径契约和其它兼容层收口。
