"""Cache key utilities."""

from __future__ import annotations


class CacheKeyUtils:
    """Cache key generation utilities."""

    @staticmethod
    def gen_user_login_state_key(session_id: str) -> str:
        """Generate user login state cache key.

        Args:
            session_id: Session ID

        Returns:
            Cache key
        """
        return f"user:login:state:{session_id}"

    @staticmethod
    def gen_user_rate_limit_key(user_id: int, action: str) -> str:
        """Generate user rate limit cache key.

        Args:
            user_id: User ID
            action: Action name

        Returns:
            Cache key
        """
        return f"user:rate_limit:{user_id}:{action}"

    @staticmethod
    def gen_app_chat_memory_key(app_id: int) -> str:
        """Generate app chat memory cache key.

        Args:
            app_id: App ID

        Returns:
            Cache key
        """
        return f"app:chat_memory:{app_id}"

    @staticmethod
    def gen_app_config_key(app_id: int) -> str:
        """Generate app configuration cache key.

        Args:
            app_id: App ID

        Returns:
            Cache key
        """
        return f"app:config:{app_id}"

    @staticmethod
    def gen_ai_model_config_key(provider_name: str, model_name: str) -> str:
        """Generate AI model configuration cache key.

        Args:
            provider_name: Provider name
            model_name: Model name

        Returns:
            Cache key
        """
        return f"ai:model_config:{provider_name}:{model_name}"