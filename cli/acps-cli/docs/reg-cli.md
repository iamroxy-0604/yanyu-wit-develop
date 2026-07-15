# acps-cli Registry 用户命令

`acps-cli` 的 Registry 用户侧能力分布在以下命令域：

- `auth`：登录与身份查看
- `agent`：Agent 草稿、提交流程和状态同步
- `entity`：基于本体 AIC 派生并注册实体
- `cert eab`：为证书申请获取 EAB 凭证

## 关键配置

```toml
[registry]
base_url = "http://localhost:9001"
mtls_base_url = "http://localhost:9002"

[auth]
user_token_file = "./.acps-cli/tokens/registry-user.json"
```

其中：

- `registry.base_url` 指向 Registry 服务根地址
- `registry.mtls_base_url` 用于本体证书 mTLS 平面
- `auth.user_token_file` 保存普通用户登录态

## 常用命令

```bash
acps-cli auth login --username alice --password 'S3cret!'
acps-cli auth whoami --json

acps-cli agent list --json
acps-cli agent save --acs-file acs.json --json
acps-cli agent submit --agent-id <UUID> --json
acps-cli agent check --acs-file acs.json --json
acps-cli agent sync --acs-file acs.json --json
acps-cli agent delete --acs-file acs.json --json

acps-cli entity derive --ontology-aic <AIC> --payload-file entity.json --json
acps-cli cert eab fetch --aic <AIC> --output private/eab.json --json
```

## 使用建议

- 草稿阶段优先使用 `agent save`
- 提交审核后使用 `agent check` 和 `agent sync` 跟踪状态
- 本体证书场景需要同时准备 `registry.mtls_base_url` 与对应证书材料
- 证书申请前先执行 `cert eab fetch`

## 查看更多帮助

```bash
acps-cli auth --help
acps-cli agent --help
acps-cli entity --help
acps-cli cert eab --help
```
