import os
import base64
import json
import logging
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

def _get_fernet() -> Fernet:
    from cli.utils.secrets import get_session_secret
    secret = get_session_secret()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)

def encrypt_data(data: str) -> str:
    f = _get_fernet()
    return f.encrypt(data.encode()).decode()

def decrypt_data(token: str) -> str:
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()

def restore_entity_files_if_needed(username: str, entity: dict):
    """SaaS 模式下从数据库中的 credentials 加密字段还原证书、私钥和 EAB 配置文件到物理磁盘。"""
    try:
        from cli.config import get_account_dir, get_account_workspace_dir
        
        creds_json = decrypt_data(entity["credentials"])
        creds = json.loads(creds_json)
        
        account_dir = get_account_dir(username)
        cert_dir = account_dir / "certs" / "entity"
        cert_dir.mkdir(parents=True, exist_ok=True)
        
        cert_path = cert_dir / "certificate.pem"
        key_path = cert_dir / "private-key.pem"
        
        # 1. 还原实体证书与私钥
        if not cert_path.exists() or cert_path.read_text(encoding="utf-8") != creds["entity_cert"]:
            cert_path.write_text(creds["entity_cert"], encoding="utf-8")
        if not key_path.exists() or key_path.read_text(encoding="utf-8") != creds["entity_key"]:
            key_path.write_text(creds["entity_key"], encoding="utf-8")
            
        # 2. 还原 ACME 账户私钥
        entity_aic = entity["entity_id"]
        acme_key_path = account_dir / "certs" / f"acme-{entity_aic}-account-key.pem"
        if "acme_account_key" in creds:
            if not acme_key_path.exists() or acme_key_path.read_text(encoding="utf-8") != creds["acme_account_key"]:
                acme_key_path.write_text(creds["acme_account_key"], encoding="utf-8")
                
        # 3. 还原 EAB 配置文件
        creds_dir = account_dir / "credentials"
        creds_dir.mkdir(parents=True, exist_ok=True)
        eab_path = creds_dir / f"eab-{entity_aic}.json"
        if "eab" in creds:
            eab_str = json.dumps(creds["eab"], indent=2, ensure_ascii=False)
            if not eab_path.exists() or eab_path.read_text(encoding="utf-8") != eab_str:
                eab_path.write_text(eab_str, encoding="utf-8")
                
        # 4. 更新用户的 config.toml 配置，以完全适配底层需要
        from cli.config import config_set
        config_set("identity.agent_aic", entity_aic, username=username)
        config_set("certs.entity_cert", str(cert_path), username=username)
        config_set("certs.entity_key", str(key_path), username=username)
        if "ca_url" in creds:
            config_set("services.ca.base_url", creds["ca_url"], username=username)
            
        # 5. 确保定时续期任务在 workspace 级别注册
        workspace_dir = get_account_workspace_dir(username)
        from cli.commands.atr_cmd import _ensure_cert_renewal_job
        _ensure_cert_renewal_job(workspace_dir)
        
    except Exception as e:
        logger.error(f"Failed to restore entity files for {username}: {e}", exc_info=True)

