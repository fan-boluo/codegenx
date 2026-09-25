"""JWT token revocation via Redis blacklist.

Strategy A (per-token revocation):
    - On logout, add token's jti to Redis Set: jwt:revoked:{user_id}
    - Each entry has TTL = token remaining lifetime
    - verify_token checks this set before accepting

Strategy B (user-level mass revocation):
    - On "logout all devices", INCR jwt:version:{user_id} in Redis
    - Each token carries token_version in payload
    - verify_token compares token_version vs Redis version

Key design:
    - jti = SHA256(token)[:16] (deterministic, no DB needed)
    - TTL auto-cleanup ensures Redis won't grow unbounded
    - Graceful degradation: Redis errors log but don't block auth
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

import redis.asyncio as redis

from shared.config.config import get_settings
from shared import log

settings = get_settings()

JWT_REVOKED_PREFIX = "jwt:revoked"
JWT_VERSION_PREFIX = "jwt:version"


def compute_jti(token: str) -> str:
    """Compute a deterministic JWT ID from the token string.

    SHA256 truncation avoids storing the full token in Redis.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class JWTRevocationService:
    """Manage JWT token revocation."""

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    # ── per-token revocation ──────────────────────────────

    async def revoke_token(self, user_id: str, token: str) -> None:
        """Revoke a single token by adding its jti to the revoked set."""
        jti = compute_jti(token)
        key = f"{JWT_REVOKED_PREFIX}:{user_id}"
        try:
            await self._redis.sadd(key, jti)
            # Set TTL to match token lifetime so Redis auto-cleans
            expire_at = int(time.time()) + settings.jwt_expiration_hours * 3600
            await self._redis.expireat(key, expire_at)
            log.info("Token revoked: user_id={} jti={}", user_id, jti)
        except Exception as exc:
            log.error("Failed to revoke token: user_id={} jti={} error={}", user_id, jti, exc)

    async def is_revoked(self, user_id: str, token: str) -> bool:
        """Check if a token has been revoked."""
        jti = compute_jti(token)
        key = f"{JWT_REVOKED_PREFIX}:{user_id}"
        try:
            return await self._redis.sismember(key, jti)
        except Exception as exc:
            log.warning("Redis error checking revocation, defaulting to not-revoked: {}", exc)
            return False

    # ── user-level mass revocation ────────────────────────

    async def revoke_all_user_tokens(self, user_id: str) -> None:
        """Revoke all tokens for a user by bumping the version number.

        All tokens issued before this call will fail version check.
        """
        key = f"{JWT_VERSION_PREFIX}:{user_id}"
        try:
            new_version = await self._redis.incr(key)
            await self._redis.expire(key, settings.jwt_expiration_hours * 3600)
            log.info("All tokens revoked for user_id={} new_version={}", user_id, new_version)
        except Exception as exc:
            log.error("Failed to revoke all tokens: user_id={} error={}", user_id, exc)

    async def get_user_token_version(self, user_id: str) -> int:
        """Get current token version for a user."""
        key = f"{JWT_VERSION_PREFIX}:{user_id}"
        try:
            version = await self._redis.get(key)
            return int(version) if version else 0
        except Exception:
            return 0


# Singleton — lazily initialized
_revocation_service: Optional[JWTRevocationService] = None


async def get_revocation_service() -> JWTRevocationService:
    """Get or create the JWT revocation service singleton."""
    global _revocation_service
    if _revocation_service is None:
        from infra.redis.redis_client import redis_client as _redis
        _revocation_service = JWTRevocationService(_redis)
    return _revocation_service
