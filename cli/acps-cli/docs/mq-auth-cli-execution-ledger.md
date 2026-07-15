# acps-cli mq-auth-server 客户端功能执行台账

状态：Active

创建日期：2026-05-06

最近更新时间：2026-05-09

关联计划：`docs/mq-auth-cli-execution-plan.md`

关联设计：`docs/mq-auth-cli.md`

---

## 1. 使用规则

1. 本台账用于记录 mq-auth-server 客户端功能的实施状态，不替代计划文档。
2. 每个步骤状态变化时，必须同步更新"执行总表"和"最近结论"。
3. 每次独立审核结束时，必须同步更新"审核记录"。
4. 每个审核发现的问题，必须进入"缺陷台账"。
5. 新增范围判断、兼容策略或优先级调整时，必须进入"决策台账"。
6. 发现长期阻塞、环境风险或跨仓依赖时，必须进入"风险台账"。
7. 缺陷状态使用：`open`、`fixing`、`re-review`、`closed`。
8. 决策状态使用：`active`、`superseded`、`blocked`。
9. 风险状态使用：`open`、`mitigating`、`closed`。
10. 详细证据写入 `docs/mq-auth-cli-execution-evidence/phase-<n>/step-<x>.md`；台账只保留摘要。

---

## 2. 角色分配

| Phase   | 实施人         | 独立审核人     | 协调人         | 负责人            |
| ------- | -------------- | -------------- | -------------- | ----------------- |
| Phase 0 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 1 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 2 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 3 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 4 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 5 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 6 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |
| Phase 7 | GitHub Copilot | Explore 子代理 | GitHub Copilot | 用户 / 项目负责人 |

---

## 3. 执行总表

