"""`wit init` — 带有 OIDC PKCE 登录的交互式首次设置向导。

创建 ~/.yanyu-wit/ 结构，并根据用户偏好写入 config.toml。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from cli.config import (
    YANYU_WIT_HOME,
    ensure_home_dir,
    get_account_config_path,
    get_account_workspace_dir,
    get_default_config_toml,
    set_active_account,
)
from cli.utils.credentials import save_credential


# ---------------------------------------------------------------------------
# PKCE and JWT Decoders
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """生成 PKCE code_verifier 和 code_challenge (S256)。"""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return code_verifier, code_challenge


def decode_jwt_payload_no_verify(token: str) -> dict:
    """在不验证签名的情况下解码 JWT 负载（用于提取声明）。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Local Loopback Callback Server
# ---------------------------------------------------------------------------

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>登录成功 - Yanyu-Wit CLI</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background-color: #f5f5f7; color: #1d1d1f; }
                .card { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 30px rgba(0,0,0,0.03); text-align: center; max-width: 400px; border: 1px dashed rgba(0,0,0,0.05); }
                h1 { color: #34c759; margin-top: 0; font-size: 24px; }
                p { color: #86868b; line-height: 1.6; font-size: 14px; margin-bottom: 20px; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>🎉 登录授权成功！</h1>
                <p>Yanyu-Wit CLI 已经安全地获取了您的身份凭证。</p>
                <p style="font-weight: 500; color: #1d1d1f;">您可以安全地关闭此浏览器窗口，并返回终端继续操作。</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
        self.server.code_queue.put((code, state))

    def log_message(self, format, *args):
        # Suppress logging to prevent terminal pollution
        pass


class CallbackServer(HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_queue = queue.Queue()


# ---------------------------------------------------------------------------
# Command Registration & Execution
# ---------------------------------------------------------------------------

def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `init` 子命令。"""
    parser = subparsers.add_parser(
        "init",
        help="首次初始化 ~/.yanyu-wit/ 配置目录（包含 OIDC 登录和模型配置）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制重新初始化（覆盖现有配置）",
    )
    parser.set_defaults(func=execute)


def execute(args: argparse.Namespace) -> None:
    """运行初始化向导。"""
    print("🚀 欢迎使用 Yanyu-Wit！正在启动初始化程序...")
    print()

    # Step 1: Create main directory (excluding default_user creation)
    YANYU_WIT_HOME.mkdir(parents=True, exist_ok=True)

    # Prepare OIDC config
    oidc_issuer = os.getenv("OIDC_ISSUER_URL") or "http://10.106.130.104:8080/realms/yanyu"
    client_id = os.getenv("OIDC_CLIENT_ID") or "yanyu-wit-cli"
    client_secret = os.getenv("OIDC_CLIENT_SECRET") or ""
    redirect_uri = "http://localhost:8089/callback"
    auth_url_base = f"{oidc_issuer}/protocol/openid-connect/auth"
    token_url = f"{oidc_issuer}/protocol/openid-connect/token"

    print("🔑 步骤 1/3: 引导 OIDC / Keycloak 登录授权")
    
    # Generate PKCE and session parameters
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_uri,
    }
    auth_url = f"{auth_url_base}?{urllib.parse.urlencode(params)}"

    print("   👉 请在打开的浏览器窗口中完成登录。如果浏览器没有自动打开，请手动复制并访问以下链接：")
    print(f"\n   🔗 {auth_url}\n")

    # Start local redirect server
    code = None
    try:
        with CallbackServer(("127.0.0.1", 8089), CallbackHandler) as httpd:
            # Automatically open browser
            webbrowser.open(auth_url)
            
            httpd.timeout = 2.0  # Check for timeouts periodically
            start_time = time.time()
            timeout_duration = 120  # 2 minutes timeout
            
            while time.time() - start_time < timeout_duration:
                httpd.handle_request()
                if not httpd.code_queue.empty():
                    code, state_rec = httpd.code_queue.get()
                    if state_rec != state:
                        print("   ⚠️ 警告：检测到不一致的 State 标识，可能存在安全风险。已丢弃。")
                        code = None
                        continue
                    break
            else:
                print("❌ 错误：登录超时，未能在 2 分钟内完成授权。")
                sys.exit(1)
    except Exception as e:
        print(f"❌ 错误：启动本地回调监听失败 ({e})。请检查 8089 端口是否被占用。")
        sys.exit(1)

    if not code:
        print("❌ 错误：未能成功获取授权码。")
        sys.exit(1)

    print("⏳ 正在换取身份令牌 (Access Token)...")
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    try:
        response = requests.post(token_url, data=payload, timeout=15)
        if response.status_code != 200:
            print(f"❌ 登录换牌失败 ({response.status_code}): {response.text}")
            sys.exit(1)
        
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            print("❌ 登录失败：未返回 access_token")
            sys.exit(1)

        print("   ✅ 登录授权成功！")
        
        # Decode claims to extract preferred_username & sub UUID
        claims = decode_jwt_payload_no_verify(access_token)
        username = claims.get("preferred_username")
        if not username:
            print("❌ 登录失败：JWT 中未包含 preferred_username 声明")
            sys.exit(1)
        sub_uuid = claims.get("sub") or username

        print(f"   👤 登录用户: {username}")

        # Step 2: Switch active account & initialize folder structure
        set_active_account(username)
        ensure_home_dir(username)

        # Save complete token set to credentials (unified approach, no .kc_token)
        save_credential("flux", {
            "access_token": access_token,
            "id_token": token_data.get("id_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "username": username,
        })
        print(f"   💾 凭证已安全保存")

        # Step 3: Interactive Model Configuration
        print("\n📦 步骤 2/3: 模型配置")

        # Provider type selection
        valid_types = ("openai", "anthropic", "google", "ollama")
        provider_type = ""
        while provider_type not in valid_types:
            provider_type = input(f"  Provider 类型 [{'/'.join(valid_types)}] (openai): ").strip().lower() or "openai"
            if provider_type not in valid_types:
                print(f"  ⚠️ 不支持的类型，请选择: {', '.join(valid_types)}")

        # Type-specific defaults
        type_defaults = {
            "openai": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com", "need_key": True},
            "anthropic": {"model": "claude-sonnet-4-20250514", "base_url": "", "need_key": True},
            "google": {"model": "gemini-2.5-flash", "base_url": "", "need_key": True},
            "ollama": {"model": "qwen3:32b", "base_url": "http://localhost:11434/v1", "need_key": False},
        }
        defaults = type_defaults[provider_type]

        model_name = input(f"  Model name [{defaults['model']}]: ").strip() or defaults["model"]
        base_url = input(f"  API Base URL [{defaults['base_url'] or '(默认)'}]: ").strip() or defaults["base_url"]

        if defaults["need_key"]:
            api_key = input("  API Key: ").strip()
        else:
            api_key = input("  API Key [ollama]: ").strip() or "ollama"

        # Write config
        config_path = get_account_config_path(username)

        # Use the new providers-based config template
        config_content = get_default_config_toml(
            username=username,
            provider_type=provider_type,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
        )
        # Fill in user_id with sub UUID
        config_content = config_content.replace(
            f'user_id = "{username}"',
            f'user_id = "{sub_uuid}"',
        )
        config_path.write_text(config_content, encoding="utf-8")


        print()
        # Step 4: Port Configuration
        print("\n⚙️  步骤 3/4: 服务端口配置")
        from cli.utils.port import DEFAULT_PORT, save_port_to_global_config
        port_input = input(f"  默认服务端口 [{DEFAULT_PORT}]: ").strip()
        if port_input:
            try:
                port_val = int(port_input)
                save_port_to_global_config(port_val)
                print(f"   ✅ 服务端口已设置为: {port_val}")
            except ValueError:
                print(f"   ⚠️  无效的端口号，将使用默认端口 {DEFAULT_PORT}")
        else:
            print(f"   ℹ️  使用默认端口: {DEFAULT_PORT}")

        print()
        print(f"🎉 步骤 4/4: 写入配置完成")
        print(f"   ✅ 配置已保存到: {config_path}")
        print(f"   📂 用户数据目录: {YANYU_WIT_HOME}")
        print()
        print("🎉 恭喜！Yanyu-Wit 初始化全部成功！")
        print("💡 您现在可以运行 `wit start` 启动服务，或 `wit -n \"您的问题\"` 开启对话！")
        print()

    except Exception as e:
        print(f"❌ 错误：登录或写入配置时发生异常 ({e})")
        sys.exit(1)
