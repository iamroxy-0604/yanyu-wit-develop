# acps-cli 统一命令执行台账

状态：Active

创建日期：2026-05-05

关联计划：`acps-cli/docs/unified-cli-execution-plan.md`

关联设计：`acps-cli/docs/unified-cli-design.md`

## 1. 使用规则

1. 本台账用于记录统一 CLI 改造的执行状态，不替代设计文档和执行计划。
2. 每个步骤状态变化时，必须同步更新“执行总表”和“审核记录”。
3. 每个审核发现的问题，必须进入“缺陷台账”。
4. 新增设计判断、范围调整或实现例外时，必须进入“决策台账”。
5. 发现长期阻塞、外部仓依赖或环境风险时，必须进入“风险台账”。
6. 缺陷状态使用：`open`、`fixing`、`re-review`、`closed`。
7. 决策状态使用：`active`、`superseded`、`blocked`。
8. 风险状态使用：`open`、`mitigating`、`closed`。
9. 长时间自动执行时，当前步骤 `accepted` 后必须立即更新“下一动作”，不得留空。
10. `Explore` 子代理负责自动化只读独立审查和复审，不直接代替实施步骤修复代码。
11. `GitHub Copilot` 负责根据审核意见修复问题，并把修复结论回填到台账。
12. 项目负责人只在 Phase 冻结点和最终整体冻结点做人工确认，不要求每一步都签字。
13. 详细实现/验证/复审证据默认归档到 `docs/unified-cli-execution-evidence/phase-<n>/step-<x>.md`；台账内只保留摘要。

## 1.1 Phase 负责人分配

| Phase   | 实施人         | 独立审核人     | 协调人                 |
| ------- | -------------- | -------------- | ---------------------- |
| Phase 0 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |
| Phase 1 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |
| Phase 2 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |
| Phase 3 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |
| Phase 4 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |
| Phase 5 | GitHub Copilot | Explore 子代理 | 项目负责人（人工确认） |

## 2. 执行总表

| 步骤 | 所属 Phase | 当前状态 | 实施人         | 独立审核人     | 计划开始   | 实际开始   | 计划完成   | 实际完成   | 最近结论                                                                            |
| ---- | ---------- | -------- | -------------- | -------------- | ---------- | ---------- | ---------- | ---------- | ----------------------------------------------------------------------------------- |
| 0.1  | Phase 0    | accepted | GitHub Copilot | Explore 子代理 | 2026-05-05 | 2026-05-05 | 2026-05-05 | 2026-05-05 | 三轮独立复审后，设计基线冻结检查清单已机械化，Phase 0.1 可验收                      |
| 0.2  | Phase 0    | accepted | GitHub Copilot | Explore 子代理 | 2026-05-05 | 2026-05-05 | 2026-05-05 | 2026-05-05 | 执行计划、台账、证据归档规则、审核责任链与 Phase 0 退出标准已闭环，Phase 0.2 可验收 |
| 1.1  | Phase 1    | ready    | GitHub Copilot | Explore 子代理 | 2026-05-05 |            | 2026-05-06 |            | 下一步进入统一入口与命令树骨架实现                                                  |
| 1.2  | Phase 1    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-06 |            | 2026-05-06 |            | 待提取共享上下文、错误出口和危险确认机制                                            |
| 1.3  | Phase 1    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-06 |            | 2026-05-06 |            | 待收口新旧命令模块边界                                                              |
| 1.4  | Phase 1    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-06 |            | 2026-05-06 |            | 待锁定帮助信息回归基线                                                              |
| 2.1  | Phase 2    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-06 |            | 2026-05-07 |            | 待切换到新配置键与 `base_url` 语义                                                  |
| 2.2  | Phase 2    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-07 |            | 2026-05-07 |            | 待落地认证矩阵与上下文隔离                                                          |
| 2.3  | Phase 2    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-07 |            | 2026-05-07 |            | 待统一覆盖参数与配置优先级                                                          |
| 2.4  | Phase 2    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-07 |            | 2026-05-07 |            | 待补齐配置/认证测试与文档                                                           |
| 3.1  | Phase 3    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-07 |            | 2026-05-08 |            | 待迁移 `auth` 与 `agent`                                                            |
| 3.2  | Phase 3    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-08 |            | 2026-05-08 |            | 待迁移 `entity derive` 与 `cert eab fetch`                                          |
| 3.3  | Phase 3    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-08 |            | 2026-05-08 |            | 待迁移 `admin registry`                                                             |
| 3.4  | Phase 3    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-08 |            | 2026-05-08 |            | 待完成 Registry 域单元/集成/e2e 三层测试适配、回归与文档切换                        |
| 4.1  | Phase 4    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-08 |            | 2026-05-09 |            | 待迁移 `cert` 域用户命令                                                            |
| 4.2  | Phase 4    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-09 |            | 2026-05-09 |            | 待迁移 `admin ca`                                                                   |
| 4.3  | Phase 4    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-09 |            | 2026-05-09 |            | 待迁移 `discover` 与 `admin discovery`                                              |
| 4.4  | Phase 4    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-09 |            | 2026-05-09 |            | 待完成 CA / Discovery 域单元/集成/e2e 三层测试适配、回归与文档切换                  |
| 5.1  | Phase 5    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-09 |            | 2026-05-10 |            | 待完成旧入口移除与全工作区旧命令痕迹清零                                            |
| 5.2  | Phase 5    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-10 |            | 2026-05-10 |            | 待完成 `acps-cli` 单元/集成/e2e 三层测试迁移并验证全量测试通过                      |
| 5.3  | Phase 5    | planned  | GitHub Copilot | Explore 子代理 | 2026-05-10 |            | 2026-05-10 |            | 待完成终审、缺陷清零与冻结                                                          |

