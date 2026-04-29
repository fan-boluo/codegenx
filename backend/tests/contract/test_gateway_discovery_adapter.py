from __future__ import annotations

import sys
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = BACKEND_ROOT / "api-gateway"
for candidate in (str(GATEWAY_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from services.discovery_adapter import DiscoveryAdapter


class DiscoveryAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_grpc_cache_keys_are_isolated(self) -> None:
        adapter = DiscoveryAdapter(ttl_seconds=3)
        with patch("services.discovery_adapter.nacos_client.get_service_base_url", new=AsyncMock(return_value="http://app-service:8004")) as http_resolver, patch(
            "services.discovery_adapter.nacos_client.get_service_addr",
            new=AsyncMock(return_value="user-service:50051"),
        ) as grpc_resolver:
            http_target = await adapter.resolve_http_base_url("app-service", fallback_base_url="http://localhost:8004")
            grpc_target = await adapter.resolve_grpc_target("app-service", fallback_target="localhost:50052")
            self.assertEqual(http_target, "http://app-service:8004")
            self.assertEqual(grpc_target, "user-service:50051")
            http_resolver.assert_awaited_once()
            grpc_resolver.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()