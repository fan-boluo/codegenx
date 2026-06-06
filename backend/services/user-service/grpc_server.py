"""gRPC server entrypoint for user-service."""

from __future__ import annotations

import asyncio
import os
from concurrent import futures
from pathlib import Path

import importlib.util
import sys
from pathlib import Path

# 确保 backend 根目录在 Python path 中
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config.log_config import log
from shared.utils.tools import get_local_ip
from shared.utils.proto_loader import load_proto_modules
from infra.nacos.nacos_client import nacos_client
import grpc
from shared.config.config import get_settings

settings = get_settings()


async def nacos_heartbeat_keepalive(service_name: str, ip: str, port: int):
    """
    每 5 秒发送一次心跳 → 保证服务在 Nacos 永不消失
    """
    while True:
        try:
            await nacos_client.heartbeat(
                service_name=service_name,
                ip=ip,
                port=port
            )
            # log.debug(f"✅ Nacos 心跳发送成功: {service_name} {ip}:{port}")
        except Exception as e:
            log.warning(f"⚠️ Nacos 心跳失败: {e}")

        # Nacos 标准心跳间隔：5秒
        await asyncio.sleep(5)

def _import_user_service_servicer() -> object:
    repo_root = Path(__file__).resolve().parents[2]
    servicer_path = repo_root / "services" / "user-service" / "grpc_service" / "user_service_impl.py"
    spec = importlib.util.spec_from_file_location("user_service_impl_module", servicer_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load user service servicer from {servicer_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["user_service_impl_module"] = module
    spec.loader.exec_module(module)
    return getattr(module, "UserServiceServicer")


async def serve() -> None:
    host = get_local_ip() or settings.user_service_host or "localhost"
    port = int(settings.user_service_port) or 5001
    service_name = settings.user_service_name or "user-service"

    # server = grpc_client.aio.server()

    server = grpc.aio.server()  # 异步gRPC
    user_pb2, user_pb2_grpc = load_proto_modules(service_name,settings.user_service_proto_name)
    UserServiceServicer = _import_user_service_servicer()
    user_pb2_grpc.add_UserServiceServicer_to_server(UserServiceServicer(), server)

    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)

    # 注册Nacos（异步调用）
    try:
        # 注册服务
        await nacos_client.register_instance(
            service_name, host, port
        )# metadata={"protocol": "grpc_service"}
        # 发送心跳
        asyncio.create_task(
            nacos_heartbeat_keepalive(service_name, host, port)
        )
        log.info(f"gRPC 服务 {service_name}启动成功：{listen_addr}")

        await server.start()  # 异步启动
        await server.wait_for_termination()

    except KeyboardInterrupt:
        # 注销服务
        await nacos_client.deregister_instance(service_name, host, port)
        log.info(f"{service_name} 服务已停止")


if __name__ == "__main__":
    # 注册nacos,启动服务：user-service
    asyncio.run(serve())
    # print(grpc_service.__version__)