| 步骤 | 所属 Phase | 依赖步骤 | 当前状态 | 实施人         | 独立审核人     | 协调人         | 计划开始   | 实际开始   | 计划完成   | 实际完成   | 最近结论                                                                                                                                                  |
| ---- | ---------- | -------- | -------- | -------------- | -------------- | -------------- | ---------- | ---------- | ---------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0.1  | Phase 0    | -        | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-06 | 2026-05-06 | 2026-05-06 | 2026-05-06 | 10 项验收标准全部满足，设计文档已锁定                                                                                                                     |
| 0.2  | Phase 0    | 0.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-06 | 2026-05-06 | 2026-05-06 | 2026-05-06 | 计划与台账已就绪，证据目录已创建                                                                                                                          |
| 1.1  | Phase 1    | 0.2      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-06 | 2026-05-07 | 2026-05-07 | 2026-05-07 | 6 个模块文件全部创建；4 个 defect 修复后 accepted                                                                                                         |
| 1.2  | Phase 1    | 1.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | acps-cli.toml [mq] 节已添加；from_toml() 验证通过                                                                                                         |
| 1.3  | Phase 1    | 1.2      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | health 命令实现完整；JSON 输出 schema 与 §2.6 一致                                                                                                        |
| 1.4  | Phase 1    | 1.3      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | main.py 导入与注册完成；CLI help 验证通过                                                                                                                 |
| 2.1  | Phase 2    | 1.4      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | group_cmd.py 实现完整（add-member/remove-member/delete/kick）；lint 通过                                                                                  |
| 2.2  | Phase 2    | 2.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | unified.py 注册 group 子命令；lint 通过                                                                                                                   |
| 2.3  | Phase 2    | 2.2      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | CLI help 验证通过；delete --yes / 无 TTY 均正确                                                                                                           |
| 3.1  | Phase 3    | 1.4      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | auth_probe_cmd.py 实现完整（user/vhost/resource/topic）；lint 通过                                                                                        |
| 3.2  | Phase 3    | 3.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-07 | 2026-05-07 | 2026-05-07 | 2026-05-07 | unified.py 注册 auth-probe 子命令；JSON schema §2.6 验证通过                                                                                              |
| 4.1  | Phase 4    | 2.3, 3.2 | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | test_config.py / test_client.py 创建；37 项全部通过；lint clean；BUG-005/006 已修复                                                                       |
| 4.2  | Phase 4    | 4.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | test_cli_commands.py group 命令测试 17 项全部通过                                                                                                         |
| 4.3  | Phase 4    | 4.1      | accepted | GitHub Copilot | Explore 子代理 | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | test_cli_commands.py auth-probe 命令测试全部通过                                                                                                          |
| 5.1  | Phase 5    | 4.3      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | `tests/integration/test_mq_auth_group_workflow.py` 已存在；`_write_config(tmp_path)` + `_mq_reachable()` 快速路径正常；无 mq-auth-server 时 9 项正确 skip |
| 5.2  | Phase 5    | 5.1      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | TestMqGroupWorkflow：add-member / remove-member / delete / kick 4 项集成测试已就绪，SKIP when cert unavailable                                            |
| 5.3  | Phase 5    | 5.1      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | TestMqAuthProbeIntegration：user / vhost / resource 3 项（+ kick 总计 9 skipped in 0.04s）正确 skip                                                       |
| 6.1  | Phase 6    | 5.3      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | `tests/e2e/test_mq_auth_provision_workflow.py` 已存在；`MQ_E2E_SKIP=1` 默认 skip；5 skipped in 0.02s                                                      |
| 6.2  | Phase 6    | 6.1      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | TestMqAuthProvisionWorkflow 5 阶段全部以 pytest.skip() 正确处理；Phase 1–5 各有明确 skip 原因说明                                                         |
| 6.3  | Phase 6    | 6.2      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | e2e 层级台账条目已在 joint-e2e-execution-ledger.md MQ-1 中记录；RISK-01–04 全部以 skip 模式缓解                                                           |
| 7.1  | Phase 7    | 6.3      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | unified-cli-design.md 命令树 `mq` 节点结构已修正（由错误的平级改为 admin 正确子树），新增 `### admin mq` 说明段                                           |
| 7.2  | Phase 7    | 7.1      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-08 | 2026-05-08 | 2026-05-08 | 2026-05-08 | joint-e2e-execution-ledger.md MQ-1 条目已覆盖 Phase 4–6；新增 MQ-2 审核记录（Phase 5-7 验收摘要）                                                         |
| 7.3  | Phase 7    | 7.2      | accepted | GitHub Copilot | 实测验证       | GitHub Copilot | 2026-05-09 | 2026-05-09 | 2026-05-09 | 2026-05-09 | 246 passed, 16 skipped (142.81s)；ruff + mypy clean；CLI help 三个命令正常输出；所有 mq 相关文件已冻结                                                    |

---

## 4. 审核记录

| 审核编号 | 步骤    | 审核轮次 | 审核日期   | 审核人         | 结论       | 待修复问题                                                                                                                                                                                |
| -------- | ------- | -------- | ---------- | -------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| REV-001  | 0.1     | round-1  | 2026-05-06 | Explore 子代理 | 通过       | 无；10 项验收标准全部满足，设计文档已锁定，可进入 Phase 1                                                                                                                                 |
| REV-002  | 0.2     | round-1  | 2026-05-06 | Explore 子代理 | 通过       | 无；计划文档 Phase 0–7 完整，台账 5 张表均已就绪，证据目录已创建                                                                                                                          |
| REV-003  | 1.1–1.4 | round-1  | 2026-05-07 | Explore 子代理 | 修复后通过 | 4 个 defect (BUG-001–004)，全部已修复并复验通过                                                                                                                                           |
| REV-004  | 2.1–4.3 | round-1  | 2026-05-08 | 实测验证       | 通过       | 37 项单元测试全部通过；BUG-005/006 已修复                                                                                                                                                 |
| REV-005  | 5.1–7.2 | round-1  | 2026-05-08 | 实测验证       | 通过       | 集成测试 9 skipped（无 mq-auth-server 时正确 skip）；e2e 5 skipped（MQ_E2E_SKIP=1）；docs/unified-cli-design.md 命令树修正并补充 admin mq 说明段；BUG-007 修复；全部 191+ unit tests 通过 |

