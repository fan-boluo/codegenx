
"""Application settings."""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

"""
pydantic_settings
加载环境变量后，自动映射环境变量名和字段名，不区分大小写

@lru_cache：装饰器，单例模式，只创建一次Setting
"""

class Settings(BaseSettings):
    # ignore：忽略多余环境变量，不报错
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_name: str = "ai-gateway"
    app_env: str = Field(default="local")
    app_host: str = "localhost"
    app_port: int = 8456
    app_base_path: str = "/api"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "codegenx"
    mysql_charset: str = "utf8mb4"

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 1
    redis_password: str = ""

    nacos_server_addr:str = "127.0.0.1:8848"
    nacos_namespace:str = "public"
    nacos_schema:str= "http"
    nacos_heartbeat_interval_seconds: int = 5

    cors_allow_origin_patterns: str = "*"
    log_level: str = "INFO"
    # 会从.env里面直接读取覆盖吗
    ai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode"
    ai_api_key: str = ""
    ai_model: str = "qwen-plus"
    ai_chat_completions_path: str = "/v1/chat/completions"
    ai_timeout_seconds: int = 120

    user_service_host: str = "localhost"
    user_service_port: str = "50051"
    user_service_name: str = "user-service"
    user_service_proto_name: str = "user_service"

    app_service_host: str = "localhost"
    app_service_port: str = "50052"
    app_service_http_port: str = "8004"
    app_service_name: str = "app-service"
    app_service_proto_name: str = "app_service"
    app_service_register_host: str = ""
    app_service_register_port: str = "8004"
    app_service_discovery_cache_ttl_seconds: int = 3

    chat_service_host: str = "localhost"
    chat_service_http_port: str = "8005"
    chat_service_name: str = "chat-service"
    chat_service_register_host: str = ""
    chat_service_register_port: str = "8005"
    
    ai_service_host: str = "localhost"
    ai_service_port: str = "50053"
    ai_service_http_port: str = "8002"
    ai_service_name: str = "ai-service"
    ai_service_proto_name: str = "ai-service"

    gateway_grpc_timeout_seconds: int = 8
    gateway_grpc_max_attempts: int = 2

    jwt_secret:str = "rainbow"
    @property
    def mysql_dsn(self) -> str:
        return (
            "mysql+aiomysql://"
            f"{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"
            f"?charset={self.mysql_charset}"
        )

    @property
    def redis_dsn(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
