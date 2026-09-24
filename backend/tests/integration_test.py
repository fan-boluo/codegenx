"""
Integration test for POST /api/ai/chat/gen and POST /api/ai/chat/stop (monolith, port 8456).
需要先启动服务：uv run python -m codegenx
且需要 .env 中配置可用的 MySQL/Redis 与已存在用户（默认 admin/123456，可用环境变量覆盖）。
Run from backend directory:
  uv run python src/codegenx/ai_service/test/integration_test.py
"""

import asyncio
import json
import os
import threading
import time
import httpx

BASE = "http://localhost:8456"
LOGIN_URL = f"{BASE}/api/user/login"
STREAM_URL = f"{BASE}/api/ai/chat/gen"
STOP_URL = f"{BASE}/api/ai/chat/stop"

LOGIN_BODY = {
    "userAccount": os.environ.get("TEST_USER_ACCOUNT", "admin"),
    "userPassword": os.environ.get("TEST_USER_PASSWORD", "123456"),
}

STREAM_BODY = {
    "traceId": "int-test-trace-01",
    "requestId": "int-test-req-01",
    "sessionId": "int-test-session-01",
    "appId": 1,
    "userId": "1",
    "message": "帮我写一个Python hello world程序",
}

STOP_BODY = {
    "traceId": "int-test-trace-01",
    "requestId": "int-test-req-01",
    "sessionId": "int-test-session-01",
    "appId": 1,
    "userId": "1",
}


def _login_token(client: httpx.Client) -> str:
    resp = client.post(LOGIN_URL, json=LOGIN_BODY)
    resp.raise_for_status()
    payload = resp.json()
    assert payload.get("code") == 0, f"login failed: {payload}"
    token = payload.get("data")
    assert token, f"login returned no token: {payload}"
    return token


def test_stream_full():
    """POST /chat/gen, collect all chunks, verify non-empty text response."""
    print("\n=== TEST 1: full stream ===")
    chunks = []
    with httpx.Client(timeout=120) as client:
        token = _login_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        with client.stream("POST", STREAM_URL, json=STREAM_BODY, headers=headers) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_text():
                if chunk:
                    chunks.append(chunk)
                    print(chunk, end="", flush=True)

    total = "".join(chunks)
    assert total.strip(), "Stream returned empty response"
    print(f"\n\n--- STREAM COMPLETE ({len(total)} chars, {len(chunks)} chunks) ---")
    return total


def test_stream_then_stop():
    """POST /chat/gen, read a few chunks, then POST /chat/stop and verify it stops."""
    print("\n=== TEST 2: stream + stop ===")
    received = []
    stop_result = {}
    auth_headers: dict[str, str] = {}

    def do_stream():
        with httpx.Client(timeout=120) as client:
            token = _login_token(client)
            auth_headers["Authorization"] = f"Bearer {token}"
            with client.stream(
                "POST", STREAM_URL, json={**STREAM_BODY, "requestId": "int-test-req-02"},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_text():
                    if chunk:
                        received.append(chunk)
                        print(chunk, end="", flush=True)

    stream_thread = threading.Thread(target=do_stream, daemon=True)
    stream_thread.start()

    # Wait for at least a few chars and the auth header, then stop
    deadline = time.time() + 30
    while time.time() < deadline and (len("".join(received)) < 20 or not auth_headers):
        time.sleep(0.1)

    print(f"\n[stopping after {len(received)} chunks]")
    with httpx.Client(timeout=10) as client:
        stop_resp = client.post(
            STOP_URL, json={**STOP_BODY, "requestId": "int-test-req-02"},
            headers=auth_headers,
        )
        stop_resp.raise_for_status()
        stop_result = stop_resp.json()

    print(f"Stop response: {json.dumps(stop_result, ensure_ascii=False, indent=2)}")
    assert stop_result.get("code") == 0, f"Unexpected stop response: {stop_result}"
    assert stop_result.get("data", {}).get("accepted") is True, \
        f"Unexpected stop payload: {stop_result}"
    stream_thread.join(timeout=10)
    print("--- STOP TEST COMPLETE ---")
    return stop_result


if __name__ == "__main__":
    try:
        test_stream_full()
        test_stream_then_stop()
        print("\n✅ All integration tests passed")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        raise
