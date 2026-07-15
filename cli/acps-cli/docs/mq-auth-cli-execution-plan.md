# acps-cli mq-auth-server 客户端功能实施计划

状态：Active

计划日期：2026-05-06

来源：基于 `acps-cli/docs/mq-auth-cli.md` 制定，用于指导 `acps-cli` 实现针对 mq-auth-server 的客户端命令树，并支撑长时间自动执行、独立审核和缺陷闭环。

相关文档：

- 设计输入：`acps-cli/docs/mq-auth-cli.md`
- 执行台账：`acps-cli/docs/mq-auth-cli-execution-ledger.md`
- 统一 CLI 设计：`acps-cli/docs/unified-cli-design.md`
- 联调 e2e 设计：`acps-cli/docs/joint-e2e-design.md`

---

## 1. 计划目标

本计划将设计文档拆解为可持续推进、可独立审核、可逐步冻结的实施步骤，覆盖：

1. `acps_cli/mq/` 模块骨架（config、client、group 命令、auth-probe 命令）
2. `[mq]` 配置节加载（`MqConfig` 数据类）
3. `acps_cli/main.py` 中注册 `admin_mq_group`
4. `admin mq health`、`admin mq group`（4 个子命令）、`admin mq auth-probe`（4 个子命令）
5. unit / integration / e2e 三层测试
6. 文档收口（`unified-cli-design.md`、`joint-e2e-execution-ledger.md`）

本计划不覆盖 mq-auth-server 服务端代码的修改；如需服务端契约变更，必须登记为阻塞项，不直接在本计划范围内扩展。

---

## 2. 范围与边界

**本计划修改范围限定在 `acps-cli` 仓内**：

- `acps_cli/mq/`（新建）
- `acps_cli/main.py`（注册 `admin_mq_group`）
- `acps-cli.toml`（新增 `[mq]` 示例节）
- `tests/unit/`（新增 mq 模块 unit 测试）
- `tests/integration/test_mq_auth_group_workflow.py`（新建）
- `tests/integration/conftest.py`（扩展 mq fixture）
- `tests/e2e/test_mq_auth_provision_workflow.py`（新建）
- `tests/e2e/conftest.py`（扩展 mq fixture）
- `tests/_local_services.py`（扩展 mq-auth-server LocalServiceSpec）
- `docs/unified-cli-design.md`（命令树更新）
- `docs/joint-e2e-execution-ledger.md`（补充台账条目）

**本计划不直接修改**：

- `mq-auth-server/` 仓内任何文件（服务端代码、路由、ACS）
- `registry-server/`、`ca-server/`、`discovery-server/` 服务端契约
- `acps-sdk/` 公共库

---

## 3. 执行原则

1. **先收口契约，再改代码**。凡涉及命令命名、配置键名、`--json` schema 的修改，必须先与 `mq-auth-cli.md` 保持一致，再进入代码实现。
2. **单步收口，串行推进**。当前步骤未通过独立审核前，不开启相邻下游步骤。
3. **审核独立于实施**。`Explore` 子代理负责只读独立审查，不替代实施步骤修复代码。
4. **问题必须在本步闭环**。审核发现的 `critical` / `major` 问题必须在当前步骤修复并复审通过，不能带病流转。
5. **证据先于结论**。每一步都必须留下实现说明、验证结果、审核记录。
6. **自动推进不中断**。某一步 `accepted` 后必须立即推进下一步，除非有登记在案的真实阻塞。
7. **两条路径并行存在**。集成测试的快速路径（外部实例）与自动托管路径（本地子进程）都必须通过，不能只通过一条。

---

## 4. 过程控制模型

### 4.1 步骤状态机

每个步骤只能处于以下状态之一：

