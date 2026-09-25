from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class App(Base):
    """项目表（精简版）：属主即创建人（owner），一个项目绑定一个项目库（dbName）。"""
    __tablename__ = "app"
    __table_args__ = (
        Index("idx_appName", "appName"),
        Index("idx_owner", "owner"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # app_xxxx，插入前由服务层生成
    app_name: Mapped[str] = mapped_column("appName", String(128), nullable=False)
    db_name: Mapped[str | None] = mapped_column("dbName", String(128), nullable=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False)  # 项目属主即创建人（user_xxxx）
    create_time: Mapped[datetime] = mapped_column(
        "createTime",
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime",
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    is_delete: Mapped[int] = mapped_column(
        "isDelete",
        TINYINT,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
