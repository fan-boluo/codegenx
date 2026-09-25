"""ai schema 回归：user_id 不得再有 "userx" 兜底（身份一律由路由层取 JWT 登录态）。

运行：backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ai_schema.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest


def test_generate_request_user_id_defaults_empty():
    """请求体不带 userId → 空串（由路由层覆写为登录用户），绝不落 "userx"。"""
    req = AiServiceGenerateRequest(appId="app_1", message="hi")
    assert req.user_id == ""


def test_generate_request_user_id_camel_alias():
    req = AiServiceGenerateRequest(**{"appId": "app_1", "userId": "user_abc", "message": "hi"})
    assert req.user_id == "user_abc"