| 状态          | 含义                         | 是否允许进入下一步 |
| ------------- | ---------------------------- | ------------------ |
| `planned`     | 已定义范围，但尚未开始       | 否                 |
| `ready`       | 输入齐备，允许开始实施       | 否                 |
| `in-progress` | 正在实施                     | 否                 |
| `in-review`   | 已提交独立审核               | 否                 |
| `fixing`      | 正在修复审核发现的问题       | 否                 |
| `accepted`    | 审核通过，当前步完成         | 是                 |
| `blocked`     | 受外部依赖或真实契约缺口阻塞 | 否                 |
| `frozen`      | 已验收并冻结，不再修改       | 是                 |

状态流转规则：

1. `planned → ready`：本步输入齐备，范围与审核人明确。
2. `ready → in-progress`：实施人开始修改代码或文档。
3. `in-progress → in-review`：实现完成，附带本步证据。
4. `in-review → fixing`：审核发现 `critical` 或 `major` 问题。
5. `fixing → in-review`：问题修复完成，重新提交审核。
6. `in-review → accepted`：审核通过，不存在未关闭的 `critical` / `major` 问题。
7. `accepted → frozen`：下一步启动前冻结本步产物。
8. 任意状态均可进入 `blocked`，但必须记录阻塞原因和解除条件。

### 4.2 每步必备产物

每一步进入独立审核前，至少准备以下产物：

1. **范围说明**：本步改什么，不改什么。
2. **变更清单**：涉及的文件、模块、命令、配置项和测试。
3. **验证证据**：命令执行结果、测试输出、人工检查结论。
4. **审核记录**：审核人、审核日期、审核意见、结论。
5. **缺陷清单**：发现的问题、严重级别、修复状态。
6. **修复结论**：所有问题已修复并复审通过，或显式记录阻塞说明。
7. **证据归档位置**：详细证据写入 `docs/mq-auth-cli-execution-evidence/phase-<n>/step-<x>.md`；台账内只保留摘要。

### 4.3 独立审核规则

每一步的审核动作至少覆盖以下四类维度：

1. **设计契约一致性**：命令树、帮助文案、配置键名、`--json` schema 是否与 `mq-auth-cli.md` 一致。
2. **行为正确性**：证书选择优先级、`--yes` 确认行为、mTLS 建立、错误处理是否正确。
3. **测试与回归**：成功路径、失败路径（401/403/502）、`--json` 输出格式是否被覆盖。
4. **范围纪律**：是否只修改本步范围，未把未批准的相邻步骤混入。

审核责任链：

- `GitHub Copilot` 负责实施和修复，不给自己的步骤签发独立审核通过结论。
- `Explore` 子代理负责只读独立审查、问题分级和复审结论。
- 项目负责人只在 Phase 冻结点进行人工确认。

### 4.4 缺陷修复闭环

1. **发现**：记录现象、影响范围、发现位置。
2. **分类**：设计偏差 / 实现缺陷 / 测试缺口 / 文档漂移。
3. **修复**：只在当前步骤范围内修复，不顺手扩展新范围。
4. **复审**：由原审核人确认问题已消除。
5. **关闭**：记录修复结论和关闭时间。

### 4.5 长时间自动推进规则

1. 当前步骤一旦 `accepted`，必须立即更新台账并启动下一步。
2. 审核发现问题时，优先留在当前步骤进入 `fixing`，修复后立刻回到 `in-review`。
3. 只有当问题依赖外部仓契约变更、环境不可用、凭证缺失或工具能力不足时，才允许置为 `blocked`。
4. 每轮执行至少同步更新一次台账（执行总表 + 审核记录 + 缺陷台账）。
5. 如果执行上下文中断，下一轮执行启动时必须先读取台账，从"最靠前的 `ready` / `in-progress` / `fixing` 步骤"恢复。

### 4.6 阻塞恢复规则

1. 每轮新的执行开始前，先检查所有 `blocked` 项是否满足解除条件。
2. 满足解除条件后，先在台账中记录解除依据，再把状态切回 `ready`。
3. 如果阻塞超过两个执行周期仍未解除，把原因写入风险台账。

---

## 5. 角色分工

