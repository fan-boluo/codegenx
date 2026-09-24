"""AI 服务契约测试（单体化后）。

原文件针对旧版 run_turn 架构与已删除的 ai-service/app.py，相应用例随架构演进移除；
现验证当前代码：
- /api/ai/chat/gen|stop 路由：认证依赖、参数校验、AgentService 委托与 JSON-lines 流式响应
- AgentAdapterService：stream_message 委托 runtime.submit_request、_get_runtime 惰性单例
- 默认配置可实例化、子代理工具过滤
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codegenx.ai_service import router as ai_router
from codegenx.gateway.middleware.auth import require_login
from codegenx.gateway.middleware.jwt_auth import JWTUser
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode
from shared.schema.ai_service import AiServiceGenerateRequest
from shared.utils.result_utils import error


def _build_app(*, with_auth_override: bool = True) -> FastAPI:
    """最小应用：仅挂 chat_router + BusinessException 处理器（与 main.py 同契约）。"""
    app = FastAPI()
    app.include_router(ai_router.chat_router, prefix="/api")

    @app.exception_handler(BusinessException)
    async def business_exception_handler(request: Request, exc: BusinessException):
        return JSONResponse(
            status_code=200,
            content=error(exc.code, exc.message).model_dump(by_alias=True),
        )

    if with_auth_override:
        app.dependency_overrides[require_login] = lambda: JWTUser(
            user_id=1, user_account="tester", user_role="user"
        )
    return app


class FakeAgentService:
    def __init__(self) -> None:
        self.requests: list[AiServiceGenerateRequest] = []
        self.stop_calls: list[dict[str, object]] = []

    async def stream_message(self, request: AiServiceGenerateRequest):
        self.requests.append(request)
        yield '{"eventType": "LLM_Response_Chunk", "data": "alpha"}\n'
        yield '{"eventType": "TurnCompleted"}\n'

    async def stop_session(self, **kwargs):
        self.stop_calls.append(kwargs)
        return {
            "accepted": True,
            "sessionId": kwargs["session_id"],
            "stoppedRequestCount": 1,
            "droppedRequestCount": 0,
            "activeRequestIds": [],
            "droppedRequestIds": [],
            "activeTurnIds": [],
        }


class AiServiceAgentStreamContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_agent = FakeAgentService()
        self.original_agent = ai_router.agent_service
        ai_router.agent_service = self.fake_agent
        self.client = TestClient(_build_app())

    def tearDown(self) -> None:
        self.client.close()
        ai_router.agent_service = self.original_agent

    def test_gen_streams_agent_json_lines(self) -> None:
        response = self.client.post(
            "/api/ai/chat/gen",
            json={
                "appId": 101,
                "userId": "u-101",
                "message": "生成一个官网首页",
                "traceId": "trace-101",
                "requestId": "req-101",
                "sessionId": "session-101",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertEqual(
            response.text,
            '{"eventType": "LLM_Response_Chunk", "data": "alpha"}\n'
            '{"eventType": "TurnCompleted"}\n',
        )
        self.assertEqual(len(self.fake_agent.requests), 1)
        sent = self.fake_agent.requests[0]
        self.assertEqual(sent.app_id, 101)
        self.assertEqual(sent.user_id, "u-101")
        self.assertEqual(sent.message, "生成一个官网首页")
        self.assertEqual(sent.trace_id, "trace-101")
        self.assertEqual(sent.request_id, "req-101")
        self.assertEqual(sent.session_id, "session-101")

    def test_gen_rejects_missing_trace_context(self) -> None:
        response = self.client.post(
            "/api/ai/chat/gen",
            json={
                "appId": 202,
                "message": "缺少链路上下文",
                "requestId": "req-202",
                "sessionId": "session-202",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], ErrorCode.PARAMS_ERROR.get_code())
        self.assertIn("traceId", payload["message"])
        self.assertEqual(self.fake_agent.requests, [])

    def test_gen_requires_login_without_token(self) -> None:
        client = TestClient(_build_app(with_auth_override=False))
        try:
            response = client.post(
                "/api/ai/chat/gen",
                json={
                    "appId": 203,
                    "message": "未登录探针",
                    "traceId": "trace-203",
                    "requestId": "req-203",
                    "sessionId": "session-203",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], ErrorCode.NOT_LOGIN_ERROR.get_code())
        self.assertEqual(self.fake_agent.requests, [])

    def test_stop_delegates_to_agent_service(self) -> None:
        response = self.client.post(
            "/api/ai/chat/stop",
            json={
                "appId": 301,
                "userId": "u-301",
                "traceId": "trace-301",
                "requestId": "req-301",
                "sessionId": "session-301",
                "reason": "user-cancel",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertTrue(payload["data"]["accepted"])
        self.assertEqual(payload["data"]["sessionId"], "session-301")
        self.assertEqual(payload["data"]["stoppedRequestCount"], 1)

        self.assertEqual(len(self.fake_agent.stop_calls), 1)
        stop_call = self.fake_agent.stop_calls[0]
        self.assertEqual(stop_call["session_id"], "session-301")
        self.assertEqual(stop_call["app_id"], 301)
        self.assertEqual(stop_call["reason"], "user-cancel")

    def test_removed_legacy_routes_return_404(self) -> None:
        """旧代码生成图路由已删除，未知 /api/* 路由应返回真实 404（无动态兜底）。"""
        for path in ("/api/ai/codegen/stream", "/api/ai/codegen/route"):
            response = self.client.post(path, json={"initPrompt": "生成官网"})
            self.assertEqual(response.status_code, 404, path)


class AgentAdapterServiceTest(unittest.TestCase):
    def test_stream_message_delegates_to_runtime_and_yields_json_lines(self) -> None:
        from codegenx.ai_service.services.agent_adapter_service import AgentAdapterService

        service = AgentAdapterService()

        async def fake_submit(request):
            yield SimpleNamespace(model_dump_json=lambda: '{"data": "alpha"}')
            yield SimpleNamespace(model_dump_json=lambda: '{"done": true}')

        service._runtime = SimpleNamespace(submit_request=fake_submit)
        request = AiServiceGenerateRequest(
            appId=401,
            message="hi",
            traceId="trace-401",
            requestId="req-401",
            sessionId="session-401",
        )

        async def collect() -> list[str]:
            return [chunk async for chunk in service.stream_message(request)]

        chunks = asyncio.run(collect())

        self.assertEqual(chunks, ['{"data": "alpha"}\n', '{"done": true}\n'])

    def test_get_runtime_is_lazy_singleton(self) -> None:
        from codegenx.ai_service.services.agent_adapter_service import AgentAdapterService

        service = AgentAdapterService()
        fake_runtime = MagicMock()

        with patch(
            "codegenx.ai_service.services.agent_adapter_service.AgentRuntime",
            return_value=fake_runtime,
        ) as runtime_ctor:
            first = service._get_runtime()
            second = service._get_runtime()

        self.assertIs(first, fake_runtime)
        self.assertIs(second, fake_runtime)
        runtime_ctor.assert_called_once()


class AgentConfigGuardTest(unittest.TestCase):
    def test_default_config_can_be_instantiated(self) -> None:
        from codegenx.ai_service.bot.utils.config import Config

        config = Config()

        self.assertEqual(config.agents, [])
        self.assertTrue(config.memory.search.enabled)
        self.assertTrue(config.memory.store.enabled)


class SubagentToolTest(unittest.TestCase):
    def test_subagent_context_filters_child_tools(self) -> None:
        from codegenx.ai_service.bot.agent.subagent_runner import SubagentContext
        from codegenx.ai_service.bot.agent.tool_handler import ToolRegistry

        registry = ToolRegistry()
        all_names = {tool.name for tool in registry.tools}
        self.assertIn("read_file", all_names)

        unrestricted = SubagentContext(prompt="inspect")
        unrestricted_names = {tool.name for tool in unrestricted.get_tools(registry)}
        # 子代理内禁止再派生子代理 / 触发压缩
        self.assertNotIn("subagent", unrestricted_names)
        self.assertNotIn("compact", unrestricted_names)
        self.assertIn("read_file", unrestricted_names)

        restricted = SubagentContext(prompt="inspect", allowed_tools=["read_file"])
        restricted_names = [tool.name for tool in restricted.get_tools(registry)]
        self.assertEqual(restricted_names, ["read_file"])


if __name__ == "__main__":
    unittest.main()
