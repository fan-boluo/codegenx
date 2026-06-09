import asyncio
from typing import Any

import httpx
from shared.config.config import get_settings
from shared.config.log_config import log
import time

settings = get_settings()
class NacosClient:
    def __init__(self):
        self.server_addr = settings.nacos_server_addr
        self.namespace_id = settings.nacos_namespace
        self.schema = settings.nacos_schema
        self.user = settings.nacos_user
        self.password = settings.nacos_password
        self._token = None
        self._token_expiry_time = None

    @property
    def base_url(self):
        return f"{self.schema}://{self.server_addr}"

    async def _login(self) -> Any | None:
        """登录获取 JWT Token"""
        url = f"{self.base_url}/nacos/v1/auth/users/login"
        params = {"username": self.user, "password": self.password}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, data=params)
            r.raise_for_status()
            data = r.json()
            print(data)
            self._token = data.get("accessToken")
            token_ttl = data.get("tokenTtl", 18000)  # 默认5小时

            # 记录【过期时间戳】= 当前时间 + 有效期 - 30秒（提前30秒刷新）
            self._token_expiry_time = time.time() + token_ttl - 30
            log.info(f"Nacos 登录成功，Token 有效期至: {self._token_expiry_time}")
            return self._token

    async def _ensure_token(self):
        """确保 Token 存在且未过期"""
        if not self._token or time.time() >= self._token_expiry_time:
            await self._login()
        # 简单检查：如果 Token 过期，重新登录
        # 注意：这里可以添加更完善的过期检查逻辑

    async def _get_headers(self):
        """获取请求头，包含 Bearer Token"""
        await self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    async def register_instance(self, service_name: str, ip: str, port: int):
        url = f"{self.base_url}/nacos/v1/ns/instance"
        params = {
            "serviceName": service_name,
            "ip": ip,
            "port": str(port),
            "namespaceId": self.namespace_id,
            "healthy": "true",
            "enable": "true",
            "weight": "1.0",
        }
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, params=params,headers=headers)
            r.raise_for_status()
            print(f"✅ Nacos 注册响应: {r.text}")
        log.info(f"Registered instance: {service_name},ip:{ip},port:{port}")

    # 服务停止后自动从nacos注销
    async def deregister_instance(self, service_name: str, ip: str, port: int):
        url = f"{self.base_url}/nacos/v1/ns/instance"
        params = {
            "serviceName": service_name,
            "ip": ip,
            "port": str(port),
            "namespaceId": self.namespace_id,
        }
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, params=params, headers=headers)
            r.raise_for_status()
        log.info(f"Deregistered instance: {service_name}")

    async def _get_instances(self, service_name: str):
        url = f"{self.base_url}/nacos/v1/ns/instance/list"
        params = {"serviceName": service_name, "namespaceId": self.namespace_id}
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data.get("hosts", [])

    async def choose_instance(self, service_name: str):
        instances = await self._get_instances(service_name)
        for item in instances:
            if item.get("healthy"):
                return item
        return instances[0] if instances else None


    async def get_service_addr(self,service_name:str) -> str:
        " 根据服务名获取ip:port "
        instance = await self.choose_instance(service_name)
        if instance is None:
            raise RuntimeError(f"No {service_name} instance available")
        ip = instance.get("ip")
        port = instance.get("port")
        if not ip or not port:
            raise RuntimeError(f"Invalid {service_name} instance from Nacos")
        return f"{ip}:{port}"

    async def get_service_base_url(self, service_name: str, *, scheme: str = "http") -> str:
        addr = await self.get_service_addr(service_name)
        return f"{scheme}://{addr}"

    async def heartbeat(self, service_name: str, ip: str, port: int):
        """
        发送 Nacos 心跳（服务健康保活）
        """
        url = f"{self.base_url}/nacos/v1/ns/instance/beat"
        params = {
            "serviceName": service_name,
            "ip": ip,
            "port": port,
            "namespaceId": self.namespace_id,
            "beat": '{"cluster":"DEFAULT","ip":"%s","port":%s,"serviceName":"%s","weight":1.0,"metadata":{},"scheduled":true}' % (ip, port, service_name)
        }
        headers = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.put(url, params=params, headers=headers)
            r.raise_for_status()

nacos_client = NacosClient()


if __name__ == '__main__':
    asyncio.run(nacos_client.register_instance("test","localhost",8080))