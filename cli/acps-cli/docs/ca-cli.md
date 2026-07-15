# acps-cli cert 命令

证书生命周期相关能力统一收敛在 `acps-cli cert ...` 和 `acps-cli admin ca ...` 下。

## 关键配置

```toml
[ca]
base_url = "http://localhost:9003"
# account_keys_dir = "./keyfiles/accounts"
# private_keys_dir = "./keyfiles/private"
# certs_dir = "./keyfiles/certs"
# csr_dir = "./keyfiles/csr"
# trust_bundle_path = "./keyfiles/trust-bundle.pem"
```

## 用户侧命令

```bash
acps-cli cert issue --aic <AIC> --eab-file private/eab.json --usage clientAuth
acps-cli cert renew --aic <AIC> --eab-file private/eab.json --usage clientAuth --force
acps-cli cert revoke --aic <AIC>
acps-cli cert status --aic <AIC> --json

acps-cli cert account-key rollover --aic <AIC>
acps-cli cert trust-bundle update --output keyfiles/trust-bundle.pem

acps-cli cert crl download --output current.crl
acps-cli cert crl info --json
acps-cli cert crl detail --json

acps-cli cert ocsp check --aic <AIC> --json
acps-cli cert ocsp cert-status --aic <AIC> --json
```

## 管理侧命令

```bash
acps-cli admin ca crl list --json
acps-cli admin ca crl refresh --json
acps-cli admin ca ocsp responder-info --json
acps-cli admin ca ocsp stats --json
```

## 使用建议

- 证书申请前先执行 `acps-cli cert eab fetch`
- `cert issue`、`cert renew` 与 `cert revoke` 共用同一组本地密钥目录
- 管理侧动作建议在已配置管理员上下文的联调环境中执行

## 查看更多帮助

```bash
acps-cli cert --help
acps-cli admin ca --help
```
