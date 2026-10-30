"""TraceStore：trace/span 异步批量落库（PostgreSQL telemetry schema）。

- 内存队列 + 懒启动后台 worker，攒批 executemany 写入（复用语义层 PG 连接池）
- 失败重试 1 次，仍失败丢弃并记 error 日志（追踪降级，不阻断主链路）
- JSONB 字段以 json.dumps 字符串 + ::jsonb 转换写入
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from shared import log

from db.postgres.postgres_client import get_postgres_pool

# 行字段与列序一致（recorder 产出 dict，此处按序转 tuple）
_TRACE_FIELDS = (
    "trace_id", "session_id", "app_id", "user_id", "event",
    "status", "error", "total_tokens", "model",
    "prompt_tokens", "completion_tokens", "duration_ms",
    "started_at", "ended_at", "tags",
)
_SPAN_FIELDS = (
    "span_id", "trace_id", "parent_span_id", "session_id", "turn_id",
    "op_type", "name", "agent", "seq", "started_at",
    "duration_ms", "status", "error", "input", "output", "attributes",
)
_JSON_FIELDS_SPANS = {"input", "output", "attributes"}  # span 表 JSONB 列
_JSON_FIELDS_TRACES = {"tags"}                          # trace 表仅 tags 为 JSONB
_JSON_FIELDS_SEMANTICS = {"tool_args", "tool_result"}   # 工具语义表 JSONB 列（入参/结果分列）

# 工具语义行（hook/tool_semantics.py 监听器产出，评测消费，独立于 span 骨架）
_SEMANTIC_FIELDS = (
    "tool_call_id", "trace_id", "span_id", "tool_name",
    "success", "tool_args", "tool_result", "created_at",
)

_INSERT_TRACE_SQL = """
INSERT INTO telemetry.traces
  (trace_id, session_id, app_id, user_id, event,
   status, error, total_tokens, model,
   prompt_tokens, completion_tokens, duration_ms,
   started_at, ended_at, tags)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
ON CONFLICT (trace_id) DO UPDATE SET
  status = EXCLUDED.status,
  error = EXCLUDED.error,
  total_tokens = EXCLUDED.total_tokens,
  model = EXCLUDED.model,
  prompt_tokens = EXCLUDED.prompt_tokens,
  completion_tokens = EXCLUDED.completion_tokens,
  duration_ms = EXCLUDED.duration_ms,
  ended_at = EXCLUDED.ended_at,
  tags = EXCLUDED.tags
"""

_INSERT_SPAN_SQL = """
INSERT INTO telemetry.trace_spans
  (span_id, trace_id, parent_span_id, session_id, turn_id,
   op_type, name, agent, seq, started_at, duration_ms,
   status, error, input, output, attributes)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb,$16::jsonb)
ON CONFLICT (span_id) DO NOTHING
"""

_INSERT_SEMANTIC_SQL = """
INSERT INTO telemetry.tool_semantics
  (tool_call_id, trace_id, span_id, tool_name, success, tool_args, tool_result, created_at)
VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8)
ON CONFLICT (tool_call_id) DO UPDATE SET
  span_id = EXCLUDED.span_id, success = EXCLUDED.success,
  tool_args = EXCLUDED.tool_args, tool_result = EXCLUDED.tool_result
"""


def _j(obj: Any) -> str:
    """JSON 序列化（非可序列化对象降级 str）。"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def _row_tuple(row: dict[str, Any], fields: tuple[str, ...], json_fields: set[str]) -> tuple:
    """dict 行 → 列序 tuple；JSONB 字段序列化为字符串。"""
    return tuple(_j(row.get(f)) if f in json_fields else row.get(f) for f in fields)


class TraceStore:
    """单例异步批量写入器：enqueue 入队，后台 worker 攒批落库。"""

    def __init__(self, batch_size: int = 200) -> None:
        self._queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self._batch_size = max(1, int(batch_size))
        self._worker: asyncio.Task | None = None
        self._pending = 0                     # 已入队未落库的行数（含攒批窗口中的行）
        self._drained = asyncio.Event()       # 全部落库后置位（flush 排空信号）
        self._drained.set()

    # ------------------------------------------------------------ 入队

    def enqueue(self, table: str, row: dict[str, Any]) -> None:
        """入队一行（table ∈ traces/spans/tool_semantics）；首次调用懒启动 worker。"""
        self._pending += 1
        self._drained.clear()
        self._queue.put_nowait((table, row))
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())

    # ------------------------------------------------------------ 后台 worker

    async def _run(self) -> None:
        """攒批主循环：首条阻塞等待，窗口 0.5s 内排空至 batch_size。"""
        while True:
            batch = [await self._queue.get()]
            deadline = asyncio.get_running_loop().time() + 0.5
            while len(batch) < self._batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), remaining))
                except asyncio.TimeoutError:
                    break
            try:
                await self._write_batch(batch)
            finally:
                self._pending -= len(batch)
                if self._pending <= 0:  # 全部落库（含重试丢弃），置排空信号
                    self._pending = 0
                    self._drained.set()

    async def _write_batch(self, batch: list[tuple[str, dict]]) -> None:
        """批量写库；失败重试 1 次，仍失败丢弃并记日志（追踪降级）。"""
        traces = [_row_tuple(r, _TRACE_FIELDS, _JSON_FIELDS_TRACES) for t, r in batch if t == "traces"]
        spans = [_row_tuple(r, _SPAN_FIELDS, _JSON_FIELDS_SPANS) for t, r in batch if t == "spans"]
        semantics = [_row_tuple(r, _SEMANTIC_FIELDS, _JSON_FIELDS_SEMANTICS)
                     for t, r in batch if t == "tool_semantics"]
        for attempt in (1, 2):
            try:
                pool = await get_postgres_pool()
                if traces:
                    await pool.executemany(_INSERT_TRACE_SQL, traces)
                if spans:
                    await pool.executemany(_INSERT_SPAN_SQL, spans)
                if semantics:
                    await pool.executemany(_INSERT_SEMANTIC_SQL, semantics)
                return
            except Exception as exc:
                if attempt == 1:
                    log.warning("trace 落库失败，重试: {}", exc)
                    await asyncio.sleep(1.0)
                else:
                    log.error("trace 落库失败，丢弃 {} 行: {}", len(batch), exc)

    # ------------------------------------------------------------ 排空

    async def flush(self, timeout: float = 5.0) -> None:
        """等待全部已入队行落库（session_end 兜底，确保会话数据落库后再销毁）。"""
        if self._pending <= 0 and self._drained.is_set():
            return
        try:
            await asyncio.wait_for(self._drained.wait(), timeout)
        except asyncio.TimeoutError:
            log.warning("trace flush 超时，剩余约 {} 行延后写入", self._pending)
