"""Project schema manager — PostgreSQL：一个项目一个 schema，统一落在 analysis 库。

历史实现为 MySQL CREATE DATABASE，现改为 PG：
- 所有用户项目共用同一个 PG 数据库 analysis
- 每个项目一个 schema（prj_ 前缀 + 用户命名）
- 公共数据集由 config.json databases 段登记，不在本模块管理
"""

from __future__ import annotations

import asyncpg

from shared.config.config import get_settings
from shared.constants import ANALYSIS_DB

settings = get_settings()


def _dsn(database: str) -> str:
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{database}"
    )


async def create_project_database(db_name: str) -> None:
    """在 analysis 库下创建项目 schema（prj_ 前缀 + 用户命名）。"""
    conn = await asyncpg.connect(_dsn(ANALYSIS_DB))
    try:
        # schema 名含特殊字符需用双引号包裹；IF NOT EXISTS 保证幂等
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{db_name}"')
    finally:
        await conn.close()


async def list_database_tables(db_name: str) -> list[dict]:
    """列出指定 schema 下的所有表名。"""
    conn = await asyncpg.connect(_dsn(ANALYSIS_DB))
    try:
        rows = await conn.fetch(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            db_name,
        )
        return [{"name": r["name"], "type": "table"} for r in rows]
    finally:
        await conn.close()
