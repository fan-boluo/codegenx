from __future__ import annotations

import asyncio

from infra.nacos.nacos_client import nacos_client
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.utils.tools import get_local_ip


settings = get_settings()


class ChatServiceRegistry:
    def __init__(self) -> None:
        self.service_name = settings.chat_service_name or "chat-service"
        self.host = settings.chat_service_register_host or get_local_ip() or settings.chat_service_host or "127.0.0.1"
        self.port = int(settings.chat_service_register_port or settings.chat_service_http_port)
        self._heartbeat_task: asyncio.Task | None = None

    async def startup(self) -> None:
        try:
            await nacos_client.register_instance(self.service_name, self.host, self.port)
            log.info(
                "chat-service registered to nacos service={} instance={}:{} namespace={}",
                self.service_name,
                self.host,
                self.port,
                settings.nacos_namespace,
            )
        except Exception as exc:
            log.warning("chat-service nacos registration failed (non-fatal): {}", exc)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def shutdown(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        try:
            await nacos_client.deregister_instance(self.service_name, self.host, self.port)
            log.info("chat-service deregistered from nacos service={} instance={}:{}", self.service_name, self.host, self.port)
        except Exception as exc:
            log.warning("chat-service deregister failed service={} instance={}:{} error={}", self.service_name, self.host, self.port, exc)

    async def _heartbeat_loop(self) -> None:
        interval = max(1, int(settings.nacos_heartbeat_interval_seconds or 5))
        while True:
            try:
                await nacos_client.heartbeat(self.service_name, self.host, self.port)
            except Exception as exc:
                log.warning("chat-service nacos heartbeat failed service={} instance={}:{} error={}", self.service_name, self.host, self.port, exc)
            await asyncio.sleep(interval)