## 3. 审核记录

| 审核编号 | 步骤    | 审核轮次 | 审核日期   | 审核人         | 结论     | 待修复问题                                                                            |
| -------- | ------- | -------- | ---------- | -------------- | -------- | ------------------------------------------------------------------------------------- |
| REV-001  | 0.1-0.2 | round-1  | 2026-05-05 | Explore 子代理 | fixing   | 设计基线检查清单、`run-sync` 审核标准、审核责任链、证据归档位置和配置迁移矩阵不够具体 |
| REV-002  | 0.1-0.2 | round-2  | 2026-05-05 | Explore 子代理 | fixing   | Phase 0 冻结标准、产物形态、配置迁移验收方法、帮助 golden 维护规则仍需继续操作化      |
| REV-003  | 0.1-0.2 | round-3  | 2026-05-05 | Explore 子代理 | accepted | No blocking findings；Phase 0 的冻结标准已足够机械化，可以进入 Phase 1                |

## 4. 缺陷台账

| 缺陷编号 | 步骤 | 严重级别 | 当前状态 | 根本原因             | 修复责任人     | 计划修复日期 | 实际修复日期 | 复审人         | 问题描述                                                 | 修复说明                                                        |
| -------- | ---- | -------- | -------- | -------------------- | -------------- | ------------ | ------------ | -------------- | -------------------------------------------------------- | --------------------------------------------------------------- |
| DEF-001  | 0.1  | major    | closed   | 基线检查清单不够具体 | GitHub Copilot | 2026-05-05   | 2026-05-05   | Explore 子代理 | Phase 0.1 缺少可机械执行的设计冻结检查清单               | 已在计划第 7.1.1 节补齐对应设计章节与验证标准                   |
| DEF-002  | 0.2  | major    | closed   | 审核责任链不清       | GitHub Copilot | 2026-05-05   | 2026-05-05   | Explore 子代理 | 实施、独立审核、复审和人工确认的边界不明确               | 已在计划第 4.3、4.4 节和台账使用规则中写死责任链                |
| DEF-003  | 0.2  | major    | closed   | 证据归档规则缺失     | GitHub Copilot | 2026-05-05   | 2026-05-05   | Explore 子代理 | 每步证据存放位置、帮助 golden 基线位置和台账摘要边界不清 | 已在计划第 4.2 节和 7.5.2 节补齐                                |
| DEF-004  | 0.1  | major    | closed   | Phase 0 冻结标准模糊 | GitHub Copilot | 2026-05-05   | 2026-05-05   | Explore 子代理 | 无法客观判定 Phase 0 何时真正 accepted / frozen          | 已在计划第 7.1.2 节与台账第 8.1 节写死条件                      |
| DEF-005  | 2.1  | major    | closed   | 配置迁移验收标准缺失 | GitHub Copilot | 2026-05-05   | 2026-05-05   | Explore 子代理 | 配置迁移矩阵存在，但缺少 PASS / FAIL 验收动作            | 已在计划第 7.3.1 节补充代码搜索、测试、帮助与示例配置的验收动作 |

## 5. 决策台账

