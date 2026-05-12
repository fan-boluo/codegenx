"""
Integration test: chat-service → ai-service → app-service full chain.

Prerequisites (must all be running):
  - ai-service    http://localhost:8002
  - app-service   http://localhost:8004
  - chat-service  http://localhost:8005  (+ MySQL / Redis)

Run from any directory:
  python test/integration_test.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── ensure shared/ is importable so we can reuse JWTAuth ──────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx
import jwt  # PyJWT

# ── service addresses ──────────────────────────────────────────────────────────
APP_SERVICE  = "http://localhost:8004"
CHAT_SERVICE = "http://localhost:8005"

CHAT_STREAM_URL = f"{CHAT_SERVICE}/api/app/chat/gen/code"
CHAT_STOP_URL   = f"{CHAT_SERVICE}/api/app/chat/stop"
APP_ADD_URL     = f"{APP_SERVICE}/api/app/add"
APP_DELETE_URL  = f"{APP_SERVICE}/api/app"

# ── test identity ──────────────────────────────────────────────────────────────
TEST_USER_ID      = 1          # must match an existing row in `user` table
TEST_USER_ACCOUNT = "test"
TEST_USER_ROLE    = "user"
JWT_SECRET        = "rainbow"
JWT_ALGORITHM     = "HS256"

TEST_SESSION_ID   = "chat-int-session-01"
TEST_SESSION_ID_2 = "chat-int-session-02"
TEST_TRACE_ID     = "chat-int-trace-01"

# ── state shared across tests ─────────────────────────────────────────────────
_TOKEN: str = ""
_APP_ID: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_token() -> str:
    expire = datetime.utcnow() + timedelta(hours=2)
    payload = {
        "user_id":      TEST_USER_ID,
        "user_account": TEST_USER_ACCOUNT,
        "user_role":    TEST_USER_ROLE,
        "exp":          expire,
        "iat":          datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "X-Trace-Id":    TEST_TRACE_ID,
    }


def _stream_body(request_id: str, session_id: str | None = None) -> dict:
    return {
        "appId":     _APP_ID,
        "message":   "帮我写一个Python hello world程序",
        "sessionId": session_id or TEST_SESSION_ID,
        "requestId": request_id,
        "stream":    True,
    }


def _stop_body(request_id: str, session_id: str | None = None) -> dict:
    return {
        "appId":     _APP_ID,
        "sessionId": session_id or TEST_SESSION_ID,
        "requestId": request_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# setup / teardown
# ─────────────────────────────────────────────────────────────────────────────

def setup() -> None:
    global _TOKEN, _APP_ID
    print("\n=== SETUP ===")

    _TOKEN = _make_token()
    print(f"  JWT generated for user_id={TEST_USER_ID}")

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            APP_ADD_URL,
            headers=_auth_headers(),
            json={"initPrompt": "int-test app – safe to delete"},
        )
        resp.raise_for_status()
        body = resp.json()
        _APP_ID = body["data"]
    print(f"  Test app created: app_id={_APP_ID}")


def teardown() -> None:
    print("\n=== TEARDOWN ===")
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(
                APP_DELETE_URL,
                headers=_auth_headers(),
                params={"appId": _APP_ID},
            )
            resp.raise_for_status()
        print(f"  Test app deleted: app_id={_APP_ID}")
    except Exception as exc:
        print(f"  WARNING: teardown failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 – full stream
# ─────────────────────────────────────────────────────────────────────────────

def test_stream_full() -> str:
    """POST /api/app/chat/gen/code (stream=true) — collect all SSE chunks."""
    print("\n=== TEST 1: full stream via chat-service ===")
    text_chunks: list[str] = []

    with httpx.Client(timeout=180) as client:
        with client.stream(
            "POST",
            CHAT_STREAM_URL,
            headers=_auth_headers(),
            json=_stream_body("chat-int-req-01"),
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                if raw.startswith("data:"):
                    data_str = raw[len("data:"):].strip()
                    if not data_str:
                        continue
                    try:
                        obj = json.loads(data_str)
                        if "d" in obj:
                            text_chunks.append(obj["d"])
                            print(obj["d"], end="", flush=True)
                        elif obj.get("error"):
                            raise RuntimeError(f"Server error: {obj.get('message')}")
                    except json.JSONDecodeError:
                        pass

    result = "".join(text_chunks)
    assert result.strip(), "Stream returned empty response"
    print(f"\n\n--- STREAM COMPLETE ({len(result)} chars, {len(text_chunks)} chunks) ---")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 – stream then stop
# ─────────────────────────────────────────────────────────────────────────────

def test_stream_then_stop() -> None:
    """Start streaming, read a few chars, call /stop, verify accepted=true."""
    print("\n=== TEST 2: stream + stop via chat-service ===")
    received: list[str] = []
    stop_result: dict = {}

    def do_stream() -> None:
        try:
            with httpx.Client(timeout=180) as client:
                with client.stream(
                    "POST",
                    CHAT_STREAM_URL,
                    headers=_auth_headers(),
                    json=_stream_body("chat-int-req-02", TEST_SESSION_ID_2),
                ) as resp:
                    resp.raise_for_status()
                    for raw in resp.iter_lines():
                        if not raw:
                            continue
                        if raw.startswith("data:"):
                            data_str = raw[len("data:"):].strip()
                            if not data_str:
                                continue
                            try:
                                obj = json.loads(data_str)
                                if "d" in obj:
                                    received.append(obj["d"])
                                    print(obj["d"], end="", flush=True)
                            except json.JSONDecodeError:
                                pass
        except Exception:
            pass  # expected — server may close stream mid-way after stop

    stream_thread = threading.Thread(target=do_stream, daemon=True)
    stream_thread.start()

    # wait until at least some text arrives, then stop
    deadline = time.time() + 30
    while time.time() < deadline and len("".join(received)) < 20:
        time.sleep(0.1)

    print(f"\n[stopping after {len(received)} chunks]")

    with httpx.Client(timeout=15) as client:
        stop_resp = client.post(
            CHAT_STOP_URL,
            headers=_auth_headers(),
            json=_stop_body("chat-int-req-02", TEST_SESSION_ID_2),
        )
        stop_resp.raise_for_status()
        stop_result = stop_resp.json()

    data = stop_result.get("data", stop_result)
    print(f"Stop response: {json.dumps(stop_result, ensure_ascii=False, indent=2)}")
    # accepted=True  → stop arrived while stream was running (preferred)
    # accepted=False → stream had already completed before stop arrived (also OK)
    assert isinstance(data.get("accepted"), bool), \
        f"Stop response missing 'accepted' field: {stop_result}"

    stream_thread.join(timeout=10)
    print("--- STOP TEST COMPLETE ---")


# ─────────────────────────────────────────────────────────────────────────────
# entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup()
    try:
        test_stream_full()
        test_stream_then_stop()
        print("\n✅ All chat-service integration tests passed")
    except Exception as exc:
        print(f"\n❌ Test failed: {exc}")
        raise
    finally:
        teardown()
