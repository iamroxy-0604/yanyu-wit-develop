# 跨仓测试清理与重构执行计划

状态：Draft

计划日期：2026-05-05

来源：基于 `registry-server`、`ca-server`、`discovery-server`、`acps-cli` 当前测试分层现状和联调边界分析制定，用于指导四个仓库完成测试清理、夹具重构、文档收口、告警修复与全绿验收。

相关文档：

- `registry-server/README.md`
- `ca-server/README.md`
- `discovery-server/README.md`
- `discovery-server/tests/README.md`
- `acps-cli/README.md`
- `acps-cli/docs/joint-e2e-design.md`

## 1. 计划目标

本计划的目标不是单纯把测试“能跑起来”，而是把四个仓库的测试分层、执行入口、环境准备、告警治理和 skip 语义统一收口。

本计划必须同时满足以下要求：

1. `registry-server`、`ca-server`、`discovery-server` 的 `integration` 测试不再依赖真实 sibling 服务进程，而是依赖本仓运行时、本地测试数据库和 fake peer / contract fixture。
2. 三个 server 仓库的 `e2e` 测试只覆盖 self-contained black-box 能力，不再承载跨服务联调链路。
3. 跨 `registry-server`、`ca-server`、`discovery-server` 的真实联调工作流统一收敛到 `acps-cli` 的 `e2e` 测试中。
4. 三个 server 仓库的 `tests/README` 必须明确写清：联调 e2e 不属于本仓，而属于 `acps-cli`。
5. `acps-cli/README.md` 必须详细说明本仓承担的联调测试范围、前置依赖、启动矩阵、标准入口和故障定位方式。
6. 所有项目现有测试告警都要进入治理范围，最终默认测试入口应无新增告警，且历史告警应尽可能清零。
7. 所有“前置条件不足型 skip”都要进入修复范围；目标不是简单 fail fast，而是把这些前置条件纳入 `just`、fixture、bootstrap、受管测试实例或测试数据准备流程中。
8. 最终只有少数已明确标记为未来工作的测试允许保留 `skip`；除此之外，其余测试应达到全绿。
9. 每个 Phase 内的每个工作项完成后，都必须立即进入独立审核；审核发现的问题必须在当前工作项或当前 Phase 内修复并复审通过后，才能继续推进。
10. 计划执行必须适应长时间自动推进；除非进入明确记录的 `blocked` 状态，否则不能在 Phase 中途停下，也不能把未闭环的问题直接带到后续 Phase。

## 2. 范围与边界

本计划覆盖以下四个仓库：

1. `registry-server`
2. `ca-server`
3. `discovery-server`
4. `acps-cli`

本计划包含以下工作面：

1. `tests/` 下的 fixture、helper、skip 逻辑、warning 触发点、测试分层和测试入口。
2. `README.md` 与 `tests/README.md` 中的测试说明、联调说明和运行说明。
3. `Justfile`、测试启动脚本和 bootstrap 逻辑中与测试环境准备相关的部分。
4. 与测试直接相关的最小代码改造，包括 fake peer 注入点、contract fixture、测试态受管实例和测试数据准备逻辑。

本计划默认不主动扩大到以下范围：

1. 不为了“测试方便”而重写大段业务逻辑。
2. 不把尚未实现的产品能力伪装成测试问题强行消化。
3. 不把联调 e2e 再拆回 server 仓库。

## 3. 目标分层模型

四个仓库统一采用以下测试分层语义：

### 3.1 unit

1. 只测纯逻辑或最小边界逻辑。
2. 不依赖真实数据库。
3. 不依赖真实网络服务。
4. 不允许以“启动本地某个 sibling 仓库”为前置条件。

### 3.2 integration

1. 测试“本服务 + 本地测试数据库 + fake peer / contract fixture”。
2. 允许使用真实 ORM、真实依赖注入、真实请求路径。
3. 不允许依赖真实 sibling 服务进程。
4. 外部协议边界必须通过 stub、mock transport、ASGI fake app、协议夹具或可替换 client 来表达。

### 3.3 server e2e

1. 测试“本服务受管启动后的黑盒行为”。
2. 可以依赖本仓测试数据库、证书材料、测试配置和受管启动脚本。
3. 不应该要求真实 sibling 服务进程同时存在。
4. 如需验证外部交互，优先使用 fake upstream/fake downstream，而不是跨仓联调。

