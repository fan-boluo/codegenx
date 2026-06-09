from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, List, Dict
import yaml
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from core.constants import RATE_LIMIT_API_PREFIX
from middleware.auth import require_login, JWTUser
from services.discovery_adapter import discovery_adapter
from services.rate_limit_service import RateLimitService
from shared.config.config import get_settings
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode

from .unified_proxy import unified_proxy


# 加载路由配置
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "routes.yaml"


class RouteConfig:
    """路由配置项"""

    def __init__(self, config: dict):
        self.path = config.get("path", "")
        self.service_name = config.get("service_name", "")
        self.protocol = config.get("protocol", "http")
        self.target_port = config.get("target_port", 50051)
        self.http_port = config.get("http_port", 8004)
        self.auth_required = config.get("auth_required", True)
        self.auth_whitelist = config.get("auth_whitelist", [])
        self.rate_limit = config.get("rate_limit", {})
        self.description = config.get("description", "")

    def get_service_base_url(self) -> str:
        """获取服务基础URL"""
        if self.protocol == "grpc":
            return f"{self.service_name}:{self.target_port}"
        return f"http://{self.service_name}:{self.http_port}"


class RouteLoader:
    """路由配置加载器"""

    _instance = None
    _routes: List[RouteConfig] = []
    _route_map: Dict[str, RouteConfig] = {}
    _internal_routes: set[str] = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def load_routes(cls) -> None:
        """加载路由配置"""
        if not CONFIG_PATH.exists():
            log.warning("路由配置文件不存在: {}", CONFIG_PATH)
            return

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        cls._routes = []
        cls._route_map = {}
        cls._internal_routes = {"/health", "/health/"}

        # 加载路由规则
        for route_config in config.get("routes", []):
            rc = RouteConfig(route_config)
            cls._routes.append(rc)

            # 建立路径映射
            if rc.path not in cls._route_map:
                cls._route_map[rc.path] = rc

        log.info("加载路由配置完成: {} 条规则", len(cls._routes))

    @classmethod
    def get_route(cls, path: str) -> Optional[RouteConfig]:
        """根据路径获取路由配置（支持前缀匹配）"""
        # 先尝试精确匹配
        if path in cls._route_map:
            return cls._route_map[path]

        # 尝试前缀匹配（按长度排序，优先匹配最长的前缀）
        sorted_paths = sorted(cls._route_map.keys(), key=len, reverse=True)
        for prefix in sorted_paths:
            if path.startswith(prefix):
                return cls._route_map[prefix]

        return None

    @classmethod
    def get_all_routes(cls) -> List[RouteConfig]:
        """获取所有路由配置"""
        return cls._routes

    @classmethod
    def is_internal_route(cls, path: str) -> bool:
        """是否为内部路由（如健康检查）"""
        return path in cls._internal_routes


# 初始化加载路由配置
RouteLoader.load_routes()


class DynamicRouter:
    """动态路由转发器"""

    def __init__(self):
        self.settings = get_settings()
        self.rate_limit_service: Optional[RateLimitService] = None

    async def _init_rate_limit(self, request: Request) -> None:
        """初始化限流服务"""
        if self.rate_limit_service is None:
            from infra.redis.redis_client import redis_client
            self.rate_limit_service = RateLimitService(redis_client)

    async def check_rate_limit(self, request: Request, route_config: RouteConfig) -> bool:
        """检查限流"""
        if self.rate_limit_service is None:
            await self._init_rate_limit(request)

        # 检查 API 限流
        api_limit = route_config.rate_limit
        if api_limit:
            key = f"{RATE_LIMIT_API_PREFIX}{request.url.path}"
            return await self.rate_limit_service.try_acquire(
                key,
                api_limit.get("max_requests", 100),
                api_limit.get("window_seconds", 60)
            )
        return True

    async def forward_request(
        self,
        request: Request,
        route_config: RouteConfig,
        grpc_method_name: Optional[str] = None
    ) -> JSONResponse | StreamingResponse:
        """转发请求到目标服务"""
        # 检查限流
        if not await self.check_rate_limit(request, route_config):
            raise BusinessException(
                ErrorCode.RATE_LIMIT_ERROR,
                message="请求过于频繁，请稍后再试"
            )

        # 根据协议处理
        if route_config.protocol == "grpc":
            return await self._forward_grpc(request, route_config, grpc_method_name)
        elif route_config.protocol == "http":
            return await self._forward_http(request, route_config)
        else:
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message=f"不支持的协议: {route_config.protocol}"
            )

    async def _forward_http(
        self,
        request: Request,
        route_config: RouteConfig
    ) -> JSONResponse | StreamingResponse:
        """HTTP 请求转发 - 调用 unified_proxy"""
        # 移除 path 前缀，构建下游路径
        path = request.url.path
        if route_config.path and path.startswith(route_config.path):
            upstream_path = path[len(route_config.path):]
            if not upstream_path.startswith("/"):
                upstream_path = "/" + upstream_path
        else:
            upstream_path = path

        # 构建请求头
        headers = {}
        auth_header = request.headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header

        trace_id = getattr(request.state, "trace_id", None)
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        # 获取请求体
        body = await request.body()

        # 调用 unified_proxy 进行转发
        try:
            result = await unified_proxy.forward_http_request(
                service_name=route_config.service_name,
                path=upstream_path,
                method=request.method,
                headers=headers,
                params=dict(request.query_params),
                json_body=None,  # 使用 raw body
                trace_id=trace_id,
                timeout=120.0
            )

            # 处理响应
            if isinstance(result, StreamingResponse):
                return result

            # JSON 响应
            return JSONResponse(
                content=result,
                status_code=200,
                headers={"Content-Type": "application/json"}
            )

        except Exception as exc:
            log.error(
                "HTTP 请求失败 service={} path={} method={} error={}",
                route_config.service_name,
                upstream_path,
                request.method,
                exc
            )
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message=f"服务调用失败: {str(exc)}"
            )

    async def _forward_grpc(
        self,
        request: Request,
        route_config: RouteConfig,
        grpc_method_name: Optional[str] = None
    ) -> JSONResponse:
        """gRPC 请求转发 - 调用 unified_proxy"""
        # 使用 dynamic_route_forward 中解析好的方法名
        if grpc_method_name:
            method_name = grpc_method_name
        else:
            # 兜底：如果 dynamic_route_forward 没有传，自己解析
            path = request.url.path
            if route_config.path and path.startswith(route_config.path):
                upstream_path = path[len(route_config.path):]
                if not upstream_path.startswith("/"):
                    upstream_path = "/" + upstream_path
            else:
                upstream_path = path

            # 解析方法名（假设路径格式为 /{methodName}）
            method_name = upstream_path.strip("/")

        if not method_name:
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message="gRPC 方法名不能为空"
            )

        # 获取请求体
        body = await request.body()
        try:
            import json
            request_data = json.loads(body.decode("utf-8")) if body else {}
        except Exception as exc:
            log.error("gRPC 请求体解析失败: {}", exc)
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message="请求体解析失败"
            )

        # 调用 unified_proxy 进行转发
        try:
            result = await unified_proxy.forward_grpc_request(
                service_name=route_config.service_name,
                method_name=method_name,
                request_data=request_data,
                proto_name=route_config.get_service_base_url().split(":")[0]
                if ":" in route_config.get_service_base_url()
                else route_config.service_name
            )

            return JSONResponse(
                content=result,
                status_code=200
            )
        except Exception as exc:
            log.error(
                "gRPC 请求失败 service={} method={} error={}",
                route_config.service_name,
                method_name,
                exc
            )
            raise BusinessException(
                ErrorCode.SYSTEM_ERROR,
                message=f"服务调用失败: {str(exc)}"
            )

