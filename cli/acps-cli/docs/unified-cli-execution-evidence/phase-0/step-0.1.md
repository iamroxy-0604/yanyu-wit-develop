# Phase 0.1 设计基线冻结核对记录

日期：2026-05-05

实施人：GitHub Copilot

独立审核人：Explore 子代理

## 核对范围

对 `acps-cli/docs/unified-cli-design.md` 执行设计基线冻结核对，范围限定为：

1. 根命令树与 `admin` 子树。
2. `agent` 状态机与管理员动作边界。
3. `entity derive`、`cert eab fetch`、`cert crl detail`、`admin discovery run-sync` 的命名和定位。
4. `user` / `admin` 两级模型。
5. 认证矩阵。
6. `base_url` 派生规则与 `registry.mtls_base_url` 例外项。
7. 配置优先级。
8. `run-sync` 禁止行为清单。
9. 一次性切换策略。

## 核对结果

1. 设计文档中的命令树与命令分配说明一致，无旧命令别名残留。
2. `agent save`、`submit`、`check`、`sync`、`delete` 的状态表已明确，管理员动作边界已单独列出。
3. `entity derive`、`cert eab fetch`、`cert crl detail`、`admin discovery run-sync` 的命名已稳定。
4. 权限分层已收敛为 `user` / `admin` 两级，不再保留 `internal` 独立级别。
5. 认证矩阵已明确各命令域默认上下文与默认凭证来源。
6. `base_url` 的服务根语义与 `registry.mtls_base_url` 显式配置要求已稳定。
7. 配置优先级已明确为“命令行 > 环境变量 > TOML 新键 > 派生默认值”。
8. `run-sync` 的禁止行为已直接写入设计文档，而不是仅停留在实现计划。
9. 一次性切换策略已定稿，不再保留旧四个 console script 和旧配置键运行时兼容逻辑。

## 审核结论

第三轮独立复审结论：No blocking findings。

Phase 0.1 可进入 `accepted`。
