# ATR 发布就绪说明

## 1. 目的

本文档用于记录本轮 ATR 改造在 `registry-server`、`ca-server`、`acps-cli` 三仓的发布就绪状态，作为 Phase 5.3 的冻结依据。

适用范围：

- `registry-server`
- `ca-server`
- `acps-cli`
- 配套设计与运维文档（`acps-design`、README、release 脚本、测试夹具）

本文档不替代执行计划与执行台账；详细实施过程、审核记录和缺陷闭环见：

- `docs/atr-execution-plan.md`
- `docs/atr-execution-ledger.md`
- `docs/atr-gap-analysis.md`

## 2. 发布范围

本轮发布范围覆盖以下内容：

1. `registry-server`
   - 收口 `9001` public 平面与 `9002` Provider 侧本体证书 mTLS 平面。
   - `/entity` 仅保留在 `9002`，并完成 token + mTLS + 授权三层边界。
   - 支持真实 `app.main_mtls` TLS listener，通过本地 CA 服务端证书运行。

2. `acps-cli`
   - 收口 `reg-cli register-entity` 与本体证书选择模型。
   - 收口 `ca-cli` 的 CA 服务根地址输入模型与“一 AIC 一把 ACME account key”模型。
   - 文档、示例配置、测试夹具和 live workflow 对齐当前主链路。

3. `ca-server`
   - 收口 public / internal / admin 边界。
   - 固化 `serverAuth` / `clientAuth` 单一 EKU 语义。
   - 明确 challenge 相关对象为兼容层，只读，不再增强。

4. 跨仓文档与脚本
   - README、设计文档、release 脚本、测试入口和本地便捷脚本统一到当前 just / EAB / 双 listener 语义。

## 3. 关键发布结论

1. Phase 0 至 Phase 4 已全部完成并通过独立审核。
2. Phase 5.1 已完成 live 联调，证据足以支持 accepted。
3. Phase 5.2 已完成缺陷清零检查与回归矩阵执行，当前无未关闭的 `critical` / `major` 缺陷阻塞本轮冻结。
4. 当前代码侧 ATR 主链路已具备冻结条件；若进入真实部署，还需部署层提供 `9002` 的外部 TCP/TLS 入口。

## 4. 验证与证据

### 4.1 registry-server 9002 服务端证书自举

已存在并复核的服务端自举目录：

- `.tmp/registry-server-9002-bootstrap/registry-server-service-acs.json`
- `.tmp/registry-server-9002-bootstrap/approve.json`
- `.tmp/registry-server-9002-bootstrap/eab.json`
- `.tmp/registry-server-9002-bootstrap/service-bootstrap-summary.json`
- `.tmp/registry-server-9002-bootstrap/keyfiles/`

关键事实：

- 服务 AIC：`1.2.156.3088.1.0001.00001.X5GS54.000000.0EJM`
- endpoint：`https://localhost:9002/acps-atr-v2/entity`
- 证书 SAN：`localhost`、`host.docker.internal`、`127.0.0.1`
- 当前 `9002` listener 通过 `python -m app.main_mtls` 使用该证书材料运行

### 4.2 Phase 5.1 live ontology / entity 主链路

本轮 live 联调产物目录：

- `.tmp/phase5-live-20260503-004206-508037/phase5-summary.json`
- `.tmp/phase5-live-20260503-004206-508037/01-login.json`
- `.tmp/phase5-live-20260503-004206-508037/02-upsert-ontology.json`
- `.tmp/phase5-live-20260503-004206-508037/03-submit-ontology.json`
- `.tmp/phase5-live-20260503-004206-508037/05-approve-ontology.json`
- `.tmp/phase5-live-20260503-004206-508037/06-get-eab-ontology.json`
- `.tmp/phase5-live-20260503-004206-508037/08-register-entity.json`
- `.tmp/phase5-live-20260503-004206-508037/09-get-eab-entity.json`
- `.tmp/phase5-live-20260503-004206-508037/keyfiles/`

已打通的步骤：

1. `login`
2. 本体 `upsert -> submit -> approve`
3. 本体 `get-eab -> new-cert`
4. `register-entity`（通过真实 `https://localhost:9002` mTLS 平面）
5. 实体 `get-eab -> new-cert`

关键结果：