| 角色           | 主要职责                                               |
| -------------- | ------------------------------------------------------ |
| GitHub Copilot | 实施代码变更、修复缺陷、更新台账                       |
| Explore 子代理 | 只读独立审查：检查设计一致性、行为正确性、测试覆盖     |
| 项目负责人     | Phase 冻结点人工确认；技术债决策；环境不可用时人工干预 |

---

## 6. Phase 划分总览

| Phase   | 内容                             | 前置条件         | 冻结标准                               |
| ------- | -------------------------------- | ---------------- | -------------------------------------- |
| Phase 0 | 基线确认与计划审核               | 无               | 设计文档已锁定，台账已创建             |
| Phase 1 | 模块骨架 + `[mq]` 配置 + health  | Phase 0 frozen   | health 命令可手工执行，unit 测试通过   |
| Phase 2 | `admin mq group` 四个子命令      | Phase 1 frozen   | group CRUD unit 测试通过               |
| Phase 3 | `admin mq auth-probe` 四个子命令 | Phase 1 frozen   | auth-probe unit 测试通过               |
| Phase 4 | unit 测试全量补齐                | Phase 2+3 frozen | 全部 mq unit 测试通过                  |
| Phase 5 | integration 测试                 | Phase 4 frozen   | integration 测试在两条路径下均通过     |
| Phase 6 | e2e 测试                         | Phase 5 frozen   | e2e 五阶段均通过                       |
| Phase 7 | 文档收口与最终冻结               | Phase 6 frozen   | unified-cli-design.md 已更新，全量回归 |

---

## 7. 各步骤详细说明

### Phase 0：基线确认与计划审核

#### 步骤 0.1 — 设计文档冻结检查

**范围**：只读检查，不修改任何代码文件。

**验收标准**（逐条机械化检查）：

1. `mq-auth-cli.md` 中 §2.1 命令树已包含所有命令（health、group 四个、auth-probe 四个）。
2. §2.6 `--json` schema 已为所有 9 个命令定义输出结构。
3. §3 `[mq]` 配置节已区分 `group_cert_file`（Leader 专属）与 `probe_cert_file`（任意 ACPs cert）。
4. §2.3 `group delete --yes` 行为已明确（非 TTY exit 1）。
5. §2.3 `group kick` 已标注 RabbitMQ Management API 依赖及 502/503 区分。
6. §5.2 integration 测试已明确快速路径（外部实例）与自动托管路径（本地子进程）两条路径。
7. §5.2 conftest 已区分 `mq_server_certs`（服务端证书）与 `leader_client_cert`（Leader 客户端证书）。
8. §6.1 环境变量表已区分 `MQ_SERVER_CERT_FILE`、`MQ_LEADER_CERT_FILE`、`MQ_PROBE_CERT_FILE` 三类。
9. `main.py` 注册路径已在 §2.5 中明确（`admin_mq_group` import + `_build_admin_group()` 修改点）。
10. `MqConfig` 数据类骨架已在 §3 中明确（`group_cert_file`、`probe_cert_file` 等字段）。

**退出条件**：以上 10 项全部满足则 `accepted`；否则记录缺陷并 `fixing`。

#### 步骤 0.2 — 计划与台账就绪检查

**范围**：只读检查。

**验收标准**：

1. `mq-auth-cli-execution-plan.md` 已存在且包含完整的 Phase 0–7 说明。
2. `mq-auth-cli-execution-ledger.md` 已存在且包含执行总表、审核记录、缺陷台账、决策台账、风险台账所有表格结构。
3. 执行总表中所有步骤初始状态均为 `planned`（Phase 0 为 `ready`）。
4. 证据归档目录 `docs/mq-auth-cli-execution-evidence/` 已创建占位。

**退出条件**：以上 4 项全部满足则 `accepted`。

**Phase 0 冻结标准**：步骤 0.1 和 0.2 均为 `accepted`，且两项审核均无 open `critical`/`major` 问题。