| 决策编号 | 日期       | 主题               | 当前状态 | 决策人                              | 决策依据                        | 备选方案                      | 结论                                                                                                   | 相关步骤  | 影响范围   |
| -------- | ---------- | ------------------ | -------- | ----------------------------------- | ------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------ | --------- | ---------- |
| DEC-001  | 2026-05-05 | CLI 分层模型       | active   | 用户 / GitHub Copilot               | `unified-cli-design.md`         | 引入 `internal` 独立级别      | CLI 只保留 `user` 与 `admin` 两级语义                                                                  | 全部步骤  | `acps-cli` |
| DEC-002  | 2026-05-05 | 切换策略           | active   | 用户 / GitHub Copilot               | `unified-cli-design.md`         | 旧四个 script 保留兼容壳      | 采用一次性切换，不保留旧入口兼容壳和旧配置键运行时兼容逻辑                                             | Phase 1-5 | `acps-cli` |
| DEC-003  | 2026-05-05 | `agent save` 语义  | active   | 用户 / GitHub Copilot               | `unified-cli-design.md`         | `agent upsert`、`agent apply` | `agent save` 表示创建或更新草稿；已提交审核的 Agent 不允许再次保存                                     | Phase 3   | `acps-cli` |
| DEC-004  | 2026-05-05 | Discovery 高阶入口 | active   | 用户 / GitHub Copilot               | `unified-cli-design.md`         | 直接暴露单一 `sync`           | 使用 `admin discovery run-sync` 作为单次同步编排器，`admin discovery dsp.*` 保留细粒度控制面           | Phase 4   | `acps-cli` |
| DEC-005  | 2026-05-05 | 帮助 golden 形式   | active   | GitHub Copilot / Explore 子代理复审 | `unified-cli-execution-plan.md` | 自动生成但不入库              | 使用仓内维护的纯文本 golden 文件，并由测试比较帮助输出                                                 | Phase 1-5 | `acps-cli` |
| DEC-006  | 2026-05-05 | 阻塞登记权限       | active   | GitHub Copilot / Explore 子代理复审 | `unified-cli-execution-plan.md` | 仅项目负责人可先登记阻塞      | 当实施人与审核人都确认存在服务端契约阻塞时，实施人可先登记 `blocked`，项目负责人后续确认扩范围或改设计 | 全部步骤  | `acps-cli` |

## 6. 风险台账

| 风险编号 | 当前状态 | 优先级 | 责任人         | 计划缓解时间 | 风险描述                                                                                                      | 影响范围                                           | 当前应对                                                                   |
| -------- | -------- | ------ | -------------- | ------------ | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------- |
| RISK-001 | open     | high   | GitHub Copilot | Phase 2 前   | 新配置键与新认证矩阵切换后，`acps-cli` 的单元、集成、e2e 测试夹具和联调脚本可能大面积失效                     | `tests/`、`docs/`、脚本                            | 在 Phase 2.4、3.4、4.4 和 Phase 5.2 分层迁移并集中回归，把旧键残留视为缺陷 |
| RISK-002 | open     | high   | GitHub Copilot | Phase 4 前   | `run-sync` 的高阶编排如果边界没锁死，容易越界调用破坏性 `dsp` 子命令                                          | `acps_cli/discovery/`                              | 在 Phase 4.3 用帮助文案、实现与测试同时锁定边界                            |
| RISK-003 | open     | high   | GitHub Copilot | Phase 5 前   | 删除旧 console script 后，工作区其它项目中的 README、设计文档、脚本和测试若仍残留旧命令，会导致一次性切换失败 | 整个工作区受版本控制项目文件                       | 在 Phase 5.1 做工作区级全文搜索和修复，在 Phase 5.2 用测试与终审再次确认   |
| RISK-004 | open     | medium | GitHub Copilot | 全程         | 统一 CLI 实现过程中，如果发现服务端现有契约无法支持设计，会超出本计划仓内边界                                 | `registry-server`、`ca-server`、`discovery-server` | 一旦发现即登记阻塞项，不在本计划内直接扩仓修改                             |

## 7. 当前阻塞

当前无显式阻塞。

## 8. 下一动作

1. 启动 1.1，开始统一入口与根命令树骨架实现。
2. 在后续 3.4、4.4、5.1、5.2 的实施与审核中，显式保留三层测试迁移证据和工作区旧命令清理证据。

## 8.1 Phase 0 退出条件检查清单

| 归属步骤 | 检查项                                                                                    | 验证方法                                         | 当前状态 | 备注               |
| -------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------ | -------- | ------------------ |
| 0.1      | 设计基线检查清单已完整覆盖命令树、状态机、认证矩阵、配置优先级、`run-sync` 边界和切换策略 | 对照 `unified-cli-execution-plan.md` 第 7.1.1 节 | closed   | 第三轮独立复审通过 |
| 0.1      | Phase 0 产物形态和冻结标准已明确                                                          | 对照 `unified-cli-execution-plan.md` 第 7.1.2 节 | closed   | 第三轮独立复审通过 |
| 0.2      | 计划、台账、缺陷、决策、风险、下一动作字段足以支撑长时间自动执行                          | 复核本台账的使用规则、执行总表和各台账分区       | closed   | 第三轮独立复审通过 |
| 0.2      | 审核责任链、复审责任和人工确认时机已明确                                                  | 复核计划第 4.3、4.4 节与本台账使用规则           | closed   | 第三轮独立复审通过 |

## 9. 阻塞项模板

| 阻塞编号 | 所属步骤 | 阻塞描述 | 根本原因 | 阻塞责任方 | 预计解除时间 | 当前状态 |
| -------- | -------- | -------- | -------- | ---------- | ------------ | -------- |
| BLK-001  |          |          |          |            |              | open     |
