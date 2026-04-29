from __future__ import annotations

from services.discovery_cache import service_discovery_cache
from infra.nacos.nacos_client import nacos_client
from shared.config.config import get_settings
from shared.config.log_config import log


settings = get_settings()


class DiscoveryAdapter:
    def __init__(self, ttl_seconds: int = 3) -> None:
        service_discovery_cache.ttl_seconds = max(1, ttl_seconds)

    async def resolve_http_base_url(self, service_name: str, *, fallback_base_url: str) -> str:
        cache_key = self._cache_key(service_name, "http")
        try:
            return await service_discovery_cache.get(
                cache_key,
                lambda _: nacos_client.get_service_base_url(service_name, scheme="http"),
            )
        except Exception as exc:
            log.warning(
                "service discovery fallback serviceName={} protocol=http fallback={} error={}",
                service_name,
                fallback_base_url,
                exc,
            )
            return fallback_base_url

    async def resolve_grpc_target(self, service_name: str, *, fallback_target: str) -> str:
        cache_key = self._cache_key(service_name, "grpc")
        try:
            return await service_discovery_cache.get(
                cache_key,
                lambda _: nacos_client.get_service_addr(service_name),
            )
        except Exception as exc:
            log.warning(
                "service discovery fallback serviceName={} protocol=grpc fallback={} error={}",
                service_name,
                fallback_target,
                exc,
            )
            return fallback_target

    def invalidate(self, service_name: str, protocol: str) -> None:
        service_discovery_cache.invalidate(self._cache_key(service_name, protocol))

    @staticmethod
    def _cache_key(service_name: str, protocol: str) -> str:
        return f"{service_name}:{protocol}"


discovery_adapter = DiscoveryAdapter(ttl_seconds=int(settings.app_service_discovery_cache_ttl_seconds or 3))