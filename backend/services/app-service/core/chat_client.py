from __future__ import annotations

import httpx

from infra.nacos.nacos_client import nacos_client
from shared.config.config import get_settings
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode


settings = get_settings()


class ChatServiceClient:
    def __init__(self) -> None:
        self.service_name = settings.chat_service_name
        self.fallback_base_url = f"http://{settings.chat_service_host}:{settings.chat_service_http_port}"
        self._timeout = httpx.Timeout(30.0)

    async def delete_chat_history_by_app_id(self, app_id: int) -> bool:
        base_url = await self._resolve_base_url()
        url = f"{base_url}/api/chatHistory/internal/app/{app_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.delete(url)
                response.raise_for_status()
            except Exception as exc:
                raise BusinessException(ErrorCode.SYSTEM_ERROR, f"调用聊天服务删除历史失败: {exc}") from exc

        body = response.json()
        if int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())) != ErrorCode.SUCCESS.get_code():
            raise BusinessException(int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())), body.get("message") or "调用聊天服务删除历史失败")
        return bool(body.get("data"))

    async def _resolve_base_url(self) -> str:
        try:
            return await nacos_client.get_service_base_url(self.service_name)
        except Exception:
            return self.fallback_base_url