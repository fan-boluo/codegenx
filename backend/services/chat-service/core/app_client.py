from __future__ import annotations

import time

import httpx

from infra.nacos.nacos_client import nacos_client
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.enums.code_gen_type import CodeGenTypeEnum
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.code import GeneratedCodeSaveRequest


settings = get_settings()


class AppServiceClient:
    def __init__(self) -> None:
        self.service_name = settings.app_service_name
        self.fallback_base_url = f"http://{settings.app_service_host}:{settings.app_service_http_port}"
        self._timeout = httpx.Timeout(120.0)

    async def save_generated_code(
        self,
        *,
        app_id: int,
        code_gen_type: CodeGenTypeEnum | None,
        code_content: str,
        trace_id: str | None = None,
    ) -> dict[str, str | None]:
        payload = GeneratedCodeSaveRequest(
            appId=app_id,
            codeGenType=code_gen_type.value if code_gen_type else None,
            content=code_content,
        )
        base_url = await self._resolve_base_url()
        url = f"{base_url}/internal/app/code/save"
        headers: dict[str, str] = {}
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        started_at = time.perf_counter()
        log.info(
            "chat-service -> app-service request traceId={} appId={} codeGenType={} url={} contentLen={} preview={}",
            trace_id,
            app_id,
            code_gen_type.value if code_gen_type else None,
            url,
            len(code_content),
            _preview_text(code_content),
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=payload.model_dump(by_alias=True))
                response.raise_for_status()
            except Exception as exc:
                log.exception(
                    "chat-service -> app-service request failed traceId={} appId={} codeGenType={} url={}",
                    trace_id,
                    app_id,
                    code_gen_type.value if code_gen_type else None,
                    url,
                )
                raise BusinessException(ErrorCode.SYSTEM_ERROR, f"调用应用服务保存代码失败: {exc}") from exc

        body = response.json()
        if int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())) != ErrorCode.SUCCESS.get_code():
            raise BusinessException(int(body.get("code", ErrorCode.SYSTEM_ERROR.get_code())), body.get("message") or "应用服务保存代码失败")

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        data = body.get("data") or {}
        log.info(
            "chat-service <- app-service response traceId={} appId={} status={} latencyMs={} savedDir={} buildDir={}",
            trace_id,
            app_id,
            response.status_code,
            latency_ms,
            data.get("savedDir"),
            data.get("buildDir"),
        )
        return {
            "savedDir": data.get("savedDir"),
            "buildDir": data.get("buildDir"),
        }

    async def _resolve_base_url(self) -> str:
        try:
            return await nacos_client.get_service_base_url(self.service_name)
        except Exception:
            return self.fallback_base_url


def _preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    return compact[:limit]