- ontology AIC：`1.2.156.3088.1.0001.00001.5HWJJF.000000.0A94`
- entity AIC：`1.2.156.3088.1.0001.00001.5HWJJF.9AGSYC.0YF0`
- ontology / entity 证书均已成功生成
- ontology / entity 证书 CN 均等于各自 AIC
- ontology / entity 证书 EKU 均为 `1.3.6.1.5.5.7.3.2`（`clientAuth`）

### 4.3 回归矩阵

已执行的回归命令与结果：

1. `acps-cli`
   - 命令：`uv run pytest tests/e2e/test_atr_eab_workflow.py -k test_register_approve_get_eab_and_cert -q`
   - 结果：`1 passed, 4 deselected`

2. `registry-server`
   - 命令：`uv run pytest tests/unit/test_agent_service_boundaries.py tests/unit/test_acs_validation.py tests/integration/test_atr_api.py -q`
   - 结果：`42 passed`

3. `ca-server`
   - 命令：`uv run pytest tests/integration/test_eab_transition.py tests/integration/test_ext.py tests/integration/test_acme.py -k 'validate_aic_no_longer_requires_challenge_url or new_order_server_auth_finalizes_to_server_auth_cert or trust_bundle' -q`
   - 结果：`3 passed, 95 deselected`

4. 环境健康检查
   - `http://localhost:9001/health` -> `{"status":"ok"}`
   - `http://localhost:9003/health` -> `{"status":"healthy","service":"Agent CA API","version":"2.1.0","environment":"development"}`

## 5. 风险结论

当前风险结论如下：

1. 代码级 ATR 主链路风险已收敛到可冻结状态。
2. 文档、CLI、设计文档、release/env 模板和测试入口已同步，不再存在已知的用户向旧命名/旧命令残留。
3. 当前仍保留一个部署层未决事项：`9002` listener 的外部 TCP/TLS 暴露仍需由 `acps-infra` / 部署环境提供；本轮代码仓仅负责 `app.main_mtls` 与证书材料接口，不负责生产网络入口。
4. challenge 相关结构已明确降级为兼容层；后续若继续增强 challenge 路径，将违反当前冻结边界。

结论：

- 对三仓代码与文档而言，可进入冻结。
- 对真实外部部署而言，需在部署方案中显式承接 `9002` 暴露与证书挂载。

## 6. 回滚方案

若本轮 ATR 变更在更大范围验证中出现不可接受问题，回滚建议如下：

1. `registry-server`
   - 停止 `app.main_mtls` 监听进程或移除部署层对 `9002` 的暴露。
   - 回退到 ATR 改造前的稳定版本，恢复未拆分的旧运行入口。
   - 撤下当前服务端证书材料，并对外声明 `register-entity` 不可用。

2. `acps-cli`
   - 回退到不包含 `register-entity` / 新 `9002` 配置 / 新 account key 模型的稳定版本。
   - 移除基于本体证书的 `.registry-client/ontology-mtls/` 运行材料。

3. `ca-server`
   - 回退到 Phase 3 前的稳定版本时，必须同时回退路径保护、认证依赖和 `serverAuth` 相关测试基线。
   - 若已签发新的服务证书或测试证书，需要在回滚记录中明确是否吊销，并清理对应联调数据。

4. 数据与联调环境
   - 本轮 live 证据均在本地开发数据库与 `.tmp/` 工作目录中产生，不涉及生产迁移脚本。
   - 若仅回滚联调环境，清理 `.tmp/phase5-live-*`、`.tmp/registry-server-9002-bootstrap` 和测试数据即可。

## 7. 未决事项

1. `acps-infra` / 部署层需要提供 `9002` 的真实外部 TCP/TLS 暴露方式。
2. 若进入对外发布，还需要补充部署说明中关于 `REGISTRY_SERVER_MTLS_CERT_FILE`、`REGISTRY_SERVER_MTLS_KEY_FILE`、`REGISTRY_SERVER_MTLS_CA_CERT_FILE` 的挂载约束。
3. 当前文档与台账均以本地联调证据为主；若要形成正式发布包，还应追加版本号、发布日期、发布责任人与人工确认记录。

## 8. 冻结建议

建议状态：`ready-to-freeze`

建议执行：

1. 在执行台账中将 5.1、5.2、5.3 依次更新为 accepted / accepted / accepted。
2. 由项目负责人补充人工确认记录。
3. 如需对外发布，再由部署负责人补齐 `9002` 外部暴露方案。
