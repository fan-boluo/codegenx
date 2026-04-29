from fastapi import APIRouter, Header, Depends
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core.constants import ErrorCode, CHAT_API_KEY_RATE_LIMIT_PER_SECOND
from app.db.session import get_db_session
from app.exceptions.business_exception import BusinessException
from app.infra.redis_client import get_redis_client
from app.schemas.chat import ChatResponse, ChatRequest
from app.services.api_key_service import ApiKeyService
from app.services.chat_service import ChatService
from app.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/v1/chat", tags=["chat"])

"""
流式响应：
StreamingResponse + AsyncGenerator
1）StreamingResponse 会自动设置 Content-Type: text/event-stream 响应头
2）AsyncGenerator 每次 yield 的数据会立即推送给客户端
3）当客户端断开连接时，FastAPI 会自动停止消费生成器，触发生成器的清理逻辑
"""

@router.post("/completions", response_model=ChatResponse | None)
async def chat_completions(
    payload: ChatRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis_client),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "缺少或无效的 Authorization Header")
    api_key_value = authorization[7:]
    api_key = await ApiKeyService(db).get_by_key_value(api_key_value)
    if api_key is None:
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "API Key 无效或已失效")
    # 拿到apikey之后立刻做限流
    allowed = await RateLimitService(redis).check_api_key_rate_limit(
        api_key_value, CHAT_API_KEY_RATE_LIMIT_PER_SECOND
    )
    if not allowed:
        raise BusinessException(ErrorCode.TOO_MANY_REQUEST, "请求过于频繁，请稍后再试")
        return None
    if not payload.messages:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "messages 不能为空")
    if not payload.model:
        payload.model = "qwen-plus"
    service = ChatService(db)
    if payload.stream:
        return StreamingResponse(
            service.chat_stream(payload, api_key.user_id, api_key.id),
            media_type="text/event-stream",
        )
    return await service.chat(payload, api_key.user_id, api_key.id)