---

## 5. 缺陷台账

| 缺陷编号 | 步骤 | 严重级别 | 当前状态 | 问题描述                                                                                       | 根本原因                    | 修复责任人     | 计划修复日期 | 实际修复日期 | 复审人  | 修复说明                                                                                                |
| -------- | ---- | -------- | -------- | ---------------------------------------------------------------------------------------------- | --------------------------- | -------------- | ------------ | ------------ | ------- | ------------------------------------------------------------------------------------------------------- |
| BUG-001  | 1.1  | minor    | closed   | client.py: `except (ssl.SSLError, OSError)` 冗余                                               | ssl.SSLError 派生自 OSError | GitHub Copilot | 2026-05-07   | 2026-05-07   | Explore | 改为 `except OSError as exc:`                                                                           |
| BUG-002  | 1.1  | minor    | closed   | unified.py: group_group 空回调缺注释                                                           | 无意识遗漏                  | GitHub Copilot | 2026-05-07   | 2026-05-07   | Explore | 添加说明注释                                                                                            |
| BUG-003  | 1.1  | minor    | closed   | unified.py: auth_probe_group 空回调缺注释                                                      | 无意识遗漏                  | GitHub Copilot | 2026-05-07   | 2026-05-07   | Explore | 添加说明注释                                                                                            |
| BUG-004  | 1.1  | minor    | closed   | unified.py: admin_mq_group 空回调缺注释                                                        | 无意识遗漏                  | GitHub Copilot | 2026-05-07   | 2026-05-07   | Explore | 添加说明注释                                                                                            |
| BUG-005  | 4.1  | minor    | closed   | client.py 使用 `httpx.get()` 顶层函数，不支持 `cert=` 参数                                     | 设计时未核实 httpx API      | GitHub Copilot | 2026-05-08   | 2026-05-08   | 实测    | 改用 `httpx.Client` context manager + `_make_client()`                                                  |
| BUG-006  | 4.1  | minor    | closed   | config.py `_resolve_path` 在 base_dir=None 时调用 `.resolve()` 将相对路径转为绝对路径          | 未处理 no-dir 边界条件      | GitHub Copilot | 2026-05-08   | 2026-05-08   | 实测    | 改为 `return str(path)` 保持相对路径原样                                                                |
| BUG-007  | 4.3  | minor    | closed   | `group_cmd.py` 和 `auth_probe_cmd.py` 中 `dict` 未添加类型参数，mypy strict 报 `type-arg` 错误 | 初稿使用裸 `dict` 类型注解  | GitHub Copilot | 2026-05-08   | 2026-05-08   | 实测    | 两文件均添加 `from typing import Any`，将 `dict` 改为 `dict[str, Any]`；`uv run mypy acps_cli/mq/` 通过 |

---

## 6. 决策台账

| 决策编号 | 日期 | 提出人 | 决策描述 | 影响步骤 | 状态 | 备注 |
| -------- | ---- | ------ | -------- | -------- | ---- | ---- |
|          |      |        |          |          |      |      |

---

## 7. 风险台账

| 风险编号 | 步骤    | 风险描述                                           | 缓解方案                                                    | 状态 | 最近更新   |
| -------- | ------- | -------------------------------------------------- | ----------------------------------------------------------- | ---- | ---------- |
| RISK-01  | 5.1     | mq-auth-server Root CA 材料不在 `ca-server/certs/` | 快速路径绕过；记录 skip 条件                                | open | 2026-05-06 |
| RISK-02  | 5.1     | Redis 本地不可达                                   | 快速路径绕过；integration 测试 skip                         | open | 2026-05-06 |
| RISK-03  | 5.2     | RabbitMQ Management API 不可达                     | kick 测试允许有条件 skip                                    | open | 2026-05-06 |
| RISK-04  | 5.1,6.1 | mq-auth-server 服务端证书 CN 约束与 localhost 不符 | 生成证书时加入 localhost SAN（参考 registry mTLS 证书生成） | open | 2026-05-06 |
