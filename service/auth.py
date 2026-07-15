"""
OIDC Authentication Module
============================
Implements the full OIDC Authorization Code + PKCE flow with Keycloak,
and issues application-level JWTs for authenticated users.

Flow:
  1. GET  /auth/login-url  → returns Keycloak authorization URL
  2. POST /auth/token       → exchanges auth code for tokens, verifies,
                              issues app JWT
  3. get_current_user()     → FastAPI dependency that verifies app JWT
"""

import os
import time
import secrets
import hashlib
import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import jwt as pyjwt
from jwt import PyJWK
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration from environment (with public-facing defaults)
# ---------------------------------------------------------------------------

# OIDC 参数：优先从环境变量/.env 读取，为空则使用预设公网默认值
_DEFAULT_OIDC_ISSUER = "http://10.106.130.104:8080/realms/yanyu"

OIDC_ISSUER = os.getenv("OIDC_ISSUER_URL") or _DEFAULT_OIDC_ISSUER

# 动态解析默认的 OIDC_CLIENT_ID
def get_default_oidc_client_id() -> str:
    from service.feature_flags import get_flags
    if get_flags().auth_mode == "oidc_pkce":
        return "yanyu-wit-cli"
    return "yanyu-wit"

OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID") or get_default_oidc_client_id()
OIDC_AUDIENCE = os.getenv("OIDC_AUDIENCE") or OIDC_CLIENT_ID
# OIDC_CLIENT_SECRET: SaaS 模式从 .env 读取；PC 模式默认为空（公共客户端 + PKCE）
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
JWKS_CACHE_TTL = int(os.getenv("OIDC_JWKS_CACHE_TTL", "3600"))

# OIDC_REDIRECT_URI: 不再从 .env 读取，改为根据实际运行端口动态拼接
# 此变量将在 get_oidc_redirect_uri() 函数中动态生成

# App-level JWT signing — 使用统一密钥管理模块
from cli.utils.secrets import (
    get_app_jwt_secret,
    get_session_secret,
    get_app_jwt_algorithm,
    get_app_jwt_expire_hours,
)

# 延迟初始化，使用函数调用获取密钥（支持动态生成）
def _get_app_jwt_secret() -> str:
    return get_app_jwt_secret()

def _get_app_jwt_algorithm() -> str:
    return get_app_jwt_algorithm()

def _get_app_jwt_expire_hours() -> int:
    return get_app_jwt_expire_hours()

# Derived endpoints (standard OIDC)
KEYCLOAK_AUTH_URL = f"{OIDC_ISSUER}/protocol/openid-connect/auth"
KEYCLOAK_TOKEN_URL = f"{OIDC_ISSUER}/protocol/openid-connect/token"


def get_oidc_redirect_uri() -> str:
    """根据当前服务实际运行端口动态拼接 OIDC 回调地址。"""
    from cli.utils.port import resolve_port
    port = resolve_port()
    return f"http://localhost:{port}/callback"


# FastAPI security scheme
_security = HTTPBearer(
    scheme_name="App Bearer Token",
    description="Pass the app JWT token obtained after OIDC login.",
    auto_error=True,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TokenUser:
    """
    Represents the authenticated user extracted from a verified JWT.
    This is the identity object injected into route handlers.
    """

    sub: str
    issuer: str
    email: str | None = None
    name: str | None = None
    preferred_username: str | None = None
    roles: list[str] = field(default_factory=list)
    raw_claims: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.name or self.preferred_username or self.email or self.sub


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# JWKS Provider (for verifying Keycloak's id_token)
# ---------------------------------------------------------------------------


class _JWKSProvider:
    """
    Manages JWKS retrieval and caching from the OIDC provider.
    Fetches from the OIDC provider's discovery endpoint with caching.
    """

    def __init__(self):
        self._jwks: dict | None = None
        self._jwks_fetched_at: float = 0

    async def get_signing_key(self, kid: str) -> dict:
        """Return the JWK matching the given key ID."""
        jwks = await self._get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        # If not found, force refresh once (key rotation)
        jwks = await self._get_jwks(force_refresh=True)
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
        raise HTTPException(
            status_code=401,
            detail="Token 签名密钥未找到 (kid 不匹配)",
        )

    async def _get_jwks(self, force_refresh: bool = False) -> dict:
        now = time.time()
        cache_valid = (
            self._jwks is not None
            and (now - self._jwks_fetched_at) < JWKS_CACHE_TTL
            and not force_refresh
        )
        if cache_valid:
            return self._jwks

        return await self._fetch_remote_jwks()

    async def _fetch_remote_jwks(self) -> dict:
        """Fetch JWKS from the OIDC provider's discovery endpoint."""
        discovery_url = f"{OIDC_ISSUER}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Discover jwks_uri
                disc_resp = await client.get(discovery_url)
                disc_resp.raise_for_status()
                jwks_uri = disc_resp.json()["jwks_uri"]

                # Fetch JWKS
                jwks_resp = await client.get(jwks_uri)
                jwks_resp.raise_for_status()
                self._jwks = jwks_resp.json()
                self._jwks_fetched_at = time.time()
                logger.info(
                    "Fetched JWKS from %s (%d keys)",
                    jwks_uri,
                    len(self._jwks.get("keys", [])),
                )
                return self._jwks
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch JWKS: %s", exc)
            # If we have a stale cache, use it as fallback
            if self._jwks is not None:
                logger.warning("Using stale JWKS cache as fallback")
                return self._jwks
            raise HTTPException(
                status_code=503,
                detail="无法获取 OIDC 签名密钥，认证服务暂时不可用",
            )


# ---------------------------------------------------------------------------
# OIDC ID Token Verifier
# ---------------------------------------------------------------------------


class OIDCAuthenticator:
    """Verifies Keycloak ID Tokens."""

    def __init__(self):
        self._jwks_provider = _JWKSProvider()

    async def verify_id_token(self, token: str, expected_nonce: str | None = None) -> dict:
        """
        Verify a Keycloak ID Token and return its payload claims.

        Args:
            token: The raw JWT id_token string from Keycloak.
            expected_nonce: If provided, verify the nonce claim matches.

        Returns:
            dict of verified claims.
        """
        try:
            # Read unverified header to get kid
            unverified_header = pyjwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            if not kid:
                raise HTTPException(status_code=401, detail="ID Token 缺少 kid 头")

            # Get the matching public key
            jwk_data = await self._jwks_provider.get_signing_key(kid)

            # Build a PyJWK and decode
            signing_key = PyJWK.from_dict(jwk_data)

            # Keycloak id_token audience is the client_id
            payload = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=OIDC_CLIENT_ID,
                issuer=OIDC_ISSUER,
                options={
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "require": ["sub", "exp", "iss"],
                },
            )

            # Verify nonce
            if expected_nonce is not None:
                token_nonce = payload.get("nonce")
                if token_nonce != expected_nonce:
                    raise HTTPException(
                        status_code=401,
                        detail="ID Token nonce 不匹配，可能存在重放攻击",
                    )

            return payload

        except pyjwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="ID Token 已过期")
        except pyjwt.InvalidAudienceError:
            raise HTTPException(status_code=401, detail="ID Token audience 不匹配")
        except pyjwt.InvalidIssuerError:
            raise HTTPException(status_code=401, detail="ID Token issuer 不匹配")
        except pyjwt.DecodeError as exc:
            raise HTTPException(status_code=401, detail=f"ID Token 解码失败: {exc}")
        except pyjwt.InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=f"ID Token 无效: {exc}")


