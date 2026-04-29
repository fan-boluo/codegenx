from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = BACKEND_ROOT / "api-gateway"
for candidate in (str(GATEWAY_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from shared.schema.ai_service import AiServiceErrorPayload, AiServiceGenerateRequest, AiServiceStreamMeta
from shared.schema.common import BaseResponse


class AppAiHttpContractTest(unittest.TestCase):
    def test_generate_request_alias_contract(self) -> None:
        request = AiServiceGenerateRequest(
            appId=123,
            message="继续完善页脚",
            codeGenType="vue_project",
            traceId="trace-2",
            requestId="req-2",
            sessionId="session-2",
        )
        dumped = request.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(dumped["appId"], 123)
        self.assertEqual(dumped["codeGenType"], "vue_project")
        self.assertEqual(dumped["traceId"], "trace-2")
        self.assertEqual(dumped["requestId"], "req-2")
        self.assertEqual(dumped["sessionId"], "session-2")

    def test_generate_request_omits_codegen_type_when_not_provided(self) -> None:
        request = AiServiceGenerateRequest(
            appId=456,
            message="直接让 agent 决定",
            traceId="trace-4",
            requestId="req-4",
            sessionId="session-4",
        )
        dumped = request.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(
            dumped,
            {
                "appId": 456,
                "message": "直接让 agent 决定",
                "traceId": "trace-4",
                "requestId": "req-4",
                "sessionId": "session-4",
            },
        )

    def test_stream_meta_and_error_payload_keep_observability_fields(self) -> None:
        response = AiServiceStreamMeta(
            traceId="trace-3",
            requestId="req-3",
            upstreamInstance="ai-service:8002",
            timeoutMs=120000,
            idempotencyMode="best-effort",
        )
        error_payload = AiServiceErrorPayload(
            code=50000,
            message="upstream failed",
            traceId="trace-3",
            requestId="req-3",
            upstreamInstance="ai-service:8002",
            retryable=True,
        )
        envelope = BaseResponse[AiServiceStreamMeta](code=0, message="ok", data=response)
        self.assertEqual(envelope.model_dump(by_alias=True)["data"]["upstreamInstance"], "ai-service:8002")
        self.assertTrue(error_payload.model_dump(by_alias=True)["retryable"])


if __name__ == "__main__":
    unittest.main()