"""`wit atr` — 智能体可信注册 (Agent Trusted Registration, ATR) 命令。

提供:
  - `wit atr auto` — 通过 Flux 一键自动实体注册
  - `wit atr status` — 查看当前 ATR 注册状态（通过 Flux）
  - `wit atr renew` — 续期现有实体证书
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from cli.config import (
    YANYU_WIT_HOME,
    config_set,
    load_config,
)

logger = logging.getLogger(__name__)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `atr` 子命令组。"""
    parser = subparsers.add_parser("atr", help="可信注册 (ATR) 管理")
    sub = parser.add_subparsers(dest="atr_command")

    # atr auto
    auto_parser = sub.add_parser("auto", help="一键自动完成实体注册（通过 Flux）")
    auto_parser.add_argument("--endpoint", "-e", help="Wit 实体回调 URL（至少一个）")
    auto_parser.add_argument("--flux-url", help="Flux 服务根地址（覆盖 config.toml 配置）")
    auto_parser.set_defaults(func=execute_auto)

    # atr status
    status_parser = sub.add_parser("status", help="查看当前注册状态（通过 Flux）")
    status_parser.set_defaults(func=execute_status)

    # atr renew
    renew_parser = sub.add_parser("renew", help="对现有实体证书进行续期")
    renew_parser.add_argument("--force", "-f", action="store_true", help="强制续期，即使证书未临近过期")
    renew_parser.set_defaults(func=execute_renew)

    parser.set_defaults(func=lambda args: parser.print_help())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_flux_token() -> str:
    """从已存储的凭据中加载 Flux Bearer Token。"""
    from cli.utils.credentials import get_access_token
    token = get_access_token("flux")
    if not token:
        raise RuntimeError(
            "未找到 Flux 访问令牌，请先运行 `wit init` 完成登录。"
        )
    return token


def _get_flux_base_url(args_flux_url: str | None = None) -> str:
    """解析 Flux 基础 URL：参数 > config.toml > 默认值。"""
    if args_flux_url:
        return args_flux_url.rstrip("/")
    cfg = load_config()
    url = cfg.get("services", {}).get("flux", {}).get("base_url", "http://127.0.0.1:13002")
    return url.rstrip("/")


def _make_acme_account_key(entity_aic: str):
    """为特定实体 AIC 生成或加载 ACME 账户密钥。"""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cli.config import CERTS_DIR

    key_path = CERTS_DIR / f"acme-{entity_aic}-account-key.pem"
    if key_path.exists():
        from cryptography.hazmat.primitives import serialization
        return serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )

    # Generate new key
    key = ec.generate_private_key(ec.SECP256R1())
    key_path.parent.mkdir(parents=True, exist_ok=True)
    from cryptography.hazmat.primitives import serialization
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    print(f"   🔑 已生成 ACME 账户密钥: {key_path}")
    return key


