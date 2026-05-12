"""
Multi-turn integration test: 3 consecutive turns in one session.
chat-service → ai-service → app-service full chain.

Turn 1: 创建一个银行年报rag搜索的前端应用
Turn 2: 修改风格为科技风
Turn 3: 加入角色假设当前登录的用户的管理员，所有的用户都先设置为管理员

Prerequisites (must all be running):
  - ai-service    http://localhost:8002
  - app-service   http://localhost:8004
  - chat-service  http://localhost:8005  (+ MySQL / Redis)

Run from any directory:
  python test/multi_turn_test.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx
import jwt  # PyJWT

# ── service addresses ──────────────────────────────────────────────────────────
APP_SERVICE  = "http://localhost:8004"
CHAT_SERVICE = "http://localhost:8005"

CHAT_STREAM_URL = f"{CHAT_SERVICE}/api/app/chat/gen/code"
APP_ADD_URL     = f"{APP_SERVICE}/api/app/add"
APP_DELETE_URL  = f"{APP_SERVICE}/api/app"

# ── test identity ──────────────────────────────────────────────────────────────
TEST_USER_ID      = 1
TEST_USER_ACCOUNT = "test"
TEST_USER_ROLE    = "user"
JWT_SECRET        = "rainbow"
JWT_ALGORITHM     = "HS256"

SESSION_ID  = f"multi-turn-session-{uuid.uuid4().hex[:8]}"
TRACE_ID    = f"multi-turn-trace-{uuid.uuid4().hex[:8]}"

TURNS = [
    "创建一个银行年报rag搜索的前端应用",
    "修改风格为科技风",
    "加入角色假设当前登录的用户的管理员，所有的用户都先设置为管理员",
]

# ── shared state ───────────────────────────────────────────────────────────────
_TOKEN: str = ""
_APP_ID: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_token() -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id":      TEST_USER_ID,
        "user_account": TEST_USER_ACCOUNT,
        "user_role":    TEST_USER_ROLE,
        "exp":          now + timedelta(hours=2),
        "iat":          now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "X-Trace-Id":    TRACE_ID,
    }


def _stream_one_turn(message: str, request_id: str) -> tuple[str, list[str]]:
    """
    Send one turn and collect all SSE text chunks.
    Returns (full_text, chunks).
    Raises on server-side business error or HTTP error.
    """
    chunks: list[str] = []
    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            CHAT_STREAM_URL,
            headers=_auth_headers(),
            json={
                "appId":     _APP_ID,
                "message":   message,
                "sessionId": SESSION_ID,
                "requestId": request_id,
                "stream":    True,
            },
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                if raw.startswith("event:"):
                    event_name = raw[len("event:"):].strip()
                    if event_name == "business-error":
                        # next line will be the data
                        pass
                    continue
                if raw.startswith("data:"):
                    data_str = raw[len("data:"):].strip()
                    if not data_str:
                        continue
                    try:
                        obj = json.loads(data_str)
                        if "d" in obj:
                            chunks.append(obj["d"])
                            print(obj["d"], end="", flush=True)
                        elif obj.get("error"):
                            raise RuntimeError(
                                f"Server business error: {obj.get('message')} "
                                f"(traceId={obj.get('traceId')})"
                            )
                    except json.JSONDecodeError:
                        pass
    return "".join(chunks), chunks


# ─────────────────────────────────────────────────────────────────────────────
# setup / teardown
# ─────────────────────────────────────────────────────────────────────────────

def setup() -> None:
    global _TOKEN, _APP_ID
    print("=" * 60)
    print("SETUP")
    print("=" * 60)

    _TOKEN = _make_token()
    print(f"  session_id : {SESSION_ID}")
    print(f"  trace_id   : {TRACE_ID}")
    print(f"  user_id    : {TEST_USER_ID}")

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            APP_ADD_URL,
            headers=_auth_headers(),
            json={"initPrompt": "multi-turn test app – safe to delete"},
        )
        resp.raise_for_status()
        _APP_ID = resp.json()["data"]
    print(f"  app_id     : {_APP_ID}")


def teardown() -> None:
    print("\n" + "=" * 60)
    print("TEARDOWN")
    print("=" * 60)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(
                APP_DELETE_URL,
                headers=_auth_headers(),
                params={"appId": _APP_ID},
            )
            resp.raise_for_status()
        print(f"  app_id={_APP_ID} deleted")
    except Exception as exc:
        print(f"  WARNING: teardown failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# multi-turn test
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_turn() -> None:
    results: list[dict] = []

    for idx, message in enumerate(TURNS, start=1):
        request_id = f"multi-turn-req-{idx:02d}-{uuid.uuid4().hex[:6]}"
        print(f"\n{'=' * 60}")
        print(f"TURN {idx}/{len(TURNS)}  request_id={request_id}")
        print(f"USER: {message}")
        print("-" * 60)

        t0 = time.perf_counter()
        try:
            full_text, chunks = _stream_one_turn(message, request_id)
            elapsed = time.perf_counter() - t0

            assert full_text.strip(), f"Turn {idx} returned empty response"

            results.append({
                "turn":       idx,
                "message":    message,
                "ok":         True,
                "chars":      len(full_text),
                "chunks":     len(chunks),
                "elapsed_s":  round(elapsed, 2),
            })
            print(f"\n[turn {idx} OK | {len(full_text)} chars | {len(chunks)} chunks | {elapsed:.1f}s]")

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            results.append({
                "turn":      idx,
                "message":   message,
                "ok":        False,
                "error":     str(exc),
                "elapsed_s": round(elapsed, 2),
            })
            print(f"\n[turn {idx} FAILED after {elapsed:.1f}s: {exc}]")

        # Brief pause between turns to let the session settle
        if idx < len(TURNS):
            time.sleep(1)

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)
    failed = 0
    for r in results:
        if r["ok"]:
            print(
                f"  ✅ Turn {r['turn']}: {r['chars']} chars, "
                f"{r['chunks']} chunks, {r['elapsed_s']}s"
            )
        else:
            failed += 1
            print(f"  ❌ Turn {r['turn']}: {r['error']}")

    if failed:
        raise AssertionError(f"{failed}/{len(TURNS)} turn(s) failed — see summary above")


# ─────────────────────────────────────────────────────────────────────────────
# entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup()
    try:
        test_multi_turn()
        print("\n✅ Multi-turn integration test passed")
    except Exception as exc:
        print(f"\n❌ Multi-turn test failed: {exc}")
        raise
    finally:
        teardown()