# ---------------------------------------------------------------------------
# App Token (our own JWT, issued after OIDC verification)
# ---------------------------------------------------------------------------


def issue_app_token(claims: dict) -> str:
    """
    Sign and return an application-level JWT.

    The app token contains user identity claims and is what the frontend
    stores and sends on subsequent API calls.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": claims["sub"],
        "iss": "yanyu-wit",
        "name": claims.get("name"),
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "roles": claims.get("realm_access", {}).get("roles", []),
        "iat": now,
        "exp": now + timedelta(hours=_get_app_jwt_expire_hours()),
    }
    return pyjwt.encode(payload, _get_app_jwt_secret(), algorithm=_get_app_jwt_algorithm())


def verify_app_token(token: str) -> TokenUser:
    """
    Verify an application-level JWT and return a TokenUser.

    This is used by the get_current_user dependency to authenticate
    subsequent API calls from the frontend.
    """
    try:
        payload = pyjwt.decode(
            token,
            _get_app_jwt_secret(),
            algorithms=[_get_app_jwt_algorithm()],
            issuer="yanyu-wit",
            options={
                "verify_exp": True,
                "verify_iss": True,
                "require": ["sub", "exp", "iss"],
            },
        )
        return TokenUser(
            sub=payload["sub"],
            issuer=payload["iss"],
            email=payload.get("email"),
            name=payload.get("name"),
            preferred_username=payload.get("preferred_username"),
            roles=payload.get("roles", []),
            raw_claims=payload,
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期，请重新登录")
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Token 无效: {exc}")


# ---------------------------------------------------------------------------
# OIDC Flow Helpers (used by the auth router)
# ---------------------------------------------------------------------------


def build_authorization_url(state: str, nonce: str, code_challenge: str) -> str:
    """Build the Keycloak authorization URL with PKCE."""
    from urllib.parse import urlencode

    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": get_oidc_redirect_uri(),
    }
    return f"{KEYCLOAK_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """
    Exchange an authorization code for tokens at the Keycloak token endpoint.

    Sends both client_secret (for confidential clients) and code_verifier
    (PKCE). Keycloak accepts both simultaneously.
    """
    data = {
        "grant_type": "authorization_code",
        "client_id": OIDC_CLIENT_ID,
        "code": code,
        "redirect_uri": get_oidc_redirect_uri(),
        "code_verifier": code_verifier,
    }
    if OIDC_CLIENT_SECRET:
        data["client_secret"] = OIDC_CLIENT_SECRET

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            KEYCLOAK_TOKEN_URL,
            data=data,  # application/x-www-form-urlencoded
        )
        if resp.status_code != 200:
            error_detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            logger.error("Token exchange failed: %s %s", resp.status_code, error_detail)
            raise HTTPException(
                status_code=401,
                detail=f"Token 换取失败: {error_detail}",
            )
        return resp.json()


# ---------------------------------------------------------------------------
# Singleton & FastAPI dependency
# ---------------------------------------------------------------------------

_authenticator = OIDCAuthenticator()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> TokenUser:
    """
    FastAPI dependency — verifies the app Bearer token and returns the
    authenticated user. Inject this into any route that requires auth.

    Usage:
        @app.get("/protected")
        async def protected(user: TokenUser = Depends(get_current_user)):
            ...
    """
    return verify_app_token(credentials.credentials)


def get_authenticator() -> OIDCAuthenticator:
    """Return the singleton OIDCAuthenticator instance."""
    return _authenticator