### 3.4 cli integration

1. 测试 CLI 参数解析、配置加载、上下文装配、输出格式和单服务命令契约。
2. 可以命中单个真实服务，也可以命中受控 fake service。
3. 不承担多服务业务链路编排。

### 3.5 cli e2e

1. 测试真实跨服务用户旅程和系统联调编排。
2. 覆盖 `registry-server`、`ca-server`、`discovery-server` 之间真实交互。
3. 承担联调 smoke、主链路回归、跨服务状态传播和运行时协作验证。

## 4. 硬性验收标准

本计划完成时必须满足以下硬性标准：

1. 三个 server 仓库各自存在 `tests/README.md`，并明确声明跨服务联调 e2e 在 `acps-cli`。
2. `acps-cli/README.md` 明确说明联调测试边界、服务启动顺序、前置环境、跳过策略和问题定位入口。
3. 三个 server 仓库的 `integration` 测试不再因 sibling 服务未启动而 `skip`。
4. 三个 server 仓库的 `e2e` 测试不再因 sibling 服务未启动而 `skip`。
5. `acps-cli` 中现有的“缺服务、缺 token、缺 cert、缺 .env、缺 secondary 实例、缺测试库数据”一类前置条件型 `skip`，必须尽量改造成自动准备。
6. 默认测试入口下，只允许保留少数明确标注“未来工作”的 `skip`。
7. 四个仓库的默认测试入口必须覆盖 warning 修复工作；计划完成时不得遗留未解释的 warning。
8. 四个仓库在标准测试入口下应达到“除未来工作型 skip 外，其余全绿”。
9. 每个 Phase 都必须留下独立审核记录、问题清单、修复动作和复审结论，不允许只记录“已完成”而没有审核证据。
10. 计划执行过程必须可恢复；中断后应从最近的 `ready`、`in-progress`、`in-review`、`fixing` 或 `blocked` Phase 继续，而不是重新自由选择范围。

## 5. 关键治理原则

### 5.1 skip 治理原则

1. `skip` 只允许用于未来工作、尚未实现能力或明确不支持的平台路径。
2. 因环境未准备、服务未启动、数据库未迁移、测试数据未 seed、证书未生成、内部 token 未对齐等原因导致的 `skip`，都视为待修复问题。
3. 对这类前置条件，优先通过 `just test bootstrap`、受管测试实例、fixture、临时配置生成、测试证书生成、测试数据 seed、受控 secondary 进程管理等方式自动准备。
4. 如果短期内无法自动准备，必须先登记为明确阻塞项，而不能长期保留为默认 `skip`。

### 5.2 warning 治理原则

1. warning 不是“可忽略噪音”，而是测试健康度的一部分。
2. 本计划要求逐仓建立 warning 清单，区分为测试代码问题、fixture 问题、依赖版本兼容问题和框架弃用问题。
3. 能在测试或 fixture 层修复的 warning，必须优先在本计划中修复。
4. 依赖升级或框架版本切换导致的 warning，如果暂时不能立即修复，也必须留下清晰解释和后续动作，不能长期无台账漂浮。

### 5.3 文档优先原则

1. 先把测试边界写清楚，再改具体 fixture 和用例。
2. 文档要先消除“本仓到底该测什么”的歧义，再进入代码重构。

### 5.4 独立审核与问题修复原则

1. 每个 Phase 的每个工作项完成后，必须立即进入一次局部独立审核，而不是等整个 Phase 结束后一次性回看。
2. 独立审核必须由非主要实施人执行，至少覆盖以下维度：范围纪律、测试分层一致性、warning/skip 治理结果、文档同步、验证结果真实性。
3. 审核发现的问题统一按 `critical`、`major`、`minor` 分级。
4. `critical` 和 `major` 问题必须在当前工作项或当前 Phase 内修复并复审通过；`minor` 问题原则上也应在当前 Phase 内闭环，只有不影响主链路推进时才允许挂账后移。
5. 每个 Phase 的全部工作项都通过局部审核后，必须再执行一次 Phase 总复审；总复审未通过时，仍然留在当前 Phase 修复，不得带病流转。
6. 每个 Phase 的验收结论必须明确写出：通过、退回修复或阻塞，以及下一动作。

### 5.5 长时间自动执行原则

