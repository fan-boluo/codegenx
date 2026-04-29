"""SQLAlchemy base."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

"""
DeclarativeBase :
SQLAlchemy 2.0 里所有数据库模型（表）的「父类 / 基类」, User、Order 这类表模型，都必须继承它
统一管理所有表结构
让 ORM 能识别你的类是数据库表
提供 __tablename__、字段映射等核心功能
"""
class Base(DeclarativeBase):
    pass
