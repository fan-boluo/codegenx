"""会话历史列表（ChatMessageStore.list_sessions）离线回归测试，不依赖 MySQL/Redis。

背景：历史会话列表原先由 session_index.json 文件索引支撑
（SessionPersistence.upsert_session_index / read_session_index），现已删除，
改为 list_sessions 端点直接聚合 chat_message 表（ChatMessageStore.list_sessions）。

覆盖：
1. list_sessions 返回结构与字段类型（session_id/first_message 字符串化、create_time isoformat）；
2. SQL 参数传递（user_id/app_id/limit）与结果原序返回（排序由 SQL 负责）；
3. create_time 非 datetime 的兜底 str() 路径；
4. 回归：session_index 相关方法/常量已从 SessionPersistence 移除，
   session_pool 不再调用 upsert_session_index。

运行：backend 目录下 `python -m tests.test_chat_session_list`
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import codegenx.ai_service.chat_message.store as store_mod
from codegenx.ai_service.chat_message.store import ChatMessageStore

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """替身 session：记录 execute 入参，原样吐出预置行。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.captured_sql = ""
        self.captured_params: dict | None = None

    async def execute(self, sql, params=None):
        self.captured_sql = str(sql)
        self.captured_params = params
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def test_list_sessions_shape_and_params() -> None:
    import asyncio

    print("[1] list_sessions 返回结构与参数")
    now = datetime(2026, 10, 1, 12, 30, 0)
    rows = [
        ("s-b", "帮我分析销量数据", now),
        ("s-a", "写一段 SQL", "2026-09-30 08:00:00"),  # 非 datetime → 走 str() 兜底
    ]
    fake = _FakeSession(rows)
    orig = store_mod.session_maker
    store_mod.session_maker = lambda: fake
    try:
        store = ChatMessageStore()
        out = asyncio.run(store.list_sessions(user_id="user_1", app_id="app_1", limit=5))
    finally:
        store_mod.session_maker = orig

    check("返回 2 条", len(out) == 2, f"got {len(out)}")
    check("字段齐全", all(set(e) == {"session_id", "first_message", "create_time"} for e in out))
    check("session_id 字符串化", all(isinstance(e["session_id"], str) for e in out))
    check("first_message 字符串化", all(isinstance(e["first_message"], str) for e in out))
    check("datetime → isoformat", out[0]["create_time"] == "2026-10-01T12:30:00", out[0]["create_time"])
    check("非 datetime → str 兜底", isinstance(out[1]["create_time"], str) and "2026-09-30" in out[1]["create_time"])
    check("结果保持 SQL 排序原序", [e["session_id"] for e in out] == ["s-b", "s-a"])

    sql, params = fake.captured_sql, fake.captured_params or {}
    check("参数传递 user_id", params.get("u") == "user_1", str(params))
    check("参数传递 app_id", params.get("a") == "app_1")
    check("参数传递 limit", params.get("l") == 5)
    check("按 user+app 过滤", "user_id = :u" in sql and "app_id = :a" in sql)
    check("仅统计 user 消息", "role = 'user'" in sql)
    check("窗口函数取每会话首条", "ROW_NUMBER() OVER" in sql)
    check("按会话最后活跃倒序", "ORDER BY last_time DESC" in sql)
    check("首条消息截断 50 字符", "1, 50" in sql)


def test_session_index_removed() -> None:
    print("[2] 回归：session_index 已删除")
    from codegenx.ai_service.chat_message import persist as persist_mod
    from codegenx.ai_service.chat_message.persist import SessionPersistence

    check("upsert_session_index 已移除", not hasattr(SessionPersistence, "upsert_session_index"))
    check("read_session_index 已移除", not hasattr(SessionPersistence, "read_session_index"))
    check("_SESSION_INDEX_FILE 常量已移除", not hasattr(persist_mod, "_SESSION_INDEX_FILE"))

    pool_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "codegenx" / "ai_service" / "session" / "session_pool.py"
    ).read_text(encoding="utf-8")
    check("session_pool 不再写会话索引", "upsert_session_index" not in pool_src)

    router_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "codegenx" / "ai_service" / "api" / "router.py"
    ).read_text(encoding="utf-8")
    check("router 不再读 session_index", "SessionPersistence" not in router_src)
    check("router 走 ChatMessageStore.list_sessions", "list_sessions(" in router_src and "get_chat_message_store()" in router_src)


def main() -> int:
    test_list_sessions_shape_and_params()
    test_session_index_removed()
    print(f"\n通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
