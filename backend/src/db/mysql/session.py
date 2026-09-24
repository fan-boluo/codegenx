"""Database session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config.config import get_settings

settings = get_settings()


"""
expire_on_commit=False
如果是 True（默认值）：异步情况下，commit提交事务后，内存中的数据对象会过期，此时sqlalchemy懒加载想再查数据，但
已经提交，连接已经关闭，就会报错

expire_on_commit=False 表示不设为过期，内存数据依然可以查，避免懒加载问题
所以 FastAPI + SQLAlchemy 异步 = 必须写 expire_on_commit=False
"""

# 异步数据库引擎
engine = create_async_engine(
    settings.mysql_dsn,
    pool_pre_ping=True,  # 连接前先ping
    pool_recycle=1800,  # 连接每30分钟回收一次，防止连接超时断开
)
session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# fastAPI的依赖注入函数，每次请求获取一个数据库连接，请求结束自动断开
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session


async def warm_up_mysql_pool() -> None:
    """Open one real connection so the async engine pool is ready before traffic arrives."""
    async with session_maker() as session:
        await session.execute(text("SELECT 1"))


async def shutdown_mysql_engine() -> None:
    await engine.dispose()