1. 本计划统一采用 `planned`、`ready`、`in-progress`、`in-review`、`fixing`、`accepted`、`blocked`、`frozen` 这组执行状态。
2. 任一 Phase 一旦进入 `accepted`，必须立即把下一个未完成 Phase 切到 `ready` 或 `in-progress`，不能停在“稍后处理”状态。
3. 如果审核发现问题，执行流必须留在当前工作项或当前 Phase 修复，直到复审通过为止。
4. 如果当前 Phase 内存在多个子任务，遇到局部阻碍时，应先切换到同 Phase 其它未阻塞子任务，保持推进不断档。
5. 只有当外部依赖、跨仓契约缺口、环境能力缺失或工具限制导致确实无法继续时，才允许把当前步骤标记为 `blocked`。
6. 阻碍一旦解除，执行流必须从最近的 `blocked` 或 `fixing` 位置继续，而不是重新规划全局。
7. 整个执行过程中必须持续记录当前状态、问题清单、修复结果、复审结论和下一动作，确保长时间自动执行可以随时恢复。

## 6. 执行阶段总览

| Phase   | 目标                             | 主要仓库           | 验收结果                                        |
| ------- | -------------------------------- | ------------------ | ----------------------------------------------- |
| Phase 0 | 冻结分层边界与问题清单           | 四仓               | 测试边界、warning 清单、skip 清单、迁移矩阵明确 |
| Phase 1 | 完成文档收口                     | 四仓               | server `tests/README` 和 `acps-cli/README` 到位 |
| Phase 2 | 清理 `registry-server` 测试边界  | `registry-server`  | integration/e2e 自闭环，warning 与 skip 收敛    |
| Phase 3 | 清理 `ca-server` 测试边界        | `ca-server`        | integration/e2e 自闭环，warning 与 skip 收敛    |
| Phase 4 | 清理 `discovery-server` 测试边界 | `discovery-server` | integration/e2e 自闭环，warning 与 skip 收敛    |
| Phase 5 | 归拢 `acps-cli` 联调测试         | `acps-cli`         | 联调测试边界清晰，前置条件可自动准备            |
| Phase 6 | 告警与 skip 总收口               | 四仓               | 除未来工作型 skip 外，其余全绿                  |

## 7. 分阶段执行计划

### 7.1 Phase 0：冻结边界与问题清单

#### 7.1.1 目标

建立统一测试边界，形成后续改造的唯一输入。

#### 7.1.2 工作项

1. 为四个仓库分别建立测试分层矩阵，逐项标记哪些用例应保留、下沉、上移或删除。
2. 为四个仓库分别建立 warning 清单，记录 warning 类别、触发文件、复现命令、预期修复方式。
3. 为四个仓库分别建立 skip 清单，区分“未来工作型 skip”和“前置条件型 skip”。
4. 明确 `acps-cli` 中必须承接的跨服务主链路清单。

#### 7.1.3 验收标准

1. 每个仓库都有一份清晰的问题清单。
2. 每个 skip 都已完成分类。
3. 后续阶段不再争论“某个联调 e2e 应属于哪个仓库”。

#### 7.1.4 独立审核与问题修复

1. `7.1.2` 的每一项工作项完成后，必须立即由独立审核人检查清单是否完整、分类是否准确、是否把前置条件型 `skip` 误归为未来工作。
2. 如果发现 warning 清单、skip 清单或迁移矩阵有遗漏，必须在当前 Phase 立即补齐，并重新复审该工作项。
3. 全部工作项完成后，执行一次 Phase 总复审，确认 Phase 0 产物已经足以作为后续改造的唯一输入。
4. Phase 0 只有在“问题清单完整、分类稳定、无关键遗漏”时才能进入 `accepted`；否则继续停留在当前 Phase 修复。

### 7.2 Phase 1：文档收口

#### 7.2.1 目标

把测试边界先写清楚，避免后续代码改造再次漂移。

#### 7.2.2 工作项

1. 新增 `registry-server/tests/README.md`。
2. 新增 `ca-server/tests/README.md`。
3. 更新 `discovery-server/tests/README.md`。
4. 更新三个 server 仓库主 README 的测试章节，补充“本仓不承载跨服务联调 e2e”的说明。
5. 扩写 `acps-cli/README.md` 中的联调与测试章节，至少覆盖：
   - 联调测试为什么归属于 `acps-cli`
   - 三个服务的启动矩阵和标准入口
   - `just doctor`、`just test bootstrap`、`just test integration`、`just test e2e` 的角色边界
   - 哪些场景属于 CLI integration
   - 哪些场景属于 CLI e2e
   - 哪些 skip 仍是未来工作，为什么保留

