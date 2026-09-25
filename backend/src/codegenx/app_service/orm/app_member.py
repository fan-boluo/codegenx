from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class AppMember(Base):
    """项目成员表（用户-项目多对多）。

    项目属主由 app.owner 表达，本表只存普通成员，不设 role 列；
    移除成员走逻辑删除（isDelete=1），重新加入恢复原记录。
    """
    __tablename__ = "app_member"
    __table_args__ = (
        Index("uk_app_user", "appId", "userId", unique=True),
        Index("idx_userId", "userId"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column("appId", String(32), nullable=False)  # app_xxxx
    user_id: Mapped[str] = mapped_column("userId", String(32), nullable=False)  # user_xxxx
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