---

### Phase 1：模块骨架 + `[mq]` 配置 + health 命令

#### 步骤 1.1 — `acps_cli/mq/` 模块骨架

**范围**：新建以下文件，均可仅含骨架（pass / placeholder）：

- `acps_cli/mq/__init__.py`
- `acps_cli/mq/config.py`：`MqConfig` 数据类 + `from_toml()` 工厂方法
- `acps_cli/mq/client.py`：`MqAuthClient` 骨架（两个方法占位：`get(path)` 和 `post_form(path, data)`）
- `acps_cli/mq/unified.py`：`admin_mq_group` Click Group 骨架
- `acps_cli/mq/group_cmd.py`：文件占位
- `acps_cli/mq/auth_probe_cmd.py`：文件占位

**验证动作**：

```bash
cd acps-cli
uv run python -c "from acps_cli.mq.config import MqConfig; print('ok')"
uv run python -c "from acps_cli.mq.unified import admin_mq_group; print('ok')"
```

**审核检查点**：

- `MqConfig` 字段清单是否与 `mq-auth-cli.md §3` 一致（包含 `group_cert_file`、`probe_cert_file`、`ca_cert_file`、`timeout_seconds`）
- `from_toml()` 是否支持相对路径相对于 `config_dir` 解析
- 模块文件名与 §2.5 设计一致

#### 步骤 1.2 — `[mq]` 配置节集成

**范围**：

- 扩展 `acps_cli/shared/unified_config.py`（或对应的 config 桥接文件），使 `MqConfig.from_toml()` 能从 `RootCliRuntime.toml_data["mq"]` 加载
- 扩展 `acps-cli.toml` 示例文件，添加注释掉的 `[mq]` 节（与 §3 设计一致）

**验证动作**：

```bash
# 创建包含 [mq] 节的测试 toml，验证加载不报错
uv run python -c "
from acps_cli.shared.config import load_toml_config
from acps_cli.mq.config import MqConfig
from pathlib import Path
data, path = load_toml_config('acps-cli.toml')
cfg = MqConfig.from_toml(data.get('mq', {}), path.parent if path else None)
print(cfg)
"
```

**审核检查点**：

- `group_cert_file` 为 `None` 时不报错（group 命令届时强制要求 `--cert-file`）
- `probe_cert_file` 为 `None` 时不报错（回退逻辑由命令层处理）
- 所有路径字段：若为相对路径，必须相对于 `config_dir` 解析为绝对路径

#### 步骤 1.3 — `admin mq health` 命令实现

**范围**：

- `acps_cli/mq/unified.py`：实现 `health` 命令
- `MqAuthClient.get()` 方法实现（httpx mTLS GET，使用 `probe_cert` 相关配置）
- 支持 `--json` 输出（格式见 §2.6）

**验证动作**：

```bash
# 本地 mq-auth-server 未运行时的错误处理
acps-cli --config acps-cli.toml admin mq health
acps-cli --config acps-cli.toml admin mq health --json
```

**`--json` 输出 schema 验证**：确认输出为：

```json
{
  "group_api": { "status": "error", "detail": "..." },
  "auth_api": { "status": "error", "detail": "..." }
}
```

**审核检查点**：

- `probe_cert_file` 为 `None` 且 `[ca].certs_dir` 未配置时，给出可读错误提示而不是 traceback
- `--json` 时服务不可达应以 exit 0 返回 `status: error`，而非 exit 非零（`health` 的语义是报告状态，不是断言）
- 两个端口独立探测，一个失败不影响另一个的结果

#### 步骤 1.4 — `main.py` 注册

**范围**：

- `acps_cli/main.py`：添加 `from acps_cli.mq.unified import admin_mq_group` 导入，并在 `_build_admin_group()` 的子命令列表中添加 `admin_mq_group`

**验证动作**：

```bash
acps-cli --help | grep -A5 "admin"
acps-cli admin --help | grep mq
acps-cli admin mq --help
acps-cli admin mq health --help
```