def _save_eab(eab: dict, entity_aic: str) -> Path:
    """将 EAB 凭据持久化到账户凭据目录中。"""
    from cli.config import get_account_dir
    creds_dir = get_account_dir() / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    eab_path = creds_dir / f"eab-{entity_aic}.json"
    eab_path.write_text(json.dumps(eab, indent=2, ensure_ascii=False), encoding="utf-8")
    import os, stat
    try:
        os.chmod(eab_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return eab_path


def _load_eab(entity_aic: str) -> dict | None:
    """加载以前保存的 EAB 凭据。"""
    from cli.config import get_account_dir
    eab_path = get_account_dir() / "credentials" / f"eab-{entity_aic}.json"
    if eab_path.exists():
        try:
            return json.loads(eab_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _issue_certificate(entity_aic: str, eab: dict, ca_url: str) -> tuple[Path, Path]:
    """运行 ACME 流程并返回 (cert_path, key_path)。"""
    from acps_cli.ca.acme import AcmeClient, AcmeError
    from acps_cli.ca.keys import generate_private_key, save_private_key
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cli.config import CERTS_DIR

    # Normalise CA URL: replace 127.0.0.1 with localhost so the host matches
    # the CA server's self-reported directory URL (which uses 'localhost').
    ca_url_full = ca_url.rstrip("/").replace("127.0.0.1", "localhost")
    if not ca_url_full.endswith("/acps-atr-v2"):
        ca_url_full = f"{ca_url_full}/acps-atr-v2"

    cert_dir = CERTS_DIR / "entity"
    cert_dir.mkdir(parents=True, exist_ok=True)
    key_path = cert_dir / "private-key.pem"

    # 1. ACME account key
    account_key = _make_acme_account_key(entity_aic)
    acme = AcmeClient(ca_server_url=ca_url_full, account_key=account_key)

    # 2. Register ACME account with EAB
    print("   💼 向 CA 注册 ACME 账户（绑定 EAB 凭证）...")
    acme.new_account(
        contact=["mailto:yanyu-wit-entity@acps"],
        eab_credential=eab,
    )
    print("   ✅ ACME 账户注册成功")

    # 3. New order
    print(f"   📦 提交证书订单，AIC: {entity_aic}...")
    order = acme.new_order(aic=entity_aic, usage="clientAuth")
    print(f"   ✅ 证书订单创建成功（状态: {order.get('status')}）")

    # 4. Generate entity private key & CSR
    print("   🔑 生成实体私钥 (P-256 ECC)...")
    entity_key = generate_private_key("ec256")
    save_private_key(entity_key, str(key_path))
    print(f"   🔑 实体私钥已保存: {key_path}")

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, entity_aic),
        ]))
        .sign(entity_key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)

    # 5. Finalize order
    print("   🚀 提交 CSR，终结订单...")
    finalized = acme.finalize_order(order["finalize"], csr_pem)
    print(f"   ✅ CSR 已提交（状态: {finalized.get('status')}）")

    # 6. Wait for certificate
    cert_url = finalized.get("certificate")
    if not cert_url and finalized.get("status") == "processing":
        print("   ⏳ 等待 CA 签发证书...")
        for _ in range(10):
            time.sleep(1)
            order_check = acme._post(finalized["url"], None)
            order_data = order_check.json()
            if order_data.get("certificate"):
                cert_url = order_data["certificate"]
                break
            if order_data.get("status") == "invalid":
                raise AcmeError(f"证书订单无效: {order_data}")

    if not cert_url:
        raise AcmeError("证书签发超时，未能获取到有效的证书 URL")

    # 7. Download certificate
    print(f"   📥 从 CA 下载证书...")
    cert_pem = acme.get_certificate(cert_url)
    cert_path = cert_dir / "certificate.pem"
    cert_path.write_bytes(cert_pem)
    print(f"   ✅ 证书已保存: {cert_path}")

    return cert_path, key_path


# ---------------------------------------------------------------------------
# Cert Renewal Heartbeat Job Creation
# ---------------------------------------------------------------------------

CERT_RENEWAL_SCRIPT = '''#!/bin/bash
# 证书自动续期脚本
# 由 wit atr auto 自动生成，请勿手动编辑
set -e

# 获取项目根目录（本脚本位于 heartbeat/scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"
echo "[$(date)] 开始执行证书续期..."
uv run wit atr renew --force
echo "[$(date)] 证书续期完成"
'''

CERT_RENEWAL_JOB_NAME = "证书自动续期"


