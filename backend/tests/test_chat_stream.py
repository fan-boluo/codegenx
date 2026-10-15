"""对话流接口测试脚本：POST /api/ai/chat/gen（generate_code_stream）

用法示例：
  # 1. 走登录接口拿 token（推荐）
  python scripts/test_chat_stream.py --account userx --password 123456 -m "你好"

  # 2. 已有 token 直接用
  python scripts/test_chat_stream.py --token <JWT> -m "帮我分析下销量趋势"

  # 3. 复用同一 sessionId 多轮对话
  python scripts/test_chat_stream.py --session-id <旧sessionId> -m "继续"

  # 4. 交互式连续对话（不传 -m 则进入交互模式，exit 退出）
  python scripts/test_chat_stream.py --account userx --password 123456

可选环境变量：AI_TEST_BASE_URL（默认 http://127.0.0.1:8000）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import requests

BASE_URL = os.environ.get("AI_TEST_BASE_URL", "http://127.0.0.1:8456")
TIMEOUT = (10, 600)  # (连接超时, 读取超时)



def do_login(base_url: str, account: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/api/user/login",
        json={"userAccount": account, "userPassword": password},
        timeout=TIMEOUT,
    )
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"登录失败: HTTP {resp.status_code} body={body}")
    return body["data"]


def stream_chat(
    base_url: str,
    token: str,
    app_id: str,
    message: str,
    session_id: str,
    trace_id: str,
    request_id: str,
) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "appId": app_id,
        "message": message,
        "sessionId": session_id,
        "traceId": trace_id,
        "requestId": request_id,
        "user_id":user_id,
        "db_name":db_name,

    }
    print(f"\n>>> [POST] {base_url}/api/ai/chat/gen")
    print(f"    traceId={trace_id} requestId={request_id} sessionId={session_id}")
    print(f"    message: {message}\n")

    start = time.monotonic()
    first_chunk_at = None
    chunk_count = 0
    collected: list[str] = []

    with requests.post(
        f"{base_url}/api/ai/chat/gen",
        json=payload,
        headers=headers,
        stream=True,
        timeout=TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            print(f"[HTTP {resp.status_code}] {resp.text}")
            sys.exit(1)

        print("<<< 回复流:")
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
                print(f"[首包耗时 {first_chunk_at - start:.2f}s]\n")
            print(chunk, end="", flush=True)
            collected.append(chunk)
            chunk_count += 1

    total = time.monotonic() - start
    text = "".join(collected)
    print(f"\n\n--- 流结束: 共 {chunk_count} 个 chunk, 总耗时 {total:.2f}s ---\n")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="测试 /api/ai/chat/gen 对话流接口")
    parser.add_argument("--base-url", default=BASE_URL, help="后端地址")
    parser.add_argument("--token", default="", help="直接指定 JWT token（跳过登录）")
    parser.add_argument("--account", default=account, help="登录账号")
    parser.add_argument("--password", default=password, help="登录密码")
    parser.add_argument("--app-id", default="app_0001", help="应用 ID")
    parser.add_argument("--session-id", default="", help="复用会话 ID（不传则新生成）")
    parser.add_argument("-m", "--message", default=user_message, help="要发送的消息（不传则进入交互模式）")
    args = parser.parse_args()

    token = args.token
    if not token:
        if not (args.account and args.password):
            parser.error("必须提供 --token，或同时提供 --account 和 --password")
        token = do_login(args.base_url, args.account, args.password)
        print(f"[登录成功] token 前 20 位: {token[:20]}...")

    session_id = args.session_id or f"s_{uuid.uuid4().hex[:16]}"

    if args.message:
        stream_chat(
            base_url=args.base_url,
            token=token,
            app_id=args.app_id,
            message=args.message,
            session_id=session_id,
            trace_id=uuid.uuid4().hex,
            request_id=uuid.uuid4().hex,
        )
        return

    # 交互模式：同一 session 多轮对话
    print(f"[交互模式] sessionId={session_id}（输入 exit 退出）")
    while True:
        try:
            message = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not message or message.lower() in ("exit", "quit", "q"):
            break
        stream_chat(
            base_url=args.base_url,
            token=token,
            app_id=args.app_id,
            message=message,
            session_id=session_id,
            trace_id=uuid.uuid4().hex,
            request_id=uuid.uuid4().hex,
        )


if __name__ == "__main__":
    account = "boluo"
    password = "boluo123"
    app_id = "app_0001"
    # 身份一律由路由层从 JWT 登录态覆写（历史默认值 "userx" 已删，防止会话/工作区挂错用户）
    user_id="user_0003"
    db_name="prj_ec"
    # user_message = "hello"
    # user_message = "2022年卖出了多少件产品"
    # user_message2 = "2022年订单金额Top3的用户是哪几位？金额分别多少"
    # user_message = "一共多少用户？一共多少产品"
    # user_message = "2023年订单位于 order_2023.csv，2022年定义位于数据库，对比2022和2023订单数量"
    user_message = "2023年订单位于 order_2023.csv,计算订单数量"
    main()