**审核检查点**：

- `acps-cli admin --help` 中能看到 `mq` 子命令
- `acps-cli admin mq --help` 中能看到 `health`、`group`、`auth-probe`（即使后两者此时还只是占位）
- 不影响已有 `admin registry`、`admin ca`、`admin discovery` 命令

**Phase 1 冻结标准**：步骤 1.1–1.4 全部 `accepted`；`acps-cli admin mq health --json` 可执行；`uv run pytest tests/unit/` 全量通过（包含 mq unit 测试）。

---

### Phase 2：`admin mq group` 四个子命令

#### 步骤 2.1 — `group add-member` 与 `group remove-member`

**范围**：`acps_cli/mq/group_cmd.py` 实现 `add-member`（PUT）和 `remove-member`（DELETE）。

**证书选择优先级**（必须实现）：`--cert-file`/`--key-file` > `[mq].group_cert_file`/`[mq].group_key_file` > 报错（`group_cert_file` 未配置且命令行未传入则终止）。

**验证动作**：

```bash
acps-cli admin mq group add-member --help
acps-cli admin mq group remove-member --help
# 错误路径：未提供证书时的错误提示
acps-cli --config acps-cli.toml admin mq group add-member \
    --leader-aic AIC_TEST --group-id GRP1 --member-aic AIC_MEMBER
```

**审核检查点**：

- `--json` 输出格式与 §2.6 一致
- 403 响应（CN 不匹配）给出可读提示而非 HTTP 原始响应 dump
- 未提供 Leader 证书时报错信息明确指引用户使用 `--cert-file` 或配置 `group_cert_file`

#### 步骤 2.2 — `group delete`

**范围**：`acps_cli/mq/group_cmd.py` 实现 `delete`（DELETE 整个群组）。

**`--yes` 行为必须实现**：

- 有 `--yes`：直接执行，不提示
- 无 `--yes` + TTY：`click.confirm("This will delete all ACL for group <GROUP_ID>. Proceed?", abort=True)`
- 无 `--yes` + 非 TTY（CI）：打印错误提示并以 exit code 1 退出

**验证动作**：

```bash
# 非 TTY 环境测试（重定向输入）
echo "" | acps-cli admin mq group delete \
    --leader-aic AIC_TEST --group-id GRP1 2>&1; echo "exit: $?"
```

**审核检查点**：

- `--json` 模式下确认行为：`--json` + 无 `--yes` 时如何处理（建议：非 TTY 直接 exit 1，TTY 仍提示）
- 单元测试必须覆盖"非 TTY + 无 `--yes`"→ exit 1 的路径

#### 步骤 2.3 — `group kick`

**范围**：`acps_cli/mq/group_cmd.py` 实现 `kick`（DELETE `.../connection`）。

**错误处理必须区分**：

- 403：CN 不匹配（权限拒绝，提示检查 Leader 证书）
- 404：成员不存在或无活跃连接（非错误，视为成功）
- 502/503：RabbitMQ Management API 不可达（提示检查 RabbitMQ 管理端口）

**审核检查点**：

- 502/503 的错误提示文案中包含 `RabbitMQ Management API` 字样
- `--json` 时 502/503 输出 `{"status": "error", "message": "RabbitMQ Management API unreachable: ..."}`

**Phase 2 冻结标准**：步骤 2.1–2.3 全部 `accepted`；group 四个子命令的 `--help` 均可执行；证书优先级和 `--yes` 行为均有 unit 测试覆盖。

---

### Phase 3：`admin mq auth-probe` 四个子命令

#### 步骤 3.1 — `auth-probe user` 与 `auth-probe vhost`

**范围**：`acps_cli/mq/auth_probe_cmd.py` 实现 `user`（POST `/auth/user`）和 `vhost`（POST `/auth/vhost`）。

**mTLS 证书选择**：使用 `probe_cert_file`/`probe_key_file`；`--cert-file`/`--key-file` 命令行覆盖。

