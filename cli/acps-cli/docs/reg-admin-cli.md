# acps-cli Registry 管理命令

Registry 管理面统一收敛在 `acps-cli admin ...` 下，分为两部分：

- `admin auth`：管理员登录与身份查看
- `admin registry`：审核、启用、禁用 Agent

## 关键配置

```toml
[registry]
base_url = "http://localhost:9001"

[auth]
admin_token_file = "./.acps-cli/tokens/registry-admin.json"
```

## 常用命令

```bash
acps-cli admin auth login --username admin --password 'AdminPass!'
acps-cli admin auth whoami --json

acps-cli admin registry review list --json
acps-cli admin registry review approve --agent-id <UUID> --comments "审核通过" --json
acps-cli admin registry review reject --agent-id <UUID> --comments "ACS 缺少必要字段" --json

acps-cli admin registry agent disable --agent-id <UUID> --json
acps-cli admin registry agent enable --agent-id <UUID> --json
```

## 管理面建议

- 管理员 token 与普通用户 token 物理隔离
- 审核动作优先使用 `admin registry review ...`
- Agent 状态控制统一使用 `admin registry agent ...`

## 查看更多帮助

```bash
acps-cli admin auth --help
acps-cli admin registry --help
```