#### 7.2.3 验收标准

1. 三个 server 的 `tests/README` 都已存在并清楚声明联调边界。
2. `acps-cli/README.md` 对联调测试说明足够详细，可以单独指导执行。

#### 7.2.4 独立审核与问题修复

1. `7.2.2` 中的每项文档改动完成后，必须立即由独立审核人核对文档边界是否一致、术语是否统一、是否把联调 e2e 的归属写清楚。
2. 如果审核发现 README、`tests/README`、Just 命令说明或联调入口存在互相矛盾的表述，必须在当前 Phase 立即修复并复审。
3. 全部文档项完成后，执行一次 Phase 总复审，确认四个仓库对测试边界的描述已完全对齐。
4. Phase 1 只有在“文档可独立指导执行、且不会把联调测试误导回 server 仓库”时才能进入下一 Phase。

### 7.3 Phase 2：`registry-server` 清理

#### 7.3.1 目标

把 `registry-server` 的测试收口为“本服务自闭环 + fake CA peer”。

#### 7.3.2 工作项

1. 清理 `tests/conftest.py` 和 integration fixture 中对真实 CA 服务的隐式依赖。
2. 为 CA 回调、吊销通知、EAB 或其它跨边界调用引入 fake peer / contract fixture。
3. 审查 `tests/e2e/`，把跨服务编排链路迁出，只保留 public/mTLS、认证、Agent 生命周期、Webhook CRUD 等本服务黑盒能力。
4. 让 `just test e2e` 继续通过受管临时实例运行，但不再要求真实 `ca-server` 同时启动。
5. 修复 `registry-server` 测试过程中的 warning。
6. 把环境前置型 skip 全部转成自动准备或明确阻塞项。

#### 7.3.3 验收标准

1. `registry-server` integration 不再依赖 `localhost:9003` 上真实 CA 进程。
2. `registry-server` e2e 在标准入口下不再因缺少 sibling 服务而 skip。
3. `registry-server` 默认测试入口无新增 warning。

#### 7.3.4 独立审核与问题修复

1. `7.3.2` 的每项工作项完成后，必须立即由独立审核人检查是否仍存在真实 CA 依赖、是否仍有前置条件型 `skip`、是否仍有未治理 warning。
2. 如果审核发现 integration 仍命中真实 `ca-server`，或 e2e 仍依赖 sibling 服务存活，必须在当前 Phase 立刻修复 fixture、fake peer 或受管启动逻辑，并重新验证。
3. 全部工作项完成后，执行一次 Phase 总复审，确认 `registry-server` 的 integration/e2e 已真正收口为自闭环。
4. 只有在 `registry-server` 的标准测试入口达到“无 sibling 依赖、无未解释 warning、无前置条件型 skip”时，Phase 2 才能进入 `accepted`。

### 7.4 Phase 3：`ca-server` 清理

#### 7.4.1 目标

把 `ca-server` 的测试收口为“本服务自闭环 + fake Registry peer”。

#### 7.4.2 工作项

1. 清理 `tests/conftest.py` 和 integration fixture 中对真实 Registry 服务的隐式依赖。
2. 为 Registry 身份校验、内部 token、EAB 验证、外部账户绑定等调用引入 fake registry verifier 或 stub transport。
3. 审查 `tests/e2e/`，保留 ACME、证书 CRUD、CRL、OCSP、admin-only 管理能力等本服务黑盒链路。
4. 收敛当前依赖 admin token、测试证书、测试库 schema 的前置条件，让它们进入 bootstrap 或 fixture 自动准备。
5. 修复 `ca-server` 测试过程中的 warning。
6. 清理所有前置条件型 skip。

#### 7.4.3 验收标准

1. `ca-server` integration 不再依赖真实 `registry-server` 进程。
2. `ca-server` e2e 在标准入口下可独立运行。
3. `ca-server` 默认测试入口无新增 warning。

#### 7.4.4 独立审核与问题修复

