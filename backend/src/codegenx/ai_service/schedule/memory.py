"""
记忆离线任务 scheduler —— 常驻单消费者循环。

职责：
1. 从 memory_task 表领取到期任务，按类型分发执行：
   - topic_extract:      水位增量读消息 → 小模型提取 → 写分室 md → 推进水位
   - session_summarize:  全量会话消息 → 小模型压缩 → 覆盖写 session md
   - session_flush:      同 session_summarize（会话关闭/TTL 的强制收尾路径）
   - consolidate:        跨会话整理（去重 / 日常条目过期 / 归档 / 会话 md 保留期清理）
2. 失败退避重试（1m/5m/30m，重试超限置 dead），任务语义 at-least-once
3. LLM 资源管控：独立小模型+ 信号量并发上限
   + 在线让位（检测到活跃会话时先让 1 秒）

生命周期由 main.py 管理：
- startup:  task_store.recover_running()（崩溃恢复）→ worker.start()
- shutdown: worker.stop(grace=10) —— 宽限等待 → 取消 → running 复位 pending
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import logging
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from codegenx.ai_service.utils.config import config
from codegenx.ai_service.llm.async_client import AsyncLLMClient
from codegenx.ai_service.memory.memory_manager import MemoryManager, _parse_fragments
from codegenx.ai_service.schedule.memory_task_store import (
    MemoryTaskStore,
    get_memory_task_store,
    TASK_TOPIC_EXTRACT,
    TASK_SESSION_SUMMARIZE,
    TASK_SESSION_FLUSH,
    TASK_CONSOLIDATE,
)
from db.chat_store import chat_store as _chat_store  # 存储是用的文件
from codegenx.ai_service.prompt.runtime_prompt import AUTO_MEMORY_PROMPT
from shared.constants import get_memory_dir, DATA_ROOT_DIR

# backend/ 目录（本文件位于 backend/app/tasks/memory/）
_BACKEND_DIR = Path(__file__).resolve().parents[3]

_MEMORY_LINE_RE = re.compile(r"^(\[\d{4}-\d{2}-\d{2}\])(\[重要\])?\s*(.*)$")

# 单次 consolidate 去重的相似度阈值（difflib 序列相似度，> 该值视为重复条目）
_DEDUP_SIMILARITY = 0.9


class MemoryScheduler:
    """记忆离线任务 。

    用法（main.py）:
        scheduler = MemoryScheduler(online_busy_fn=lambda: session_manager.get_session_count() > 0)
        scheduler.start()
        ...
        await scheduler.stop(grace=10.0)
    """

    def __init__(
        self,
        task_store: MemoryTaskStore | None = None,
        store=None,
        online_busy_fn=None,
        log: logging.Logger | None = None,
    ) -> None:
        self.tasks = task_store or get_memory_task_store()
        self.chats = store or _chat_store
        # 在线忙检测（可选）：返回 True 表示当前有活跃会话，LLM 调用前先让位
        self._online_busy = online_busy_fn
        self.log = log or logging.getLogger("memory_worker")
        # 离线小模型客户端（惰性创建，独立于在线对话的大模型）
        self._llm: AsyncLLMClient | None = None
        # LLM 并发上限：离线提取最多同时 N 个请求，避免挤占在线资源
        self._sem = asyncio.Semaphore(config.memory.store.extract_max_concurrency)
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    # === 生命周期 ===

    def start(self) -> None:
        """启动消费者循环（幂等）。必须在事件循环内调用（FastAPI startup 满足）。"""
        if self._loop_task is None or self._loop_task.done():
            self._stop.clear()
            self._loop_task = asyncio.create_task(self._run_loop())
            self.log.info("MemoryTaskWorker 已启动")

    async def stop(self, grace: float = 10.0) -> None:
        """停止 worker：宽限等待在跑任务 → 超时取消 → running 任务复位 pending。

        复位保证关闭窗口期没跑完的任务下次启动续跑（at-least-once）。
        """
        self._stop.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._loop_task), timeout=grace)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # 宽限期内未结束（如 LLM 请求悬挂）：强制取消
                self._loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._loop_task
            self._loop_task = None
        recovered = self.tasks.recover_running()
        self.log.info(f"MemoryTaskWorker 已停止（复位 running 任务 {recovered} 个）")

    # === 消费循环 ===

    async def _run_loop(self) -> None:
        """单消费者主循环：领取 → 逐个执行 → 空闲轮询。

        单实例顺序消费即可满足吞吐（离线任务对延迟不敏感），无需多 worker 抢占。
        """
        last_schedule_check = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                # 每分钟最多检查一次 consolidate 日调度（廉价查询）
                if now - last_schedule_check >= 60:
                    last_schedule_check = now
                    self._schedule_consolidate(now)

                batch = settings.memory.worker_batch_size
                # 领取任务
                tasks = self.tasks.claim_due(limit=batch)
                if not tasks:
                    # 空闲：等待 stop 信号或轮询间隔（wait 可被 stop() 立即唤醒）
                    await asyncio.wait(self._stop.wait(), timeout=settings.memory.worker_poll_interval)
                    continue

                for task in tasks:
                    if self._stop.is_set():
                        break  # 让位退出；未跑任务仍为 running，stop() 会统一复位
                    await self._process(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单轮异常不退出循环（任务级失败已在 _process 内记录）
                self.log.error(f"worker 循环异常\n{traceback.format_exc()}")
                await asyncio.wait(self._stop.wait(), timeout=settings.memory.worker_poll_interval)

    def _schedule_consolidate(self, now: float) -> None:
        """每日定时调度：到达 consolidate_hour 且今天未登记过则入队全局整理任务。"""
        hour = datetime.now().hour
        if hour < config.memory.store.consolidate_hour:
            return
        if self.tasks.consolidate_scheduled_today():
            return
        # patient_id="*" 表示全局任务（worker 内遍历所有患者目录）
        self.tasks.enqueue(TASK_CONSOLIDATE, patient_id="*", session_id="")
        self.log.info("已登记今日跨会话整理任务（consolidate）")

    async def _process(self, task: dict) -> None:
        """执行单个任务：按类型分发；失败走 mark_failed 退避重试。"""
        task_id = task["id"]
        task_type = task["task_type"]
        started = time.time()
        try:
            if task_type == TASK_TOPIC_EXTRACT:
                await self._do_topic_extract(task)
            elif task_type in (TASK_SESSION_SUMMARIZE, TASK_SESSION_FLUSH):
                await self._do_session_summarize(task)
            elif task_type == TASK_CONSOLIDATE:
                await self._do_consolidate(task)
            else:
                raise ValueError(f"未知任务类型: {task_type}")
            self.tasks.mark_done(task_id)
            self.log.debug(f"任务 #{task_id} {task_type} 完成，耗时 {time.time() - started:.1f}s")
        except Exception as e:
            status = self.tasks.mark_failed(task_id, f"{type(e).__name__}: {e}")
            self.log.warning(
                f"任务 #{task_id} {task_type} 失败（第 {task.get('retry_count', 0) + 1} 次）: {e} → {status}"
            )

    # === 任务实现 ===

    async def _do_topic_extract(self, task: dict) -> None:
        """主题记忆增量提取：水位 → 完整轮次 → LLM → 写分室 md → 推进水位。

        只消费「以 assistant 收尾的完整轮次」：assistant 消息在响应流结束后才落库，
        半截对话（只有 user 提问）留在水位之后，等下个任务与后续轮次合并提取。
        """
        patient_id = task["patient_id"]
        session_id = task["session_id"]

        watermark = self.tasks.get_watermark(session_id)
        messages = self.chats.get_messages_since(session_id, watermark)
        if not messages:
            return

        turns = MemoryManager.split_messages_into_turns(messages)
        if turns and turns[-1] and turns[-1][-1].get("role") != "assistant":
            turns = turns[:-1]  # 末尾轮次不完整，留待下次
        if not turns:
            return

        window_text = MemoryManager.render_turns_text(turns)
        if window_text:
            raw = await self._invoke_llm(
                messages=[
                    {"role": "system", "content": AUTO_MEMORY_PROMPT},
                    {"role": "user", "content": f"本轮对话片段：\n{window_text}"},
                ],
                max_tokens=1024,
            )
            fragments = _parse_fragments(raw)
            if fragments:
                written = self._build_manager(patient_id, session_id).write_fragments(fragments)
                if written:
                    self.log.info(f"[topic_extract] 会话 {session_id} 写入 {written} 条主题记忆")
            else:
                self.log.debug(f"[topic_extract] 会话 {session_id} 本批无有效增量信息")

        # 提取成功才推进水位；LLM 失败已抛异常走重试，水位不动 → 下次续提同一段
        self.tasks.advance_watermark(session_id, patient_id, int(turns[-1][-1]["msg_rowid"]))

    async def _do_session_summarize(self, task: dict) -> None:
        """会话记忆压缩：全量落库消息 → LLM 摘要 → 覆盖写 {session_id}.md。

        执行时读库而非入队时的快照 —— 排队期间的新消息一并覆盖，天然幂等。
        """
        patient_id = task["patient_id"]
        session_id = task["session_id"]

        messages = self.chats.get_messages(session_id)
        if not messages:
            return

        manager = self._build_manager(patient_id, session_id)
        ok = await manager.session_memory.extract_and_save(messages)
        if not ok:
            raise RuntimeError("session summarize 失败（LLM 异常）")

    async def _do_consolidate(self, task: dict) -> None:
        """跨会话整理：分室去重/过期/归档 + 会话 md 保留期清理（5.3.7 节）。

        patient_id="*" 时遍历所有患者目录；纯 Python 规则处理（difflib 近似去重），
        不消耗 LLM —— 大部分整理工作无需模型参与。
        """
        patient_id = task["patient_id"]
        if patient_id == "*":
            # 患者数据根目录（backend/data/，与 plugins/utils.get_patient_dir 的解析规则一致）
            base = DATA_ROOT_DIR
            if not base.is_absolute():
                base = _BACKEND_DIR / base
            patient_ids = [
                d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
            ] if base.exists() else []
        else:
            patient_ids = [patient_id]

        for pid in patient_ids:
            try:
                self._consolidate_patient(pid)
            except Exception:
                # 单患者失败不阻断其他患者
                self.log.error(f"[consolidate] 患者 {pid} 整理异常\n{traceback.format_exc()}")

    # === consolidate 具体逻辑 ===

    def _consolidate_patient(self, patient_id: str) -> None:
        """整理单个患者：四个分室去重/过期/归档 + 过期会话 md 删除。"""
        memory_dir = get_memory_dir(patient_id)
        today = datetime.now().date()

        for room in ("diagnosis_treatment", "medication", "lab_result", "general"):
            file_path = memory_dir / f"{room}.md"
            if not file_path.exists():
                continue
            removed = self._dedup_and_expire_room(file_path, room, today)
            if removed:
                self.log.info(f"[consolidate] 患者 {patient_id} {room} 清理 {len(removed)} 条 → 归档")

        # 会话 md 保留期清理：文件名为 UUID 的（会话记忆），mtime 超期即删除
        retention_days = config.memory.store.session_retention_days
        now_ts = time.time()
        for f in memory_dir.glob("*.md"):
            if not _is_uuid(f.stem):
                continue
            if now_ts - f.stat().st_mtime > retention_days * 86400:
                f.unlink(missing_ok=True)
                self.log.debug(f"[consolidate] 已删除过期会话记忆: {f.name}")

    def _dedup_and_expire_room(self, file_path: Path, room: str, today) -> list[str]:
        """单分室整理：过期过滤（general）+ 近似去重，清理条目写入归档文件。

        返回被清理的原始行列表。写回采用「临时文件 + replace」两步，
        避免中途崩溃导致文件截断。
        """
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # 分离 frontmatter 与正文条目
        body_start = 0
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    body_start = i + 1
                    break
        head, entries = lines[:body_start], lines[body_start:]


        ttl_days = config.memory.store.general_ttl_days
        kept: list[str] = []
        removed: list[str] = []
        for line in entries:
            stripped = line.strip()
            if not stripped:
                continue  # 空行直接清理
            m = _MEMORY_LINE_RE.match(stripped)
            if m is None:
                kept.append(line)  # 非条目格式（人工写入等），原样保留
                continue
            date_str, _, content = m.group(1), m.group(2), m.group(3)
            # 日常交互室条目过期（7 天热度窗口）
            if room == "general":
                try:
                    entry_date = datetime.strptime(date_str, "[%Y-%m-%d]").date()
                except ValueError:
                    entry_date = None
                if entry_date and (today - entry_date).days > ttl_days:
                    removed.append(stripped)
                    continue
            # 近似去重：与已保留条目相似度过高则丢弃旧的（本次这条更新）
            if any(_similar(stripped, other) for other in kept):
                removed.append(stripped)
                continue
            kept.append(stripped)

        if not removed:
            return []

        # 两步写回：tmp + replace
        tmp = file_path.with_suffix(".md.tmp")
        tmp.write_text("\n".join(head + kept) + "\n", encoding="utf-8")
        tmp.replace(file_path)

        # 归档：archive/{room}-{yyyymm}.md（按月分文件，append-only）
        archive_dir = file_path.parent / "archive"
        archive_dir.mkdir(exist_ok=True)
        archive_file = archive_dir / f"{room}-{datetime.now().strftime('%Y%m')}.md"
        with open(archive_file, "a", encoding="utf-8") as f:
            f.write("\n".join(removed) + "\n")
        return removed

    # === LLM 调用（小模型 + 并发管控）===

    def _get_llm(self) -> AsyncLLMClient:
        """惰性创建离线提取小模型客户端。"""
        if self._llm is None:
            llm_model = config.memory.store.model_name or config.get_default_model()
            self._llm = AsyncLLMClient(llm_model)
            self.log.info(f"离线记忆小模型已初始化: {config['model']} @ {config['base_url']}")
        return self._llm

    async def _invoke_llm(self, messages: list[dict], max_tokens: int = 1024) -> str:
        """受控 LLM 调用：信号量限并发 + 在线让位（有活跃会话时先等 1 秒）。"""
        async with self._sem:
            if self._online_busy is not None and self._online_busy():
                # 在线对话优先：短暂让位后再调用（只延迟一次，不无限等待）
                await asyncio.sleep(1.0)
            return await self._get_llm().invoke(
                messages=messages, max_tokens=max_tokens, temperature=0.0
            )

    def _build_manager(self, patient_id: str, session_id: str) -> MemoryManager:
        """构建离线任务专用的 MemoryManager。

        ctx 用轻量命名空间模拟插件上下文：logger 指向 worker 日志，
        llm.get_llm_service() 指向离线小模型（SessionMemory._session_summarize
        内部通过该接口取客户端，天然路由到小模型）。
        """
        ctx = SimpleNamespace(
            logger=self.log,
            llm=SimpleNamespace(get_llm_service=lambda: self._get_llm()),
        )
        return MemoryManager(patient_id, session_id, ctx)


def _similar(a: str, b: str) -> bool:
    """近似重复判断：完全相等或序列相似度 ≥ 阈值。"""
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= _DEDUP_SIMILARITY


def _is_uuid(name: str) -> bool:
    """判断文件名（不含扩展名）是否为 UUID（用于识别会话记忆 md）。"""
    try:
        from uuid import UUID
        UUID(name)
        return True
    except (ValueError, AttributeError):
        return False
