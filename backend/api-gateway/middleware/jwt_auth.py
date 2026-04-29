"""JWT authentication middleware."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from shared.config.config import get_settings

settings = get_settings()

JWT_SECRET = settings.jwt_secret
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


class JWTUser(BaseModel):
    """JWT user payload."""
    user_id: int
    user_account: str
    user_role: str


class JWTAuth:
    """JWT authentication utilities."""

    @staticmethod
    def create_token(user: JWTUser) -> str:
        """Create JWT token."""
        expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
        payload = {
            "user_id": user.user_id,
            "user_account": user.user_account,
            "user_role": user.user_role,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_token(token: str) -> JWTUser:
        """Verify and decode JWT token."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return JWTUser(
                user_id=payload["user_id"],
                user_account=payload["user_account"],
                user_role=payload["user_role"]
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

    @staticmethod
    async def get_current_user(request: Request) -> JWTUser | None:
        """Extract user from JWT token in request."""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未登录")

        token = auth_header.split(" ")[1]
        return JWTAuth.verify_token(token)


class JWTBearer(HTTPBearer):
    """JWT Bearer authentication."""

    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> JWTUser | None:
        credentials = await super().__call__(request)
        if credentials:
            return JWTAuth.verify_token(credentials.credentials)
        return None