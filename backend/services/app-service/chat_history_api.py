from __future__ import annotations

from pathlib import Path
import sys

LOCAL_SERVICES_ROOT = Path(__file__).resolve().parent / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infra.mysql.session import get_db_session
from shared.constants import UserRole
from shared.schema.chat_history import ChatHistoryQueryRequest, ChatHistoryVO
from shared.schema.common import BaseResponse
from shared.utils.result_utils import success

from core.auth_proxy import JWTUser, require_login, require_role
from chat_history import ChatHistoryService


router = APIRouter(prefix="/api/chatHistory", tags=["chatHistory"])


@router.get("/app/{app_id}", response_model=BaseResponse[list[ChatHistoryVO]])
async def list_app_chat_history(
    app_id: int,
    session_id: str = "",
    current_user: JWTUser = Depends(require_login),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list[ChatHistoryVO]]:
    service = ChatHistoryService(db)
    result = await service.list_app_chat_history(app_id, session_id, current_user)
    return success([ChatHistoryVO.model_validate(item) for item in result])


@router.post("/admin/list/page/vo", response_model=BaseResponse[list[ChatHistoryVO]])
async def list_all_chat_history_by_page_for_admin(
    chat_history_query_request: ChatHistoryQueryRequest,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[list[ChatHistoryVO]]:
    service = ChatHistoryService(db)
    result = await service.list_app_chat_history_by_page_with_query(chat_history_query_request)
    return success([ChatHistoryVO.model_validate(item) for item in result])


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


@router.post("/admin/cleanup", response_model=BaseResponse[int])
async def cleanup_expired_history(
    retention_days: int = 3,
    current_user: JWTUser = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db_session),
) -> BaseResponse[int]:
    service = ChatHistoryService(db)
    deleted = service.cleanup_expired_files(retention_days)
    return success(deleted)