from fastapi import APIRouter, Depends
# 创建动态路由路由器
router = APIRouter()


@router.api_route(path="{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def dynamic_route_forward(
    request: Request,
    full_path: str,
    jwt_user: JWTUser | None = Depends(lambda: None, use_cache=False)  # 改为可选依赖
):
    """
    动态路由转发入口
    根据配置自动将请求转发到对应的服务
    """
    print("=== 路由匹配调试 ===")
    print("full_path:", full_path)
    print("request.url.path:", request.url.path)
    # 获取设置
    settings = get_settings()
    print("app_base_path:", settings.app_base_path)

    # 健康检查路由
    if full_path == "/health" or full_path == "/health/":
        from shared.utils.result_utils import success
        from shared.schema.common import BaseResponse
        return success("ok")

    # 获取路由配置（使用完整路径进行匹配）
    route_config = RouteLoader.get_route(f"{full_path}")
    print("route_config found:", route_config)
    if route_config:
        print("route_config.path:", route_config.path)
        print("route_config.service_name:", route_config.service_name)
        print("route_config.protocol:", route_config.protocol)

    if route_config is None:
        log.warning("未找到路由配置 path={}", full_path)
        raise BusinessException(
            ErrorCode.NOT_FOUND_ERROR,
            message="路由不存在"
        )

    # 解析 gRPC 方法名（用于白名单检查）
    method_name = None
    if route_config.protocol == "grpc":
        # 移除路由前缀，获取方法名
        path = full_path

        # 构建完整的路由路径: /user（注意: full_path 已经去掉了 /api 前缀）
        full_route_path = route_config.path
        print("=== 路径匹配调试 ===")
        print("path:", path)
        print("full_route_path:", full_route_path)
        print("route_config.path:", route_config.path)
        print("path.startswith(full_route_path):", path.startswith(full_route_path))
        # 检查路径是否匹配
        if path.startswith(full_route_path):
            # 移除完整路由路径，获取方法名
            upstream_path = path[len(full_route_path):]
            if upstream_path.startswith("/"):
                upstream_path = upstream_path[1:]
            method_name = upstream_path.strip("/")
        else:
            # 路径不匹配，记录日志
            log.warning("路径不匹配 path={} full_route_path={}", path, full_route_path)
            method_name = None
    print("method_name", method_name)

    # 检查认证要求
    # 1. 如果路由配置 auth_required=false，跳过认证
    # 2. 如果方法在白名单中，跳过认证
    should_check_auth = route_config.auth_required
    if should_check_auth and method_name and method_name in route_config.auth_whitelist:
        should_check_auth = False  # 方法在白名单中，跳过认证

    if should_check_auth and jwt_user is None:
        raise BusinessException(
            ErrorCode.NOT_LOGIN_ERROR,
            message="未登录"
        )

    # 转发请求
    dynamic_router = DynamicRouter()
    return await dynamic_router.forward_request(request, route_config, method_name)


@router.get("/routes")
async def list_routes():
    """列出所有路由配置（管理接口）"""
    routes = RouteLoader.get_all_routes()
    return {
        "routes": [
            {
                "path": r.path,
                "service_name": r.service_name,
                "protocol": r.protocol,
                "auth_required": r.auth_required,
                "auth_whitelist": r.auth_whitelist,
                "description": r.description
            }
            for r in routes
        ]
    }