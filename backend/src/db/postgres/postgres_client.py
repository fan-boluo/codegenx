
"""
postgres客户端连接池
"""
from shared.config.config import get_settings

settings = get_settings()

class PostgresClient:


    async def shutdown(self):
        pass



async def shutdown_postgres_engine() -> None:
    pass