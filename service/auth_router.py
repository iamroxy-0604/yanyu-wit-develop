"""
Auth Router
=============
Implements the OIDC Authorization Code + PKCE login flow endpoints.

  GET  /auth/login-url  → Generate & return the Keycloak authorization URL
  POST /auth/token       → Exchange auth code for tokens, verify, issue app JWT
"""

import secrets
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .auth import (
    generate_pkce_pair,
    build_authorization_url,
    exchange_code_for_tokens,
    issue_app_token,
    get_authenticator,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginUrlResponse(BaseModel):
    """Response for GET /auth/login-url."""
    url: str


class TokenRequest(BaseModel):
    """Request body for POST /auth/token."""
    code: str
    state: str


class TokenResponse(BaseModel):
    """Response for POST /auth/token."""
    access_token: str
    token_type: str = "Bearer"
    user: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/login-url", response_model=LoginUrlResponse)
async def get_login_url(request: Request):
    """
    Step 1-3: Generate PKCE params, state, nonce, store in session,
    and return the Keycloak authorization URL.
    """
    # Generate security parameters
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()

    # Store in session for later verification
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce
    request.session["oidc_code_verifier"] = code_verifier

    # Build the authorization URL
    auth_url = build_authorization_url(
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
    )

    logger.info("Generated login URL (state=%s...)", state[:8])
    return LoginUrlResponse(url=auth_url)


@router.post("/token", response_model=TokenResponse)
async def exchange_token(body: TokenRequest, request: Request):
    """
    Step 7-9: Verify state, exchange auth code for tokens at Keycloak,
    verify the ID token, and issue an app-level JWT.
    """
    # --- Step 8a: Verify state ---
    session_state = request.session.get("oidc_state")
    if not session_state:
        raise HTTPException(
            status_code=400,
            detail="Session 中没有找到 state，请重新发起登录",
        )
    if body.state != session_state:
        raise HTTPException(
            status_code=400,
            detail="State 不匹配，可能是 CSRF 攻击",
        )

    # Retrieve session values
    code_verifier = request.session.get("oidc_code_verifier")
    expected_nonce = request.session.get("oidc_nonce")

    if not code_verifier:
        raise HTTPException(
            status_code=400,
            detail="Session 中没有找到 code_verifier，请重新发起登录",
        )

    # --- Step 8b: Exchange code for tokens at Keycloak ---
    token_data = await exchange_code_for_tokens(
        code=body.code,
        code_verifier=code_verifier,
    )

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail="Keycloak 未返回 id_token",
        )

    # --- Step 8c & 8d: Verify ID token + nonce ---
    authenticator = get_authenticator()
    claims = await authenticator.verify_id_token(
        token=id_token,
        expected_nonce=expected_nonce,
    )

    # --- Step 9: Issue our own app token ---
    app_token = issue_app_token(claims)

    # Save the Keycloak access_token to the user's workspace for agent skills
    # This mirrors the behavior of CLI `yanyu-wit login` for consistency
    username = claims.get("preferred_username") or claims.get("sub")
    if username:
        from pathlib import Path
        from cli.config import (
            set_active_account,
            ensure_home_dir,
            get_account_workspace_dir,
            get_account_config_path,
            get_default_config_toml,
            config_set,
        )
        from cli.utils.credentials import save_credential
        
        from service.feature_flags import get_flags
        
        # Switch globally active account and initialize folders
        if get_flags().auth_mode == "oidc_pkce":
            set_active_account(username)
        ensure_home_dir(username)
        
        # Initialize account config if missing
        config_path = get_account_config_path(username)
        if not config_path.exists():
            config_path.write_text(get_default_config_toml(username))
            logger.info("Initialized default config for OIDC user: %s", username)
        
        access_token = token_data.get("access_token", "")
        
        # Save complete token set to credentials store (unified approach)
        # 不再写入 workspace/.kc_token（冗余设计已消除）
        save_credential("flux", {
            "access_token": access_token,
            "id_token": token_data.get("id_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "username": username,
        })
        
        # 3. Store Keycloak sub UUID in identity config for Flux API compatibility
        config_set("identity.user_id", claims.get("sub") or username, username=username)
        
        # 4. Trigger ACPS entity registration in background for SaaS mode
        if get_flags().cert_renewal_daemon:
            from service.entity_manager import register_entity_if_needed
            import threading
            threading.Thread(target=register_entity_if_needed, args=(username,), daemon=True).start()
        


    # Clear session OIDC data (one-time use)
    request.session.pop("oidc_state", None)
    request.session.pop("oidc_nonce", None)
    request.session.pop("oidc_code_verifier", None)

    logger.info("User authenticated: sub=%s, name=%s", claims.get("sub"), claims.get("name"))

    return TokenResponse(
        access_token=app_token,
        user={
            "sub": claims.get("sub"),
            "name": claims.get("name"),
            "email": claims.get("email"),
            "preferred_username": claims.get("preferred_username"),
        },
    )