1. `7.4.2` 的每项工作项完成后，必须立即由独立审核人检查是否仍依赖真实 Registry 进程、admin token/证书/schema 前置条件是否已自动准备、warning 是否已收敛。
2. 如果审核发现某个 ACME、CRL、OCSP 或 admin-only 链路仍然通过 `skip` 规避前置条件，而不是自动准备，必须在当前 Phase 修复 bootstrap 或 fixture。
3. 全部工作项完成后，执行一次 Phase 总复审，确认 `ca-server` 的 integration/e2e 已真正实现自闭环。
4. 只有在 `ca-server` 的标准测试入口达到“无真实 Registry 依赖、无未解释 warning、无前置条件型 skip”时，Phase 3 才能进入下一 Phase。

### 7.5 Phase 4：`discovery-server` 清理

#### 7.5.1 目标

以现有结构为基础，把 `discovery-server` 进一步收口为“本服务自闭环 + fake DSP/Registry upstream”。

#### 7.5.2 工作项

1. 保持 integration 的 ASGI + test DB 路线，继续清理任何隐含的 sibling 依赖。
2. 审查 `tests/e2e/` 中所有需要 secondary 实例、上游同步、forwarder、fallback 的场景，区分哪些属于本仓黑盒协议验证，哪些应迁到 `acps-cli`。
3. 对仍需保留在本仓的 discovery e2e，优先使用 fake DSP/fake upstream endpoint，而不是依赖真实 `registry-server`。
4. 对应 warning 全部进入修复范围。
5. 对前置条件型 skip 进行收口，确保 `just test e2e` 能受管准备本仓所需实例、端口、测试库和最小运行配置。

#### 7.5.3 验收标准

1. `discovery-server` e2e 不再依赖 sibling 仓库环境文件。
2. 真实跨服务 forwarder / webhook / 410 fallback 主链路不再留在 server 仓库。
3. `discovery-server` 默认测试入口无新增 warning。

#### 7.5.4 独立审核与问题修复

1. `7.5.2` 的每项工作项完成后，必须立即由独立审核人检查 discovery e2e 是否仍然依赖 sibling 仓库环境文件、真实上游服务或未受管的 secondary 实例。
2. 如果审核发现仍有跨服务主链路滞留在 `discovery-server`，或仍有前置条件型 `skip` 依赖手工准备，必须在当前 Phase 立即迁移或补足自动准备逻辑。
3. 全部工作项完成后，执行一次 Phase 总复审，确认 `discovery-server` 只保留本仓 black-box 与 fake upstream 验证。
4. 只有在 `discovery-server` 的标准测试入口达到“无 sibling 环境文件依赖、无未解释 warning、无错误归属的联调 e2e”时，Phase 4 才能进入下一 Phase。

### 7.6 Phase 5：`acps-cli` 联调测试归拢

#### 7.6.1 目标

让 `acps-cli` 成为唯一的跨服务联调测试承载层，并把前置条件型 skip 收口为自动准备。

#### 7.6.2 工作项

1. 重新梳理 `tests/integration/` 与 `tests/e2e/` 的职责边界。
2. `integration` 只保留 CLI 本身的参数、配置、输出和单服务契约验证。
3. `e2e` 统一承接以下真实跨服务场景：
   - ATR / EAB / 证书申请主链路
   - CA 生命周期与状态传播
   - discovery snapshot / incremental / webhook / runtime 协作
   - discovery snapshot / incremental / webhook / 410 恢复等跨服务工作流
4. 清理现有依赖 `.env`、缺 token、缺 cert、缺 secondary 实例、缺 seed 数据的 `skip`。
5. 把这些前置条件转为以下一种或多种自动准备手段：
   - `just test bootstrap` 生成共享配置和测试材料
   - `just doctor` 补做前置检查并输出可执行修复动作
   - fixture 受管启动 secondary instance
   - fixture 自动生成临时 cert / token / config
   - fixture 自动准备测试数据库初始数据
   - 共享脚本统一启动/停止跨服务联调拓扑
6. 对 `acps-cli` 测试 warning 全部进入治理范围。
7. 把保留的 `skip` 限定到真正的未来工作，如尚未实现的多实例 discovery forwarder/fallback 联调与 fanout 能力。

#### 7.6.3 验收标准

1. `acps-cli` 成为唯一承载跨服务联调 e2e 的仓库。
2. `acps-cli` 的默认联调测试入口不再因为常见前置条件缺失而大量 skip。
3. `acps-cli` 默认测试入口无新增 warning。