def register_entity_if_needed(user_id: str):
    """如果租户在 SaaS 模式下首次登录，并且在 user_entities 中没有实体注册，则自动代其注册。"""
    try:
        from service.app import db
        entity = db.get_user_entity(user_id)
        if entity:
            return # 已经注册
            
        logger.info(f"ACPS entity registration needed for user: {user_id}")
        
        # 1. 从凭据管理模块读取 Token（统一路径，不再依赖 .kc_token 文件）
        from cli.utils.credentials import get_access_token as _get_at
        token = _get_at("flux")
        if not token:
            logger.warning(f"Flux credential not found for user {user_id}, registration postponed.")
            return
        
        # 2. 从 Flux 注册，获取 EAB
        from cli.commands.atr_cmd import _get_flux_base_url
        flux_base = _get_flux_base_url()
        register_url = f"{flux_base}/wit/atr/entity/register"
        
        import requests
        endpoint_url = os.getenv("WIT_ENDPOINT_URL", "http://localhost:7000")
        register_payload = {
            "endPoints": [
                {
                    "url": endpoint_url,
                    "transport": "HTTP_JSON",
                    "security": []
                }
            ],
            "entityMeta": {
                "description": f"Yanyu-Wit entity registration for {user_id} (SaaS auto)"
            }
        }
        
        logger.info(f"Sending entity registration request to Flux for user {user_id}...")
        resp = requests.post(
            register_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=register_payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"Flux entity registration failed (HTTP {resp.status_code}): {resp.text}")
            return
            
        register_res = resp.json()
        entity_aic = register_res.get("entity_aic")
        eab = register_res.get("eab")
        ca_url = register_res.get("ca_url", "")
        
        if not entity_aic or not eab:
            logger.error(f"Missing entity_aic or eab in Flux response: {register_res}")
            return
            
        logger.info(f"Flux registered user {user_id} as entity {entity_aic}. Proceeding to ACME order...")
        
        # 3. ACME 流程
        from acps_cli.ca.acme import AcmeClient
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        import time
        
        # ACME 账户密钥生成
        account_key = ec.generate_private_key(ec.SECP256R1())
        account_key_pem = account_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()
        ).decode("utf-8")
        
        # 规范化 CA URL (同 atr_cmd.py)
        ca_url_full = ca_url.rstrip("/").replace("127.0.0.1", "localhost")
        if not ca_url_full.endswith("/acps-atr-v2"):
            ca_url_full = f"{ca_url_full}/acps-atr-v2"
            
        acme = AcmeClient(ca_server_url=ca_url_full, account_key=account_key)
        
        # 注册 ACME 账户
        acme.new_account(
            contact=["mailto:yanyu-wit-entity@acps"],
            eab_credential=eab,
        )
        
        # 创建证书订单
        order = acme.new_order(aic=entity_aic, usage="clientAuth")
        
        # 生成实体密钥与 CSR
        entity_key = ec.generate_private_key(ec.SECP256R1())
        entity_key_pem = entity_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()
        ).decode("utf-8")
        
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, entity_aic),
            ]))
            .sign(entity_key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM)
        
        # 终结订单并等待证书
        finalized = acme.finalize_order(order["finalize"], csr_pem)
        cert_url = finalized.get("certificate")
        if not cert_url and finalized.get("status") == "processing":
            for _ in range(10):
                time.sleep(1)
                order_check = acme._post(finalized["url"], None)
                order_data = order_check.json()
                if order_data.get("certificate"):
                    cert_url = order_data["certificate"]
                    break
                    
        if not cert_url:
            logger.error("Timed out waiting for CA certificate issuance.")
            return
            
        cert_pem_bytes = acme.get_certificate(cert_url)
        cert_pem = cert_pem_bytes.decode("utf-8")
        
        # 4. 加密存储到 PostgreSQL user_entities 表
        creds_data = {
            "entity_cert": cert_pem,
            "entity_key": entity_key_pem,
            "acme_account_key": account_key_pem,
            "ca_url": ca_url,
            "eab": eab
        }
        encrypted_creds = encrypt_data(json.dumps(creds_data))
        
        db.save_user_entity(user_id, entity_aic, encrypted_creds)
        logger.info(f"Saved ACPS entity credentials to PostgreSQL for user {user_id}.")
        
        # 5. 还原文件到本地磁盘，并注入用户的 config.toml 与定时任务
        db_entity = db.get_user_entity(user_id)
        if db_entity:
            restore_entity_files_if_needed(user_id, db_entity)
            logger.info(f"Restored entity certificate files for user {user_id}.")
            
        # 6. 写入安全审计日志
        db.write_audit_log(
            user_id, 
            "atr_register", 
            f"Successfully auto-registered ACPS entity {entity_aic} with EAB KeyId: {eab.get('keyId')}"
        )
        
    except Exception as e:
        logger.exception(f"Failed to auto register ACPS entity for user {user_id}: {e}")

