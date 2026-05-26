from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_SERVICE_ROOT = BACKEND_ROOT / "services" / "app-service"
APP_SERVICE_SERVICES_ROOT = APP_SERVICE_ROOT / "services"
for candidate in (str(APP_SERVICE_SERVICES_ROOT), str(APP_SERVICE_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import chat_history
import chat_history_api
import core.auth_proxy as auth_proxy
from chat_history import ChatHistoryService
from shared.constants import CHAT_HISTORY_FILE_NAME, UserRole
from shared.enums.chat_history_message_type import ChatHistoryMessageTypeEnum


class FakeSession:
    async def get(self, model, app_id: int):
        return SimpleNamespace(id=app_id, user_id=1001)


async def override_get_db_session():
    yield FakeSession()


async def fake_user_login(request):
    return SimpleNamespace(user_id=1001, user_role=UserRole.USER.value)


async def fake_admin_login(request):
    return SimpleNamespace(user_id=1, user_role=UserRole.ADMIN.value)


class ChatHistoryFileApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name) / "workspace"
        self.original_session_dir_getter = chat_history.get_session_dir
        chat_history.get_session_dir = lambda app_id: self.runtime_root / str(app_id) / "session"
        self.service = ChatHistoryService(FakeSession())

        app = FastAPI()
        app.include_router(chat_history_api.router)
        app.dependency_overrides[chat_history_api.get_db_session] = override_get_db_session
        self.client = TestClient(app)
        self.auth_module = auth_proxy.module

    def tearDown(self) -> None:
        self.client.close()
        chat_history.get_session_dir = self.original_session_dir_getter
        self.temp_dir.cleanup()

    def test_user_and_admin_endpoints_read_jsonl_history(self) -> None:
        asyncio.run(self.service.add_chat_message(101, "用户问题", ChatHistoryMessageTypeEnum.USER.value, 1001))
        asyncio.run(self.service.add_chat_message(101, "AI 回答", ChatHistoryMessageTypeEnum.AI.value, 1001))

        history_file = self.runtime_root / "101" / "session" / CHAT_HISTORY_FILE_NAME
        self.assertTrue(history_file.exists())
        self.assertEqual(len(history_file.read_text(encoding="utf-8").splitlines()), 2)

        with patch.object(self.auth_module, "get_login_user", new=fake_user_login):
            response = self.client.get("/api/chatHistory/app/101", params={"page_size": 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(len(payload["data"]), 2)
        self.assertEqual(payload["data"][0]["message"], "AI 回答")
        self.assertEqual(payload["data"][0]["messageType"], "ai")

        with patch.object(self.auth_module, "get_login_user", new=fake_admin_login):
            admin_response = self.client.post(
                "/api/chatHistory/admin/list/page/vo",
                json={"pageNum": 1, "pageSize": 10, "message": "AI"},
            )

        self.assertEqual(admin_response.status_code, 200)
        admin_payload = admin_response.json()
        self.assertEqual(admin_payload["code"], 0)
        self.assertEqual(len(admin_payload["data"]), 1)
        self.assertEqual(admin_payload["data"][0]["appId"], 101)
        self.assertEqual(admin_payload["data"][0]["message"], "AI 回答")

    def test_delete_endpoint_removes_history_file(self) -> None:
        asyncio.run(self.service.add_chat_message(202, "待删除", ChatHistoryMessageTypeEnum.USER.value, 1001))
        history_file = self.runtime_root / "202" / "session" / CHAT_HISTORY_FILE_NAME
        self.assertTrue(history_file.exists())

        response = self.client.delete("/api/chatHistory/internal/app/202")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 0)
        self.assertFalse(history_file.exists())


if __name__ == "__main__":
    unittest.main()