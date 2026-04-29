"""App model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text, Integer
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class App(Base):
    __tablename__ = "app"
    __table_args__ = (
        Index("idx_appName", "appName"),
        Index("idx_userId", "userId"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_name: Mapped[str] = mapped_column("appName", String(128), nullable=False)
    app_desc: Mapped[str | None] = mapped_column("appDesc", String(2048), nullable=True)
    app_icon: Mapped[str | None] = mapped_column("appIcon", String(1024), nullable=True)
    app_type: Mapped[int] = mapped_column("appType", Integer, nullable=False, default=0)
    scoring_strategy: Mapped[int] = mapped_column("scoringStrategy", Integer, nullable=False, default=0)
    review_status: Mapped[int] = mapped_column("reviewStatus", Integer, nullable=False, default=0)
    review_message: Mapped[str | None] = mapped_column("reviewMessage", String(512), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column("reviewerId", BigInteger, nullable=True)
    review_time: Mapped[datetime | None] = mapped_column("reviewTime", DateTime, nullable=True)
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