def _ensure_cert_renewal_job(workspace_dir: Path) -> None:
    """在 heartbeat store 中创建证书续期定时任务（若不存在）。

    直接操作 heartbeat.json 文件，避免依赖 async HeartbeatManager。
    """
    import os
    import stat

    heartbeat_dir = workspace_dir / "heartbeat"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)

    store_path = heartbeat_dir / "heartbeat.json"

    # 加载已有数据
    existing_jobs: list[dict] = []
    if store_path.exists():
        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
            existing_jobs = data.get("jobs", [])
        except Exception:
            pass

    # 检查是否已存在同名任务
    for job in existing_jobs:
        if job.get("name") == CERT_RENEWAL_JOB_NAME:
            print(f"\n   ℹ️  证书续期定时任务已存在，跳过创建。")
            return

    # 生成脚本文件
    script_rel_path = "scripts/cert_renewal.sh"
    full_script_path = heartbeat_dir / script_rel_path
    full_script_path.parent.mkdir(parents=True, exist_ok=True)
    full_script_path.write_text(CERT_RENEWAL_SCRIPT, encoding="utf-8")
    os.chmod(
        str(full_script_path),
        os.stat(str(full_script_path)).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
    )

    # 构建 job 数据
    import uuid

    now_ms = time.time() * 1000
    job_id = uuid.uuid4().hex[:12]

    schedule = {
        "frequency": "monthly",
        "time": "03:00",
        "weekdays": [],
        "monthdays": [1, 15],
        "once_at": None,
        "timezone": "Asia/Shanghai",
    }

    # 计算 next_run_at_ms
    next_run_at_ms: float | None = None
    try:
        from croniter import croniter
        import zoneinfo

        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        from datetime import datetime as _dt
        now_dt = _dt.fromtimestamp(now_ms / 1000, tz=tz)
        cron = croniter("0 3 1,15 * *", now_dt)
        next_dt = cron.get_next(_dt)
        next_run_at_ms = next_dt.timestamp() * 1000
    except Exception:
        pass

    job_dict = {
        "id": job_id,
        "name": CERT_RENEWAL_JOB_NAME,
        "description": "定期执行 atr renew --force 续期实体证书（有效期48天）",
        "enabled": True,
        "type": "script",
        "instruction": "",
        "script_path": script_rel_path,
        "schedule": schedule,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "state": {
            "next_run_at_ms": next_run_at_ms,
            "last_run_at_ms": None,
            "last_status": None,
            "last_error": None,
            "last_result": None,
            "consecutive_errors": 0,
            "running": False,
        },
    }

    # 保存到 heartbeat.json
    existing_jobs.append(job_dict)
    store_data = {
        "version": 2,
        "jobs": existing_jobs,
    }
    store_path.write_text(
        json.dumps(store_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n   ✅ 已自动创建证书续期定时任务（每月1日/15日 03:00）")
    print(f"      任务 ID: {job_id}")
    print(f"      脚本路径: {full_script_path}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def execute_auto(args: argparse.Namespace) -> None:
    """执行通过 Flux 进行的完整 ATR 自动化实体注册流程。"""
    import requests

    # Check if Yanyu account is logged in
    from cli.config import get_active_account, get_account_workspace_dir, CERTS_DIR
    active_user = get_active_account()

    if not active_user:
        print("❌ 错误: 未检测到已登录的 Yanyu 账号。进行可信注册前必须先登录您的 Yanyu 账号！")
        print("💡 请先运行 `wit init` 登录您的账号，然后再运行可信注册。")
        return

    workspace_dir = get_account_workspace_dir(active_user)

    # 检查凭据存储中是否有有效 Token（不再依赖 .kc_token 文件）
    from cli.utils.credentials import get_access_token as _get_at
    if not _get_at("flux"):
        print("❌ 错误: 未检测到有效的登录凭据。进行可信注册前必须先登录您的 Yanyu 账号！")
        print("💡 请先运行 `wit init` 登录您的账号，然后再运行可信注册。")
        return

    try:
        token = _get_flux_token()
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    flux_base = _get_flux_base_url(getattr(args, "flux_url", None))

    print("🚀 开始 ATR 实体可信注册流程（通过 Flux）\n")

    # ── Step 1: Collect Wit endpoint ────────────────────────────────────────
    print("📋 步骤 1/3: 配置实体回调端点")
    endpoint_url = getattr(args, "endpoint", None) or ""
    if not endpoint_url:
        endpoint_url = input("   请输入 Wit 实体回调 URL（例如 http://10.0.0.1:7000）: ").strip()
    if not endpoint_url:
        print("   ❌ 未提供回调 URL，注册中止。")
        return

    register_payload = {
        "endPoints": [
            {
                "url": endpoint_url,
                "transport": "HTTP_JSON",
                "security": []
            }
        ],
        "entityMeta": {
            "description": "Yanyu-Wit entity registration"
        }
    }

    # ── Step 2: Call Flux POST /wit/atr/entity/register ────────────────────
    print("\n📋 步骤 2/3: 向 Flux 发起实体注册，获取 EAB 凭证")
    register_url = f"{flux_base}/wit/atr/entity/register"
    print(f"   👉 POST {register_url}")

    try:
        resp = requests.post(
            register_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=register_payload,
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"   ❌ 网络请求失败: {e}")
        return

    if resp.status_code != 200:
        print(f"   ❌ Flux 注册失败（HTTP {resp.status_code}）: {resp.text}")
        return

    register_res = resp.json()
    entity_aic = register_res.get("entity_aic")
    eab = register_res.get("eab")
    ca_url = register_res.get("ca_url", "")

    if not entity_aic or not eab:
        print(f"   ❌ 响应中缺少 entity_aic 或 eab，无法继续。响应内容: {register_res}")
        return

    print(f"   ✅ 实体注册成功！Entity AIC: {entity_aic}")
    print(f"   ✅ EAB keyId: {eab.get('keyId', '?')}")
    print(f"   ✅ CA URL: {ca_url}")

    # Save AIC and CA URL to config.toml immediately
    config_set("identity.agent_aic", entity_aic)
    config_set("services.ca.base_url", ca_url)

    # Save EAB to credentials directory
    eab_path = _save_eab(eab, entity_aic)
    print(f"   💾 EAB 已保存: {eab_path}")

    # ── Step 3: ACME certificate issuance ──────────────────────────────────
    print("\n📋 步骤 3/3: 使用 EAB 向 CA 申请实体运行证书（ACME）")
    try:
        cert_path, key_path = _issue_certificate(entity_aic, eab, ca_url)
    except Exception as e:
        print(f"   ❌ 证书申请失败: {e}")
        return

    # Save cert paths to config.toml
    config_set("certs.entity_cert", str(cert_path))
    config_set("certs.entity_key", str(key_path))

    # 创建证书续期定时任务
    _ensure_cert_renewal_job(workspace_dir)

    print()
    print("🎉 ATR 实体可信注册完成！")
    print(f"   Entity AIC:   {entity_aic}")
    print(f"   CA URL:       {ca_url}")
    print(f"   证书路径:     {cert_path}")
    print(f"   私钥路径:     {key_path}")
    print(f"   EAB 路径:     {eab_path}")
    print(f"   配置已更新:   ~/.yanyu-wit/")


def execute_status(args: argparse.Namespace) -> None:
    """显示当前 ATR 注册状态（通过 Flux GET 端点）。"""
    import requests

    cfg = load_config()
    identity = cfg.get("identity", {})
    certs = cfg.get("certs", {})

    print("📋 ATR 注册状态\n")

    # ── Local config status ─────────────────────────────────────────────────
    agent_aic = identity.get("agent_aic", "")
    if agent_aic:
        print(f"   ✅ Agent AIC (本地): {agent_aic}")
    else:
        print("   ❌ Agent AIC: 未注册")

    entity_cert = certs.get("entity_cert", "")
    if entity_cert and Path(entity_cert).exists():
        print(f"   ✅ 实体证书: {entity_cert}")
        try:
            from cryptography import x509
            cert = x509.load_pem_x509_certificate(Path(entity_cert).read_bytes())
            print(f"      有效期至: {cert.not_valid_after_utc}")
        except Exception:
            pass
    else:
        print("   ❌ 实体证书: 未颁发")

    entity_key = certs.get("entity_key", "")
    if entity_key and Path(entity_key).exists():
        print(f"   ✅ 实体私钥: {entity_key}")
    else:
        print("   ❌ 实体私钥: 未生成")

    # ── Remote Flux status ──────────────────────────────────────────────────
    print()
    try:
        token = _get_flux_token()
        flux_base = _get_flux_base_url()
        status_url = f"{flux_base}/wit/atr/entity/register"
        print(f"   🔍 查询 Flux 注册状态... ({status_url})")
        resp = requests.get(
            status_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            remote = resp.json()
            print(f"   ✅ Flux 端实体已注册")
            print(f"      Entity AIC:     {remote.get('entity_aic', '?')}")
            print(f"      Ontology AIC:   {remote.get('ontology_aic', '?')}")
            print(f"      创建时间:       {remote.get('created_at', '?')}")
        elif resp.status_code == 404:
            print("   ℹ️  Flux 端：当前用户尚未注册任何实体")
        else:
            print(f"   ⚠️  Flux 状态查询异常（HTTP {resp.status_code}）: {resp.text[:200]}")
    except RuntimeError as e:
        print(f"   ⚠️  无法查询 Flux 状态: {e}")
    except Exception as e:
        print(f"   ⚠️  Flux 状态查询失败: {e}")

    if not agent_aic:
        print("\n   💡 运行 `wit atr auto` 开始自动注册")


def execute_renew(args: argparse.Namespace) -> None:
    """通过重新获取 Flux EAB 来续期现有的 Agent 实体证书。"""
    import requests
    from datetime import datetime, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    # Check login
    from cli.config import get_active_account, get_account_workspace_dir, CERTS_DIR
    active_user = get_active_account()

    if not active_user:
        print("❌ 错误: 未检测到已登录的 Yanyu 账号。进行可信注册前必须先登录您的 Yanyu 账号！")
        print("💡 请先运行 `wit init` 登录您的账号，然后再运行可信注册。")
        return

    workspace_dir = get_account_workspace_dir(active_user)

    # 检查凭据存储中是否有有效 Token（不再依赖 .kc_token 文件）
    from cli.utils.credentials import get_access_token as _get_at
    if not _get_at("flux"):
        print("❌ 错误: 未检测到有效的登录凭据。进行可信注册前必须先登录您的 Yanyu 账号！")
        print("💡 请先运行 `wit init` 登录您的账号，然后再运行可信注册。")
        return

    cfg = load_config()
    identity = cfg.get("identity", {})
    certs = cfg.get("certs", {})

    entity_aic = identity.get("agent_aic", "")
    if not entity_aic:
        print("❌ 错误: 本地未检测到已注册的 Agent AIC，无法进行续期。")
        print("💡 请先运行 `wit atr auto` 进行初次自动注册。")
        return

    entity_cert_path = certs.get("entity_cert", "")
    if not entity_cert_path or not Path(entity_cert_path).exists():
        print("❌ 错误: 本地未检测到实体证书文件，无法续期。")
        print("💡 请先运行 `wit atr auto` 进行初次自动注册。")
        return

    # Check expiration unless --force
    cert_path = Path(entity_cert_path)
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        days_remaining = (cert.not_valid_after_utc - datetime.now(timezone.utc)).days
        if not args.force and days_remaining > 30:
            print(f"❌ 续期中止: 证书仍有 {days_remaining} 天有效。如果想强行续期，请使用 --force 参数。")
            return
        print(f"   ℹ️  证书剩余有效期: {days_remaining} 天，开始执行续期...")
    except Exception as e:
        print(f"   ⚠️  读取现有证书失效状态失败 ({e})，将强制执行续期流程。")

    print("🚀 开始 ATR 实体证书续期流程\n")

    # Retrieve Flux token
    try:
        token = _get_flux_token()
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    flux_base = _get_flux_base_url()

    # ── Re-fetch EAB from Flux by calling POST again ────────────────────────
    print("📋 步骤 1/2: 通过 Flux 重新获取最新 EAB 凭证")
    register_url = f"{flux_base}/wit/atr/entity/register"
    print(f"   👉 POST {register_url}")

    # Reuse existing endpoint info or provide a placeholder
    # We look up existing registration first
    try:
        get_resp = requests.get(
            register_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if get_resp.status_code == 200:
            remote = get_resp.json()
            endpoints = remote.get("end_points", [])
        else:
            endpoints = [{"url": "http://localhost:7000", "transport": "HTTP_JSON", "security": []}]
    except Exception:
        endpoints = [{"url": "http://localhost:7000", "transport": "HTTP_JSON", "security": []}]

    register_payload = {
        "endPoints": endpoints if endpoints else [{"url": "http://localhost:7000", "transport": "HTTP_JSON", "security": []}],
        "entityMeta": {"description": "Yanyu-Wit certificate renewal"}
    }

    try:
        resp = requests.post(
            register_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=register_payload,
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"   ❌ 网络请求失败: {e}")
        return

    if resp.status_code != 200:
        print(f"   ❌ Flux 请求失败（HTTP {resp.status_code}）: {resp.text}")
        return

    register_res = resp.json()
    eab = register_res.get("eab")
    ca_url = register_res.get("ca_url", cfg.get("services", {}).get("ca", {}).get("base_url", ""))

    if not eab:
        print(f"   ❌ 响应中缺少 eab，无法继续续期。")
        return

    print(f"   ✅ EAB 获取成功 (keyId: {eab.get('keyId', '?')})")

    # Save updated EAB
    eab_path = _save_eab(eab, entity_aic)
    print(f"   💾 EAB 已更新: {eab_path}")

    # Update CA URL in config if returned
    if ca_url:
        config_set("services.ca.base_url", ca_url)

    # ── ACME certificate renewal ─────────────────────────────────────────────
    print("\n📋 步骤 2/2: 申请并下载新实体证书（ACME）")
    try:
        new_cert_path, new_key_path = _issue_certificate(entity_aic, eab, ca_url)
    except Exception as e:
        print(f"   ❌ 证书续期失败: {e}")
        return

    config_set("certs.entity_cert", str(new_cert_path))
    config_set("certs.entity_key", str(new_key_path))

    print("\n🎉 ATR 实体证书续期完成！")
    print(f"   Entity AIC:  {entity_aic}")
    print(f"   证书路径:    {new_cert_path}")
