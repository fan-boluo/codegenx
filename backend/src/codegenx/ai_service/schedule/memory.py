"""
记忆离线任务 scheduler —— 常驻单消费者循环。

职责（设计文档 §5.2 / §6）：
1. 从 memory_task 表领取到期任务，按类型分发执行：
   - warm_extract:   水位增量读对话 → 小模型提取候选记忆 → 判重/仲裁 → 双写 → 推进水位
   - consolidate:    每日跨会话整理（近似去重合并）
   - decay_archive:  30 天未访问软删除 / 90 天归档（jsonl→zip + Qdrant 物理删除）
   - sync_check:     json 真相源 → Qdrant 对账补写（检查点增量）
2. 失败退避重试（1m/5m/30m，超限置 dead），任务语义 at-least-once
3. LLM 资源管控：独立小模型 + 信号量并发上限 + 在线让位（有活跃会话先让 1 秒）

生命周期由 services/agent_adapter_service.py 管理：
- startup: task_store.recover_running()（崩溃恢复）→ ensure_warm_collection → worker.start()
- shutdown: worker.stop(grace) —— 宽限等待 → 取消 → running 复位 pending
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import traceback
from datetime import datetime

from shared import log

from codegenx.ai_service.utils.config import config
from codegenx.ai_service.llm.async_client import AsyncLLMClient
from codegenx.ai_service.memory.prompts import (
    MEMORY_EXTRACT_SYSTEM_PROMPT,
    format_conversation_for_extract,
    parse_extracted_memories,
)
from codegenx.ai_service.memory.writer import write_memories
from codegenx.ai_service.schedule.memory_task_store import (
    MemoryTaskStore,
    get_memory_task_store,
    TASK_WARM_EXTRACT,
    TASK_CONSOLIDATE,
    TASK_DECAY_ARCHIVE,
    TASK_SYNC_CHECK,
)
from codegenx.ai_service.session.manager import SessionManager

# 单次提取消费的消息量上限（防止单任务过大拖垮小模型）
_MAX_MESSAGES_PER_EXTRACT = 80
_MESSAGE_TEXT_LIMIT = 600


class MemoryScheduler:
    """记忆离线任务调度器（单消费者）。

    用法（agent_adapter_service）:
        scheduler = get_memory_scheduler()
        await scheduler.startup()   # 崩溃恢复 + 启动循环
        ...
        await scheduler.shutdown(grace=10.0)
    """

    def __init__(
        self,
        task_store: MemoryTaskStore | None = None,
        online_busy_fn=None,
        poll_interval: float = 15.0,
        batch_size: int = 5,
    ) -> None:
        self.tasks = task_store or get_memory_task_store()
        # 在线忙检测（可选）：True 表示有活跃会话，LLM 调用前先让位
        self._online_busy = online_busy_fn
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        # 离线小模型客户端（惰性创建，独立于在线对话模型）
        self._llm: AsyncLLMClient | None = None
        # 离线 LLM 并发上限：避免挤占在线对话资源
        self._sem = asyncio.Semaphore(config.memory.store.extract_max_concurrency or 2)
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    # === 生命周期 ===

    async def startup(self) -> None:
        """崩溃恢复 + 启动消费循环（幂等，必须在事件循环内调用）。"""
        try:
            await self.tasks.recover_running()
        except Exception:  # noqa: BLE001 — MySQL 暂不可用时不阻断主服务启动
            log.error("memory_task 崩溃恢复失败（MySQL 不可用？）\n{}", traceback.format_exc())
        if self._loop_task is None or self._loop_task.done():
            self._stop.clear()
            self._loop_task = asyncio.create_task(self._run_loop())
            log.info("MemoryScheduler 已启动（poll={}s）", self._poll_interval)

    async def shutdown(self, grace: float = 10.0) -> None:
        """停止：宽限等待在跑任务 → 超时取消 → running 复位 pending（下次续跑）。"""
        self._stop.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._loop_task), timeout=grace)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._loop_task
            self._loop_task = None
        try:
            recovered = await self.tasks.recover_running()
            log.info("MemoryScheduler 已停止（复位 running 任务 {} 个）", recovered)
        except Exception:  # noqa: BLE001
            log.warning("MemoryScheduler 停止时复位 running 任务失败")

    # === 消费循环 ===

    async def _run_loop(self) -> None:
        """单消费者主循环：每日调度检查 → 领取 → 逐个执行 → 空闲轮询。"""
        last_schedule_check = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                # 每分钟最多检查一次日调度（廉价查询）
                if now - last_schedule_check >= 60:
                    last_schedule_check = now
                    await self._schedule_daily_tasks()

                batch = await self.tasks.claim_due(limit=self._batch_size)
                if not batch:
                    await asyncio.wait(self._stop.wait(), timeout=self._poll_interval)
                    continue
                for task in batch:
                    if self._stop.is_set():
                        break  # 未跑任务仍为 running，shutdown() 统一复位
                    await self._process(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 单轮异常不退出循环
                log.error("MemoryScheduler 循环异常\n{}", traceback.format_exc())
                await asyncio.wait(self._stop.wait(), timeout=self._poll_interval)

    async def _schedule_daily_tasks(self) -> None:
        """每日定时登记：consolidate（跨会话整理）+ decay_archive（衰减归档）。"""
        hour = datetime.now().hour
        trigger_hour = config.memory.store.consolidate_hour or 3
        if hour >= trigger_hour:
            if not await self.tasks.consolidate_scheduled_today():
                await self.tasks.enqueue(TASK_CONSOLIDATE, app_id="*")
                log.info("已登记今日跨会话整理任务（consolidate）")
            if not await self.tasks.decay_scheduled_today():
                await self.tasks.enqueue(TASK_DECAY_ARCHIVE, app_id="*")
                log.info("已登记今日衰减归档任务（decay_archive）")
            if not await self.tasks.sync_scheduled_today():
                await self.tasks.enqueue(TASK_SYNC_CHECK, app_id="*")
                log.info("已登记今日双数据源对账任务（sync_check）")

    async def _process(self, task: dict) -> None:
        """执行单个任务；失败走 mark_failed 退避重试。"""
        task_id = task["id"]
        task_type = task["task_type"]
        started = time.time()
        try:
            if task_type == TASK_WARM_EXTRACT:
                await self._do_warm_extract(task)
            elif task_type == TASK_CONSOLIDATE:
                await self._do_consolidate(task)
            elif task_type == TASK_DECAY_ARCHIVE:
                await self._do_decay_archive(task)
            elif task_type == TASK_SYNC_CHECK:
                await self._do_sync_check(task)
            else:
                raise ValueError(f"未知任务类型: {task_type}")
            await self.tasks.mark_done(task_id)
            log.debug("任务 #{} {} 完成，耗时 {:.1f}s", task_id, task_type, time.time() - started)
        except Exception as exc:  # noqa: BLE001
            status = await self.tasks.mark_failed(task_id, f"{type(exc).__name__}: {exc}")
            log.warning(
                "任务 #{} {} 失败（第 {} 次）: {} → {}",
                task_id, task_type, task.get("retry_count", 0) + 1, exc, status,
            )

    # === 任务实现 ===

    async def _do_warm_extract(self, task: dict) -> None:
        """warm 层增量提取：水位 → 完整轮次 → 小模型提取 → 判重仲裁双写 → 推进水位。

        只消费「以 assistant 收尾的完整轮次」：assistant 消息在 turn 结束后才落盘，
        半截对话留在水位之后，等下个任务与后续轮次合并提取。
        """
        app_id = str(task.get("app_id") or "")
        session_id = str(task.get("session_id") or "")
        user_id = str(task.get("user_id") or "")
        if not app_id or not session_id:
            log.warning("[warm_extract] 任务缺少 app_id/session_id，跳过: #{}", task["id"])
            return

        # 1. 水位增量读取（对话 jsonl 文件为源）
        file_name, line_no = await self.tasks.get_watermark(session_id)
        records = SessionManager(app_id, session_id).read_messages_since(file_name, line_no)
        if not records:
            return

        # 2. 截到「以 assistant 收尾」的完整前缀；超过单次上限的留待下次
        consumed: list[tuple[str, int, dict]] = []
        for i, rec in enumerate(records):
            if rec[2].get("role") == "assistant":
                consumed = records[: i + 1]
        if not consumed:
            return  # 末尾没有完整轮次
        if len(consumed) > _MAX_MESSAGES_PER_EXTRACT:
            consumed = consumed[-_MAX_MESSAGES_PER_EXTRACT:]
            # 截断后必须仍以 assistant 收尾，否则本轮只读不推进
            while consumed and consumed[-1][2].get("role") != "assistant":
                consumed.pop()
            if not consumed:
                return

        # 3. 小模型提取候选记忆
        turns_text = _render_messages([m for _, _, m in consumed])
        raw = await self._invoke_llm(
            messages=[
                {"role": "system", "content": MEMORY_EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": format_conversation_for_extract(turns_text)},
            ],
            max_tokens=1024,
        )
        candidates = parse_extracted_memories(raw)

        # 4. 判重/仲裁 + 双写（jsonl 事实源先行）
        if candidates:
            written = await write_memories(app_id, session_id, candidates, self._invoke_llm)
            if written:
                log.info("[warm_extract] 会话 {} 写入 {} 条记忆", session_id, written)

        # 5. 推进水位到消费到的最后一行；LLM 失败已抛异常走重试，水位不动
        last_file, last_line, _ = consumed[-1]
        await self.tasks.advance_watermark(session_id, app_id, user_id, last_file, last_line)

    async def _do_consolidate(self, task: dict) -> None:
        """每日跨会话整理：全 app 近似去重合并（idle 时段运行，不耗 LLM）。"""
        from codegenx.ai_service.memory.lifecycle import consolidate_all_apps
        await consolidate_all_apps()

    async def _do_decay_archive(self, task: dict) -> None:
        """衰减归档：30 天未访问软删除 + 90 天归档（jsonl→zip + Qdrant 删除）。"""
        from codegenx.ai_service.memory.lifecycle import decay_and_archive_all_apps
        await decay_and_archive_all_apps()

    async def _do_sync_check(self, task: dict) -> None:
        """双数据源对账：检查点增量扫描 json 侧，补写/修正 Qdrant。"""
        from codegenx.ai_service.memory.sync import reconcile_all_apps
        await reconcile_all_apps(self._invoke_llm)

    # === LLM 调用（小模型 + 并发管控）===

    def _get_llm(self) -> AsyncLLMClient:
        """惰性创建离线提取小模型客户端（model_name 未配置则用默认模型）。"""
        if self._llm is None:
            llm_model = config.memory.store.model_name or config.get_default_model()
            self._llm = AsyncLLMClient(llm_model)
            log.info("离线记忆小模型已初始化: {}", llm_model)
        return self._llm

    async def _invoke_llm(self, messages: list[dict], max_tokens: int = 1024) -> str:
        """受控 LLM 调用：信号量限并发 + 在线让位（有活跃会话先等 1 秒）。"""
        async with self._sem:
            if self._online_busy is not None and self._online_busy():
                # 在线对话优先：短暂让位后再调用（只延迟一次，不无限等待）
                await asyncio.sleep(1.0)
            return await self._get_llm().invoke(
                messages=messages, max_tokens=max_tokens, temperature=0.0
            )


# === 全局单例 ===

_global_memory_scheduler: MemoryScheduler | None = None


def get_memory_scheduler() -> MemoryScheduler:
    """全局 scheduler 单例（worker 循环与访问缓冲都要求进程内唯一）。"""
    global _global_memory_scheduler
    if _global_memory_scheduler is None:
        _global_memory_scheduler = MemoryScheduler()
    return _global_memory_scheduler


# === 消息渲染 ===

def _render_messages(messages: list[dict]) -> str:
    """把消息列表渲染为提取 prompt 输入（压缩 tool 内容，控制总长）。"""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "; ".join(str(item.get("content", "")) for item in content)
        content = str(content or "").strip()
        if content:
            parts.append(f"[{role}]: {content[:_MESSAGE_TEXT_LIMIT]}")
        for tc in msg.get("tool_calls", []) or []:
            name = tc.get("name") or (tc.get("function") or {}).get("name", "?")
            parts.append(f"[tool_call]: {name}")
    return "\n".join(parts)
