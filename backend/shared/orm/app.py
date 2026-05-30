from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func, text
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
    db_name: Mapped[str | None] = mapped_column("dbName", String(128), nullable=True)
    cover: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    init_prompt: Mapped[str] = mapped_column("initPrompt", String(4096), nullable=False)
    code_gen_type: Mapped[str] = mapped_column("codeGenType", String(64), nullable=False)
    deploy_key: Mapped[str | None] = mapped_column("deployKey", String(128), nullable=True)
    deployed_time: Mapped[datetime | None] = mapped_column("deployedTime", DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    user_id: Mapped[int] = mapped_column("userId", BigInteger, nullable=False)
    edit_time: Mapped[datetime | None] = mapped_column("editTime", DateTime, nullable=True)
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
