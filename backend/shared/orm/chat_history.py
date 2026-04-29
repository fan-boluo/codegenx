from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class ChatHistory(Base):
    __tablename__ = "chat_history"
    __table_args__ = (
        Index("idx_appId", "appId"),
        Index("idx_userId", "userId"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(String(8192), nullable=False)
    message_type: Mapped[str] = mapped_column("messageType", String(32), nullable=False)
    app_id: Mapped[int] = mapped_column("appId", BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column("userId", BigInteger, nullable=False)
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
