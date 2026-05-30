from __future__ import annotations

from pathlib import Path
import sys

LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infra.mysql.session import get_db_session
from shared.schema.common import BaseResponse
from shared.utils.result_utils import success

from chat_history import ChatHistoryService


router = APIRouter(prefix="/api/chatHistory", tags=["chatHistory"])


@router.delete("/internal/app/{app_id}", response_model=BaseResponse[bool])
async def delete_chat_history_by_app_id(
    app_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[bool]:
    try:
        service = ChatHistoryService(db)
        return success(await service.delete_by_app_id(app_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
