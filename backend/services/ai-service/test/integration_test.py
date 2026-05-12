"""
Integration test for POST /api/ai/codegen/stream and POST /api/ai/codegen/stop
Run from any directory:
  python test/integration_test.py
"""

import asyncio
import json
import threading
import time
import httpx

BASE = "http://localhost:8002"
STREAM_URL = f"{BASE}/api/ai/codegen/stream"
STOP_URL = f"{BASE}/api/ai/codegen/stop"

STREAM_BODY = {
    "trace_id": "int-test-trace-01",
    "request_id": "int-test-req-01",
    "session_id": "int-test-session-01",
    "app_id": "1",
    "user_id": "test-user",
    "message": "帮我写一个Python hello world程序",
    "code_gen_type": "agent",
}

STOP_BODY = {
    "trace_id": "int-test-trace-01",
    "request_id": "int-test-req-01",
    "session_id": "int-test-session-01",
    "app_id": "1",
    "user_id": "test-user",
}


def test_stream_full():
    """POST /stream, collect all chunks, verify non-empty text response."""
    print("\n=== TEST 1: full stream ===")
    chunks = []
    with httpx.Client(timeout=120) as client:
        with client.stream("POST", STREAM_URL, json=STREAM_BODY) as resp:
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
    """POST /stream, read a few chunks, then POST /stop and verify it stops."""
    print("\n=== TEST 2: stream + stop ===")
    received = []
    stop_result = {}

    def do_stream():
        with httpx.Client(timeout=120) as client:
            with client.stream("POST", STREAM_URL, json={**STREAM_BODY, "request_id": "int-test-req-02"}) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_text():
                    if chunk:
                        received.append(chunk)
                        print(chunk, end="", flush=True)

    stream_thread = threading.Thread(target=do_stream, daemon=True)
    stream_thread.start()

    # Wait for at least a few chars, then stop
    deadline = time.time() + 30
    while time.time() < deadline and len("".join(received)) < 20:
        time.sleep(0.1)

    print(f"\n[stopping after {len(received)} chunks]")
    with httpx.Client(timeout=10) as client:
        stop_resp = client.post(STOP_URL, json={**STOP_BODY, "request_id": "int-test-req-02"})
        stop_resp.raise_for_status()
        stop_result = stop_resp.json()

    print(f"Stop response: {json.dumps(stop_result, ensure_ascii=False, indent=2)}")
    assert stop_result.get("accepted") is True, \
        f"Unexpected stop response: {stop_result}"
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
