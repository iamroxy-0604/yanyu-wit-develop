# acps-cli Discovery 命令

Discovery 能力分为用户侧查询命令与管理侧 DSP 控制命令。

## 关键配置

```toml
[discovery]
base_url = "http://localhost:9005"
```

## 用户侧命令

```bash
acps-cli discover status --json
acps-cli discover query "北京旅游推荐" --limit 5
acps-cli discover query --request-json '{"type":"filtered","query":"","limit":5}'
```

## 管理侧命令

```bash
acps-cli admin discovery run-sync --json

acps-cli admin discovery dsp status --json
acps-cli admin discovery dsp registry-info --json
acps-cli admin discovery dsp sync --json
acps-cli admin discovery dsp start --json
acps-cli admin discovery dsp stop --json
acps-cli admin discovery dsp reset --json
acps-cli admin discovery dsp hard-reset --yes --json
acps-cli admin discovery dsp register-webhook --url https://example.test/webhook --json
```

## 使用建议

- 日常联调优先使用 `admin discovery run-sync`
- 只有在需要细粒度控制 DSP 状态机时再进入 `admin discovery dsp ...`
- 结构化查询建议使用 `--request-json` 或 `--request-file`

## 查看更多帮助

```bash
acps-cli discover --help
acps-cli admin discovery --help
```
