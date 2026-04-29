import json
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from shared.config.log_config import log
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ERROR_MESSAGE_MAP, ErrorCode
from shared.utils import result_utils

# 全局统一异常处理
async def global_exception_handler(request: Request, exc: Exception):
    # 1. 业务异常
    if isinstance(exc, BusinessException):
        log.error("BusinessException", exc_info=exc)
        code = exc.code
        msg = exc.message

        if await handle_sse_error(request, code, msg):
            return empty_sse_response()
        return result_utils.error(code, msg)

    # 2. 系统异常
    else:
        log.error("RuntimeException", exc_info=exc)
        code = ErrorCode.SYSTEM_ERROR
        msg = ERROR_MESSAGE_MAP.get(code)

        if await handle_sse_error(request, code, msg):
            return empty_sse_response()
        return result_utils.error(code, msg)


# SSE 错误处理
async def handle_sse_error(request: Request, code: int, msg: str) -> bool:
    try:
        accept = request.headers.get("Accept", "")
        uri = request.url.path

        # 判断是不是 SSE 请求
        is_sse = "text/event-stream" in accept or "/chat/gen/code" in uri
        if not is_sse:
            return False

        # 直接构造 SSE 错误流返回
        async def sse_error_generator():
            error = json.dumps({"error": True, "code": code, "message": msg}, ensure_ascii=False)
            yield f"event: business-error\ndata: {error}\n\n"
            yield f"event: done\ndata: {{}}\n\n"

        # 把 SSE 错误直接作为响应返回（全局生效）
        request.state.sse_response = StreamingResponse(
            sse_error_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        return True

    except Exception as e:
        # 即使写入失败，也表示这是SSE请求
        log.error("SSE 错误处理失败", exc_info=e)
        return True

def empty_sse_response():
    return StreamingResponse(iter([]), media_type="text/event-stream")