#### 7.6.4 独立审核与问题修复

1. `7.6.2` 的每项工作项完成后，必须立即由独立审核人检查 CLI integration/e2e 边界是否清晰、跨服务主链路是否都已归位、前置条件型 `skip` 是否真的转成自动准备。
2. 如果审核发现仍依赖手工 `.env`、手工 token、手工 cert、手工 secondary 实例或手工 seed 数据，必须在当前 Phase 继续补齐 bootstrap、fixture 或共享脚本，而不是把问题后移。
3. 全部工作项完成后，执行一次 Phase 总复审，确认 `acps-cli` 已成为唯一联调 e2e 承载层。
4. 只有在 `acps-cli` 的标准测试入口达到“跨服务主链路完整、前置条件自动准备、无未解释 warning、仅未来工作型 skip 保留”时，Phase 5 才能进入下一 Phase。

### 7.7 Phase 6：告警与 skip 总收口

#### 7.7.1 目标

实现“除未来工作型 skip 外，其余全绿”。

#### 7.7.2 工作项

1. 逐仓运行标准测试入口，汇总 remaining warning 与 remaining skip。
2. 对 remaining warning 逐个确认是否已修复、是否仍可复现、是否需要额外代码改造。
3. 对 remaining skip 逐个确认是否属于未来工作。
4. 如果某个 skip 仍然是前置条件不足型，就继续回到前一阶段补足自动准备能力。
5. 必要时把 warning 纳入更严格的 gate，例如在合适的测试入口上启用更严格的 warning 检查。

#### 7.7.3 验收标准

1. 四个仓库剩余 skip 都有明确理由，且均属于未来工作。
2. 四个仓库默认测试入口没有未解释 warning。
3. 四个仓库标准入口达到“除未来工作型 skip 外，其余全绿”。

#### 7.7.4 独立审核与问题修复

1. `7.7.2` 的每项收口工作完成后，必须立即由独立审核人检查剩余 warning 和剩余 `skip` 是否已经全部被正确解释和正确分类。
2. 如果审核发现还有前置条件型 `skip`、未复现但无解释的 warning、或被错误保留的历史噪音，必须回到对应 Phase 继续修复，不能直接宣布完成。
3. 全部仓库完成总收口后，执行一次最终总复审，确认计划完成定义中的每一项条件都已满足。
4. Phase 6 只有在“除未来工作型 `skip` 外，其余全绿”被复审确认后，才能进入 `accepted` 并冻结计划。

## 8. 各仓默认验证入口

### 8.1 `registry-server`

1. `just test unit`
2. `just test integration`
3. `just test e2e`
4. `just test`

### 8.2 `ca-server`

1. `just test unit`
2. `just test integration`
3. `just test e2e`
4. `just test`

### 8.3 `discovery-server`

1. `just test unit`
2. `just test integration`
3. `just test e2e`
4. `just test all`

### 8.4 `acps-cli`

1. `just test unit`
2. `just test integration`
3. `just test e2e`
4. `just test`

## 9. 计划完成定义

只有当以下条件同时满足时，本计划才算完成：

1. 三个 server 仓库的 `tests/README` 已完成并被主 README 正确引用或语义对齐。
2. `acps-cli/README.md` 已明确承担联调 e2e 的角色。
3. 三个 server 仓库的 integration/e2e 已完成边界清理。
4. `acps-cli` 已完成联调测试归拢。
5. warning 修复工作已纳入并完成。
6. 前置条件型 skip 已转成自动准备，或者被彻底消除。
7. 除未来工作型 skip 外，其余测试全绿。

## 10. 下一步执行顺序

建议按以下顺序实施，避免返工：

1. Phase 1：先完成四仓文档收口。
2. Phase 2：优先清理 `registry-server`。
3. Phase 3：再清理 `ca-server`。
4. Phase 4：以 `discovery-server` 当前结构为模板完成收口。
5. Phase 5：最后归拢 `acps-cli` 联调测试和自动准备逻辑。
6. Phase 6：做全仓 warning / skip 总收口和全量验证。
7. 每个 Phase 内的每个工作项完成后，立刻进入独立审核和问题修复闭环；复审通过后无缝推进下一工作项或下一 Phase，只有进入 `blocked` 才允许暂停。