**验证动作**：

```bash
acps-cli admin mq auth-probe user --help
acps-cli admin mq auth-probe vhost --help
```

**审核检查点**：

- 请求体为 `application/x-www-form-urlencoded`（不是 JSON）
- 响应解析：`allow` → `{"result": "allow", ...}`；`deny` → `{"result": "deny", ...}`；其他响应内容 → error
- `--json` 输出与 §2.6 一致

#### 步骤 3.2 — `auth-probe resource` 与 `auth-probe topic`

**范围**：`acps_cli/mq/auth_probe_cmd.py` 实现 `resource` 和 `topic`。

**`resource` 必须校验**：`--resource` 只接受 `exchange` 或 `queue`；`--permission` 只接受 `configure`、`write`、`read`。

**`topic` 必须校验**：`--permission` 只接受 `write` 或 `read`。

**审核检查点**：

- 非法参数值（如 `--resource invalid`）在客户端提前报错，不发出网络请求
- `--json` 输出与 §2.6 一致

**Phase 3 冻结标准**：步骤 3.1–3.2 全部 `accepted`；auth-probe 四个子命令的 `--help` 均可执行；参数校验有 unit 测试覆盖。

---

### Phase 4：unit 测试全量补齐

#### 步骤 4.1 — `MqConfig` 与 `MqAuthClient` unit 测试

**测试文件**：`tests/unit/test_mq_config.py`、`tests/unit/test_mq_client.py`

**覆盖场景**：

- `MqConfig.from_toml()`：完整配置、空配置（所有可选字段缺失）、相对路径解析
- `MqAuthClient`：证书选择优先级逻辑（mock httpx，不发真实请求）
- `MqAuthClient` mTLS 上下文构建：cert 文件缺失时报错

#### 步骤 4.2 — `group` 子命令 unit 测试

**测试文件**：`tests/unit/test_mq_group_cmd.py`

**覆盖场景**：

- `add-member`：成功路径（200/204）、403、500
- `remove-member`：成功路径、404（视为成功）
- `delete`：`--yes` 直接执行；TTY 交互确认（mock `click.confirm`）；非 TTY + 无 `--yes` → exit 1
- `kick`：成功路径；403；502/503 → 含 `RabbitMQ Management` 字样的错误消息
- 证书优先级：`--cert-file` 覆盖 toml 配置
- `--json` 输出：每个命令的成功和失败 schema

#### 步骤 4.3 — `auth-probe` 子命令 unit 测试

**测试文件**：`tests/unit/test_mq_auth_probe_cmd.py`

**覆盖场景**：

- `user`：`allow`、`deny`、服务不可达
- `vhost`：`allow`（`acps` vhost）、`deny`
- `resource`：非法 `--resource` 参数值（客户端报错）、非法 `--permission` 参数值
- `topic`：非法 `--permission` 参数值
- 请求体为 `application/x-www-form-urlencoded` 的断言（通过 httpx mock 检查 request.content）
- `--json` 输出：`allow` / `deny` / error schema

**Phase 4 冻结标准**：步骤 4.1–4.3 全部 `accepted`；`uv run pytest tests/unit/ -q` 全量通过（无新增 skip）。

---

### Phase 5：integration 测试

#### 步骤 5.1 — integration conftest fixture 建设

**范围**：`tests/integration/conftest.py` 扩展，`tests/_local_services.py` 扩展。

**新增 fixture**（详见 §5.2 conftest 设计）：

- `mq_server_certs`：服务端证书（快速路径读环境变量，自动托管路径用 Root CA 直接签发）
- `leader_client_cert`：Leader 客户端证书（读环境变量，或走 ATR 流程自动签发）
- `mq_integration_conf`：生成含 `[mq]` 节的测试专用 `acps-cli.toml`

**\_local_services.py 扩展**：在 `_service_specs()` 中添加 `mq-auth-server` 的 `LocalServiceSpec`，包含：

