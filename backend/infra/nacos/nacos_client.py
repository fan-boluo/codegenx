import httpx
from shared.config.config import get_settings
from shared.config.log_config import log

settings = get_settings()
class NacosClient:
    def __init__(self):
        self.server_addr = settings.nacos_server_addr
        self.namespace_id = settings.nacos_namespace
        self.schema = settings.nacos_schema

    @property
    def base_url(self):
        return f"{self.schema}://{self.server_addr}"

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
        async with httpx.AsyncClient() as client:
            r = await client.post(url, params=params)
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
        async with httpx.AsyncClient() as client:
            r = await client.delete(url, params=params)
            r.raise_for_status()
        log.info(f"Deregistered instance: {service_name}")

    async def _get_instances(self, service_name: str):
        url = f"{self.base_url}/nacos/v1/ns/instance/list"
        params = {"serviceName": service_name, "namespaceId": self.namespace_id}
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params)
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
            "beat": '{"ip":"%s","port":%s,"serviceName":"%s"}' % (ip, port, service_name)
        }
        async with httpx.AsyncClient() as client:
            await client.put(url, params=params)

nacos_client = NacosClient()
# GROUP = "DEFAULT_GROUP"
# def keep_heartbeat(service_name, ip, port, interval=5):
#     """持续心跳（必须）"""
#     while True:
#         try:
#             client.send_heartbeat(
#                 service_name=service_name,
#                 ip=ip,
#                 port=port,
#                 group_name=GROUP
#             )
#         except:
#             pass
#         time.sleep(interval)

# def start_heartbeat(service_name, ip, port):
#     t = threading.Thread(target=keep_heartbeat, args=(service_name, ip, port), daemon=True)
#     t.start()