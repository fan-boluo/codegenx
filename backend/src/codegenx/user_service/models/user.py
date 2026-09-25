"""User model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        Index("uk_userAccount", "userAccount", unique=True),
        Index("idx_userName", "userName"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # user_xxxx，插入前由服务层生成
    user_account: Mapped[str] = mapped_column("userAccount", String(256), nullable=False)
    user_password: Mapped[str] = mapped_column("userPassword", String(512), nullable=False)
    user_name: Mapped[str | None] = mapped_column("userName", String(256), nullable=True)
    user_profile: Mapped[str | None] = mapped_column("userProfile", String(512), nullable=True)
    user_role: Mapped[str] = mapped_column(
        "userRole",
        String(256),
        nullable=False,
        default="user",
        server_default=text("'user'"),
    )
    edit_time: Mapped[datetime] = mapped_column(
        "editTime",
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
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