- `repo_path`：`WORKSPACE_ROOT / "mq-auth-server"`
- `env`：`TLS_CERT_FILE`、`TLS_KEY_FILE`、`REDIS_URL`
- `health_urls`：`https://127.0.0.1:9007/health`、`https://127.0.0.1:9008/health`

**审核检查点**：

- 快速路径（`MQ_GROUP_API_URL` 已设置）：fixture 不启动子进程，直接使用外部实例
- 自动托管路径：Root CA 文件不存在时给出明确错误提示（而非 FileNotFoundError traceback）
- `leader_client_cert` 的 ATR 自动签发路径复用 `_complete_ontology_certificate_flow`（已在 `test_atr_eab_workflow.py` 验证过）

#### 步骤 5.2 — `TestMqGroupWorkflow` 集成测试

**测试文件**：`tests/integration/test_mq_auth_group_workflow.py`

**必须实现的测试方法**：

```
test_health_returns_ok_for_both_ports
test_add_member_returns_success
test_remove_member_returns_success
test_delete_group_returns_success（使用 --yes 跳过交互）
test_kick_member_closes_connection（允许 skip 如果 RabbitMQ Management 不可达）
test_group_member_denied_when_not_in_acl（auth-probe resource 验证）
```

**通过标准**：快速路径（外部实例可达时）必须 pass；自动托管路径在本地 Redis + Root CA 可用时必须 pass；`kick` 在 RabbitMQ Management 不可达时允许 skip（但不能 fail）。

#### 步骤 5.3 — `TestMqAuthProbe` 集成测试

**测试文件**：同上（新增 class）

**必须实现的测试方法**：

```
test_probe_user_allow_for_valid_aic
test_probe_user_deny_for_invalid_username
test_probe_vhost_allow_for_acps_vhost
test_probe_resource_allow_for_inbox_queue
```

**通过标准**：使用 `probe_cert`（非 Leader 专属证书）可以连接 9008 并获得授权决策。

**Phase 5 冻结标准**：步骤 5.1–5.3 全部 `accepted`；在快速路径（外部 mq-auth-server 实例）和自动托管路径（本地子进程）下分别运行 integration 测试均通过；`uv run pytest tests/unit/ tests/integration/ -q` 全量通过（kick 允许有条件 skip）。

---

### Phase 6：e2e 测试

#### 步骤 6.1 — e2e conftest + Phase 1–2 实现（证书预置 + 服务启动）

**测试文件**：`tests/e2e/test_mq_auth_provision_workflow.py`、`tests/e2e/conftest.py`（扩展）

**实现内容**：

- `test_phase1_provision_server_cert`：走完整 ATR 流程（admin 登录 → agent save → approve → EAB → cert issue serverAuth）
- `test_phase2_service_starts_and_health_ok`：用 Phase 1 签发的证书启动 mq-auth-server 子进程，等待 `/health` 就绪；两端口均返回 `status: ok`

**审核检查点**：

- Phase 1–2 是否复用了 `make_acs_file()` 动态生成 mq-auth-server ACS
- mq-auth-server 子进程启动的环境变量是否正确传入（`TLS_CERT_FILE`、`TLS_KEY_FILE`、`REDIS_URL`）
- Phase 2 的 `/health` 探测必须使用 `probe_cert`（不是 serverAuth 证书自己）

#### 步骤 6.2 — Phase 3–4：group CRUD + auth-probe e2e

**实现内容**：

- `test_phase3_group_crud_lifecycle`：Leader 证书 → `add-member` → `remove-member` → `delete`（`--yes`）；`--json` 输出结构验证
- `test_phase4_auth_probe_decisions`：`user allow`（合法 AIC）、`user deny`（非 AIC 字符串）、`vhost allow`（acps）

**审核检查点**：

