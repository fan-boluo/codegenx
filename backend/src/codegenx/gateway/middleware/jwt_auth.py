"""JWT authentication middleware.

Supports both HS256 (symmetric, single shared secret) and RS256 (asymmetric, private/public key pair).

RS256 key loading priority (for each of private/public key):
    1. File path from env JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH (K8s Secret mount)
    2. Direct env var JWT_PRIVATE_KEY / JWT_PUBLIC_KEY (inline, not recommended for production)
    3. Fallback: use JWT_SECRET for HS256

Token revocation (logout):
    - Each token gets a jti (JWT ID) derived from SHA256(token)[:16]
    - On logout, jti is added to Redis Set jwt:revoked:{user_id} with TTL matching token expiry
    - verify_token checks Redis blacklist before accepting
    - User-level mass revocation via token_version bump (jwt:version:{user_id})

Usage:
    # HS256 (existing behavior, backward compatible)
    JWT_ALGORITHM=HS256
    JWT_SECRET=your-secret

    # RS256 with K8s Secret file mount (recommended for production)
    JWT_ALGORITHM=RS256
    JWT_PRIVATE_KEY_PATH=/etc/jwt/jwt_private.pem
    JWT_PUBLIC_KEY_PATH=/etc/jwt/jwt_public.pem
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from shared.config.config import get_settings

settings = get_settings()

JWT_ALGORITHM = settings.jwt_algorithm
JWT_EXPIRATION_HOURS = settings.jwt_expiration_hours


def _load_signing_key() -> Any:
    if JWT_ALGORITHM == "RS256":
        if settings.jwt_private_key_path and os.path.isfile(settings.jwt_private_key_path):
            return open(settings.jwt_private_key_path, "rb").read()
        if settings.jwt_private_key:
            return settings.jwt_private_key.encode("utf-8")
        return settings.jwt_secret
    return settings.jwt_secret


def _load_verifying_key() -> Any:
    if JWT_ALGORITHM == "RS256":
        if settings.jwt_public_key_path and os.path.isfile(settings.jwt_public_key_path):
            return open(settings.jwt_public_key_path, "rb").read()
        if settings.jwt_public_key:
            return settings.jwt_public_key.encode("utf-8")
        return settings.jwt_secret
    return settings.jwt_secret


SIGNING_KEY = _load_signing_key()
VERIFYING_KEY = _load_verifying_key()


class JWTUser(BaseModel):
    """JWT user payload."""
    user_id: int
    user_account: str
    user_role: str


class JWTAuth:
    """JWT authentication utilities — RS256/HS256 + token revocation."""

    @staticmethod
    def create_token(user: JWTUser, token_version: int = 0) -> str:
        """Create JWT token with jti and token_version for revocation support.

        Returns (token, jti) — jti is derived from the token itself.
        """
        now = datetime.now(timezone.utc)
        expire = now + timedelta(hours=JWT_EXPIRATION_HOURS)
        payload = {
            "user_id": user.user_id,
            "user_account": user.user_account,
            "user_role": user.user_role,
            "jti": str(uuid.uuid4()),  # unique JWT ID for precise revocation
            "token_version": token_version,
            "exp": expire,
            "iat": now,
        }
        token = jwt.encode(payload, SIGNING_KEY, algorithm=JWT_ALGORITHM)
        return token

    @staticmethod
    def decode_token(token: str) -> dict:
        """Decode and validate JWT signature only. Does NOT check revocation."""
        try:
            return jwt.decode(token, VERIFYING_KEY, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

    @staticmethod
    async def verify_token(token: str) -> JWTUser:
        """Verify token signature, expiration, AND revocation status."""
        payload = JWTAuth.decode_token(token)

        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token: missing user_id")

        # Check per-token revocation
        from codegenx.gateway.services.jwt_revocation_service import get_revocation_service
        revoke_svc = await get_revocation_service()
        if await revoke_svc.is_revoked(user_id, token):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        # Check user-level mass revocation (token_version)
        token_version = payload.get("token_version", 0)
        current_version = await revoke_svc.get_user_token_version(user_id)
        if token_version < current_version:
            raise HTTPException(status_code=401, detail="Token has been revoked (all devices)")

        return JWTUser(
            user_id=user_id,
            user_account=payload["user_account"],
            user_role=payload["user_role"],
        )

    @staticmethod
    async def get_current_user(request: Request) -> JWTUser | None:
        """Extract user from JWT token in request."""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未登录")
        token = auth_header.split(" ")[1]
        return await JWTAuth.verify_token(token)

    @staticmethod
    def extract_token_from_request(request: Request) -> str | None:
        """Extract raw JWT token from Authorization header."""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        return auth_header.split(" ")[1]


class JWTBearer(HTTPBearer):
    """JWT Bearer authentication with revocation check."""

    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> JWTUser | None:
        credentials = await super().__call__(request)
        if credentials:
            return await JWTAuth.verify_token(credentials.credentials)
        return None
