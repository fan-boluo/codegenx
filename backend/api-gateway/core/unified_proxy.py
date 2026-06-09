from __future__ import annotations

from typing import Any, Dict
import httpx
from fastapi.responses import StreamingResponse

from services.discovery_adapter import discovery_adapter
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.service_invocation import ServiceInvocationError

settings = get_settings()
def camel_to_snake(data: dict) -> dict:
    import re
    return {re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower(): v for k, v in data.items()}

class UnifiedProxy:
    """统一代理类，处理 HTTP 和 gRPC 请求转发"""

    def __init__(self):
        self.settings = get_settings()

    async def forward_http_request(
        self,
        service_name: str,
        path: str,
        method: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        trace_id: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any] | StreamingResponse:
        """
        转发 HTTP 请求到目标服务

        Args:
            service_name: 服务名称（用于服务发现）
            path: 请求路径
            method: HTTP 方法
            headers: 请求头
            params: 查询参数
            json_body: JSON 请求体
            trace_id: 链路追踪 ID
            timeout: 超时时间

        Returns:
            响应数据或流式响应
        """
        # 构建请求头
        request_headers = headers or {}
        if trace_id:
            request_headers["X-Trace-Id"] = trace_id

        # 获取服务基础 URL
        base_url = await discovery_adapter.resolve_http_base_url(
            service_name,
            fallback_base_url=self._get_service_base_url(service_name)
        )

        # 构建完整 URL
        url = f"{base_url}{path}"

        # 发起请求
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    json=json_body,
                )
            except Exception as exc:
                log.error(
                    "HTTP 请求失败 service={} path={} method={} error={}",
                    service_name,
                    path,
                    method,
                    exc
                )
                raise BusinessException(
                    ErrorCode.SYSTEM_ERROR,
                    message=f"服务调用失败: {str(exc)}"
                )

        # 处理响应
        content_type = response.headers.get("Content-Type", "")

        # SSE 流式响应
        if "text/event-stream" in content_type:
            async def stream_response():
                async for chunk in response.aiter_bytes():
                    yield chunk

            return StreamingResponse(
                stream_response(),
                status_code=response.status_code,
                headers=dict(response.headers)
            )

        # 普通 JSON 响应
        try:
            data = response.json()
        except Exception as exc:
            log.error(
                "响应解析失败 service={} status={} body={}",
                service_name,
                response.status_code,
                response.text[:200]
            )
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message="服务响应格式错误"
            )

        return data

    async def forward_grpc_request(
        self,
        service_name: str,
        method_name: str,
        request_data: dict[str, Any],
        proto_name: str,
    ) -> dict[str, Any]:
        """
        转发 gRPC 请求到目标服务

        Args:
            service_name: 服务名称
            method_name: gRPC 方法名
            request_data: 请求数据
            proto_name: proto 文件名

        Returns:
            响应数据
        """
        # 根据服务名称加载对应的 gRPC 客户端
        client = self._get_grpc_client(service_name, proto_name)

        # 调用对应的方法
        if hasattr(client, method_name):
            method = getattr(client, method_name)
            log.debug("原始参数：{}", request_data)
            request_data = camel_to_snake(request_data)
            log.debug("转换后参数：{}", request_data)
            return await method(**request_data)
        else:
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message=f"方法 {method_name} 不存在"
            )

    def _get_service_base_url(self, service_name: str) -> str:
        """获取服务基础 URL"""
        service_configs = {
            "user-service": f"http://{settings.user_service_host}:{settings.user_service_port}",
            "app-service": f"http://{settings.app_service_host}:{settings.app_service_port}",
            "ai-service": f"http://{settings.ai_service_host}:{settings.ai_service_http_port}",
        }
        return service_configs.get(service_name, "")

    def _get_grpc_client(self, service_name: str, proto_name: str):
        """获取 gRPC 客户端实例"""
        # 根据服务名称导入对应的客户端
        if service_name == "user-service":
            from grpc_client.user_service_client import UserServiceGrpcClient
            return UserServiceGrpcClient()
        else:
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message=f"不支持的 gRPC 服务: {service_name}"
            )


# 创建全局代理实例
unified_proxy = UnifiedProxy()