- Phase 3 的 Leader 证书是否通过完整 ATR clientAuth 签发（不是直接用 serverAuth 证书充当客户端证书）
- `auth-probe` 是否确实使用与 Leader 不同的 `probe_cert`（验证设计中"任意合法 ACPs cert 均可"）

#### 步骤 6.3 — Phase 5 清理 + 全量回归验收

**实现内容**：

- `test_phase5_cleanup`：停止 mq-auth-server 子进程；通过 `admin registry` 删除测试 Agent
- 全量回归：`uv run pytest tests/ -q`

**通过标准**：`uv run pytest tests/unit/ tests/integration/ tests/e2e/ -q` 全量通过（允许 kick / fanout 类有条件 skip，不允许 fail）。

**Phase 6 冻结标准**：步骤 6.1–6.3 全部 `accepted`；e2e 五个阶段均有独立测试方法且通过。

---

### Phase 7：文档收口与最终冻结

#### 步骤 7.1 — `unified-cli-design.md` 更新命令树

**范围**：在统一命令树中补充 `admin mq` 子树（health、group 四个、auth-probe 四个）。

**审核检查点**：命令树与 `mq-auth-cli.md §2.1` 完全一致。

#### 步骤 7.2 — `joint-e2e-execution-ledger.md` 补充台账

**范围**：在联调台账中添加 mq-auth-server e2e 相关条目（test 文件路径、通过状态、前置条件）。

#### 步骤 7.3 — 最终冻结与全量验收

**验收动作**：

```bash
uv run pytest tests/ -q                     # 全量回归
uv run ruff check acps_cli/mq/              # lint
uv run mypy acps_cli/mq/                    # 类型检查
acps-cli admin mq --help                    # 帮助文案
acps-cli admin mq group --help
acps-cli admin mq auth-probe --help
```

**Phase 7 冻结标准**：步骤 7.1–7.3 全部 `accepted`；所有审核记录无 open `critical`/`major` 问题；全量 pytest 无新增 fail。

---

## 8. 审核检查清单模板

每步进入 `in-review` 前，实施人自检以下项目：

```
[ ] 命令名称与 mq-auth-cli.md §2.1 命令树一致
[ ] --json 输出 schema 与 §2.6 一致
[ ] 证书选择优先级：--cert-file/--key-file > toml > 报错
[ ] group 命令使用 group_cert_file（Leader）；auth-probe/health 使用 probe_cert_file
[ ] group delete --yes：TTY 提示确认；非 TTY 无 --yes → exit 1
[ ] group kick 502/503 错误消息含 "RabbitMQ Management API" 字样
[ ] auth-probe 请求体为 application/x-www-form-urlencoded（非 JSON）
[ ] 新增文件全部通过 ruff check 和 mypy
[ ] 新增 unit 测试覆盖成功路径、失败路径（4xx）、--json 输出格式
[ ] 变更不超出本步范围（未提前改动相邻步骤文件）
```

---

## 9. 证据归档规则

- 执行总表和审核结论摘要内联写入 `mq-auth-cli-execution-ledger.md`
- 详细实现/验证/复审证据写入 `docs/mq-auth-cli-execution-evidence/phase-<n>/step-<x>.md`
- 占位目录创建于 Phase 0.2

---

## 10. 已知风险与约束

| 风险 ID | 描述                                               | 影响步骤 | 缓解方案                                                    |
| ------- | -------------------------------------------------- | -------- | ----------------------------------------------------------- |
| RISK-01 | mq-auth-server Root CA 材料不在 `ca-server/certs/` | 5.1      | 快速路径绕过；记录 skip 条件                                |
| RISK-02 | Redis 本地不可达                                   | 5.1      | 快速路径绕过；integration 测试 skip                         |
| RISK-03 | RabbitMQ Management API 不可达                     | 5.2      | kick 测试允许有条件 skip                                    |
| RISK-04 | mq-auth-server 服务端证书 CN 约束与 localhost 不符 | 5.1, 6.1 | 生成证书时加入 localhost SAN（参考 registry mTLS 证书生成） |
