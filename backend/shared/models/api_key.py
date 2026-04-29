from datetime import datetime

from sqlalchemy import Index, BigInteger, String, Integer, Text, func, DateTime, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base

# 每个用户的api_key的消耗
class ApiKey(Base):
    __tablename__ = "api_key"
    __table_args__ = (
        Index("uk_keyValue", "keyValue", unique=True),
        Index("idx_userId", "userId"),
        Index("idx_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column("userId", BigInteger, nullable=False)
    key_value: Mapped[str] = mapped_column("keyValue", String(128), nullable=False)
    key_name: Mapped[str | None] = mapped_column("keyName", String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'"),
    )
    total_tokens: Mapped[int] = mapped_column(
        "totalTokens", BigInteger, nullable=False, default=0, server_default=text("0"),
    )
    last_used_time: Mapped[datetime | None] = mapped_column("lastUsedTime", DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, nullable=False,
        default=datetime.utcnow, server_default=func.current_timestamp(),
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime", DateTime, nullable=False,
        default=datetime.utcnow, onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    is_delete: Mapped[int] = mapped_column(
        "isDelete", TINYINT, nullable=False, default=0, server_default=text("0"),
    )
