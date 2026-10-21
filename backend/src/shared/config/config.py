
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

    env:str = "dev"
    app_name: str = "ai-gateway"
    app_env: str = Field(default="local")
    app_host: str = "localhost"
    app_port: int = 8456
    app_base_path: str = "/api"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_password: str = "password"


    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "codegenx"
    mysql_charset: str = "utf8mb4"

    # 用户项目数据库连接
    userdb_mysql_host: str = "127.0.0.1"
    userdb_mysql_port: int = 3306
    userdb_mysql_user: str = "root"
    userdb_mysql_password: str = ""

    # Docker 执行容器（非 dev 环境使用）
    # 容器内预装 mysql 客户端与 python 环境，供 SQL / Python 代码执行使用
    sql_exec_docker_container: str = "codegenx-sql-exec"
    # 宿主机 .data/ 目录在容器内的挂载点（非 dev 环境下 Python/SQL 子进程看到的用户数据根）
    docker_data_root: str = "/data"
    # 容器内 python 可执行文件（默认 PATH 中的 python）
    docker_python_bin: str = "python"


    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 1
    redis_password: str = ""

    qdrant_url:str = "localhost"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""
    # 注意：向量维度不在此配置 —— 以 backend/config.json 的 embedding.dimensions 为唯一来源，
    # 由 memory/vector_store.py 建库时读取，避免双配置不一致。

    cors_allow_origin_patterns: str = "*"
    log_level: str = "INFO"

    # JWT configuration
    jwt_secret: str = "rainbow"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    # RS256 key paths — for K8s Secret file mounting.
    # Set JWT_ALGORITHM=RS256 and point these to the mounted Secret files.
    # jwt_private_key_path is only needed on the service that issues tokens (api-gateway).
    # jwt_public_key_path is needed on all services that verify tokens.
    jwt_private_key_path: str = ""
    jwt_public_key_path: str = ""
    # Fallback: if the key file is not found, try reading the key directly from these env vars.
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    @property
    def mysql_dsn(self) -> str:
        from urllib.parse import quote_plus
        return (
            "mysql+aiomysql://"
            f"{self.mysql_user}:{quote_plus(self.mysql_password)}"
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

settings = get_settings()