def renew_user_certificate(user_id: str, entity: dict):
    """在 Host 端为指定用户完成实体证书续签，并更新加密存储在 PostgreSQL。"""
    try:
        from service.app import db
        # 确保物理文件夹与账户设置存在
        restore_entity_files_if_needed(user_id, entity)
        
        creds_json = decrypt_data(entity["credentials"])
        creds = json.loads(creds_json)
        
        entity_aic = entity["entity_id"]
        eab = creds["eab"]
        ca_url = creds["ca_url"]
        
        logger.info(f"Starting certificate renewal for user {user_id} (AIC={entity_aic})...")
        
        # 调用 atr_cmd 的 _issue_certificate 接口获取更新的证书与私钥
        from cli.commands.atr_cmd import _issue_certificate
        cert_path, key_path = _issue_certificate(entity_aic, eab, ca_url)
        
        new_cert = cert_path.read_text(encoding="utf-8")
        new_key = key_path.read_text(encoding="utf-8")
        
        # 获取 ACME 账户密钥（在 restore 时应该已经还原至 disk）
        from cli.config import get_account_dir
        account_dir = get_account_dir(user_id)
        acme_key_path = account_dir / "certs" / f"acme-{entity_aic}-account-key.pem"
        acme_account_key = acme_key_path.read_text(encoding="utf-8")
        
        # 更新数据库
        updated_creds = {
            "entity_cert": new_cert,
            "entity_key": new_key,
            "acme_account_key": acme_account_key,
            "ca_url": ca_url,
            "eab": eab
        }
        encrypted_creds = encrypt_data(json.dumps(updated_creds))
        db.save_user_entity(user_id, entity_aic, encrypted_creds)
        
        logger.info(f"Successfully renewed ACPS certificate for user {user_id} (AIC={entity_aic}).")
        
        # 写入安全审计日志
        db.write_audit_log(
            user_id, 
            "atr_renew", 
            f"Successfully auto-renewed ACPS certificate for entity {entity_aic}"
        )
        
    except Exception as e:
        logger.exception(f"Failed to renew ACPS certificate for user {user_id}: {e}")
        raise e

def run_cert_renewal_cycle():
    """扫描 PostgreSQL 的 user_entities 表，检查并执行证书续签。"""
    from service.app import db
    from service.feature_flags import get_flags
    if not get_flags().cert_renewal_daemon:
        return
        
    db_instance = db.get_db()
    entities = []
    try:
        with db_instance._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, entity_id, credentials, updated_at FROM user_entities")
                rows = cur.fetchall()
                for row in rows:
                    entities.append({
                        "user_id": row[0],
                        "entity_id": row[1],
                        "credentials": row[2],
                        "updated_at": row[3]
                    })
    except Exception as e:
        logger.error(f"Failed to fetch user entities from database: {e}")
        return

    logger.info(f"Checking {len(entities)} ACPS user entities for expiration...")
    for entity in entities:
        user_id = entity["user_id"]
        entity_aic = entity["entity_id"]
        try:
            creds_json = decrypt_data(entity["credentials"])
            creds = json.loads(creds_json)
            
            from cryptography import x509
            cert = x509.load_pem_x509_certificate(creds["entity_cert"].encode("utf-8"))
            
            try:
                expiry = cert.not_valid_after_utc
            except AttributeError:
                expiry = cert.not_valid_after.replace(tzinfo=timezone.utc)
                
            days_remaining = (expiry - datetime.now(timezone.utc)).days
            
            if days_remaining <= 7:
                logger.info(f"User certificate for {user_id} (AIC={entity_aic}) expires in {days_remaining} days. Triggering renewal...")
                renew_user_certificate(user_id, entity)
            else:
                logger.debug(f"User certificate for {user_id} (AIC={entity_aic}) has {days_remaining} days remaining. Skipping.")
        except Exception as e:
            logger.error(f"Error checking/renewing certificate for user {user_id}: {e}", exc_info=True)

async def start_cert_renewal_daemon():
    """在 Host 端启动证书自动轮签的异步守护协程，每日检查一次。"""
    logger.info("Background certificate renewal daemon started.")
    await asyncio.sleep(10)
    while True:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, run_cert_renewal_cycle)
        except Exception as e:
            logger.error(f"Error in cert renewal daemon cycle: {e}", exc_info=True)
        await asyncio.sleep(86400)
