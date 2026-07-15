---
applyTo: "**"
---

# Git Commit 信息规范

生成 commit message 时，严格遵循 Conventional Commits 规范。

## 格式

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

## Type（必选）

- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档变更
- `style`: 代码格式调整（不影响逻辑）
- `refactor`: 重构（非新功能、非修复）
- `perf`: 性能优化
- `test`: 测试相关
- `build`: 构建系统或外部依赖变更
- `ci`: CI 配置变更
- `chore`: 其他杂项（工具、脚手架等）
- `revert`: 回滚提交

## Scope（可选，推荐填写）

使用模块名作为 scope：`registry`、`ca`、`discovery`、`shared`、`config`、`ci`、`deps`。

跨模块变更可省略 scope 或使用 `*`。

## Description（必选）

- 使用中文简要描述变更内容
- 不加句号结尾
- 使用祈使语气（动词开头）：如"添加"、"修复"、"重构"、"更新"

## Body（可选）

- 解释变更的动机和上下文
- 与上方空一行
- 使用中文

## Footer（可选）

- 破坏性变更：以 `BREAKING CHANGE: ` 开头描述
- 关联 Issue：`Closes #123` 或 `Refs #456`

## 示例

```
feat(ca): 添加证书自动续期子命令

实现 renew 子命令，在证书有效期少于 30 天时自动触发 ACME 续期流程。

Closes #42
```

```
fix(registry): 修复注册信息查询时未处理 404 响应的问题
```

```
refactor(discovery): 提取公共 HTTP 客户端逻辑至 shared 模块
```

```
chore(deps): 更新 cryptography 至 44.0.0
```
