"""P2 服务化单测：五对象服务化后的全局服务（docs/SystemApp架构设计.md §4.3）。

覆盖：
  - TaskBoardService   ids 进签名、跨会话隔离、依赖自动解锁
  - CompactionService  compact.model_name 模型解析 + 统一失败兜底（保守截断）
  - SessionSummaryService 阈值状态外置（SummaryState 随会话生灭）+ 模型解析

运行：backend/.venv/Scripts/python.exe -m pytest backend/tests/test_system_services.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.task import task_manager as tm_mod
from codegenx.ai_service.task.task_manager import TaskBoardService
from codegenx.ai_service.compact.compact import CompactionService
from codegenx.ai_service.compact.session_summary import (
    SessionSummaryService,
    SummaryState,
)


@pytest.fixture()
def task_board(tmp_path, monkeypatch):
    """把会话目录重定向到 tmp_path，返回 (服务, 目录根)。"""
    monkeypatch.setattr(
        tm_mod, "get_current_session_dir",
        lambda user_id, app_id, session_id: tmp_path / user_id / app_id / session_id,
    )
    return TaskBoardService()


# ── TaskBoardService：ids 隔离 ───────────────────────────────────────────────

def test_task_ids_isolation(task_board):
    """同一服务实例，不同 (user, app, session) 的看板互相不可见。"""
    ids_a = dict(user_id="u1", app_id="a1", session_id="s1")
    ids_b = dict(user_id="u1", app_id="a1", session_id="s2")

    task = task_board.create("分析销售额", **ids_a)
    assert task["id"] == 1

    # 会话 B：看不到 A 的任务
    assert task_board.get(1, **ids_b) is None
    assert task_board.list_all(**ids_b) == []
    assert task_board.get_board(**ids_b) == "No active tasks."

    # 会话 A：可见
    assert task_board.get(1, **ids_a)["subject"] == "分析销售额"
    assert len(task_board.list_all(**ids_a)) == 1


def test_task_dependency_auto_unlock(task_board):
    """完成上游任务后，下游 blockedBy 自动清空并变为 ready。"""
    ids = dict(user_id="u1", app_id="a1", session_id="s1")
    t1 = task_board.create("取数", **ids)
    t2 = task_board.create("出报表", depends_on=[t1["id"]], **ids)
    assert t2["blockedBy"] == [t1["id"]]
    assert not TaskBoardService.is_ready(t2)

    task_board.complete(t1["id"], **ids)
    t2_after = task_board.get(t2["id"], **ids)
    assert t2_after["blockedBy"] == []
    assert TaskBoardService.is_ready(t2_after)


def test_task_invalid_status_rejected(task_board):
    ids = dict(user_id="u1", app_id="a1", session_id="s1")
    task = task_board.create("x", **ids)
    with pytest.raises(ValueError, match="Invalid status"):
        task_board.update(task["id"], status="done", **ids)


# ── CompactionService：模型解析 + 统一失败兜底 ───────────────────────────────

def test_compact_failure_falls_back_to_truncation(monkeypatch):
    """LLM 压缩失败（韧性层耗尽后）统一走保守截断兜底，绝不向上抛错。"""
    import asyncio

    import codegenx.ai_service.compact.thresholds as thresholds_mod
    from codegenx.ai_service.compact import compact as compact_mod

    svc = CompactionService(system_app=None)
    big = [{"role": "user", "content": "x" * 40_000}]  # 远超压缩阈值
    monkeypatch.setattr(thresholds_mod, "should_auto_compact", lambda msgs: True)

    async def _boom(messages, chain):
        raise RuntimeError("resilience exhausted")

    monkeypatch.setattr(compact_mod, "_call_llm_for_summary", _boom)  # Path B 失败

    messages, result = asyncio.run(svc.compact_if_needed(big, summary_loader=None))

    assert result is not None and result.path_used == "none"   # 统一兜底出口
    assert messages == big                                      # 单条消息受 min_keep 保护不被截掉
    assert result.tokens_before == result.tokens_after


def test_compact_model_from_config(monkeypatch):
    """compact.model_name 配置了 → 用它；空 → 回落默认模型。"""
    from codegenx.ai_service.utils.config import config as app_config

    monkeypatch.setattr(app_config.compact, "model_name", "qwen-compact-x")
    assert CompactionService(system_app=None).model_chain == ["qwen-compact-x"]

    monkeypatch.setattr(app_config.compact, "model_name", None)
    assert CompactionService(system_app=None).model_chain == [app_config.get_default_model()]


def test_summary_model_from_config(monkeypatch):
    """会话摘要与压缩同族：共用 compact.model_name，空则默认模型。"""
    from codegenx.ai_service.utils.config import config as app_config

    monkeypatch.setattr(app_config.compact, "model_name", "qwen-compact-x")
    assert SessionSummaryService(system_app=None).model_chain == ["qwen-compact-x"]

    monkeypatch.setattr(app_config.compact, "model_name", None)
    assert SessionSummaryService(system_app=None).model_chain == [app_config.get_default_model()]


# ── SessionSummaryService：阈值状态外置 ──────────────────────────────────────

def _big_messages(n_chars: int) -> list[dict]:
    return [
        {"role": "user", "content": "x" * n_chars},
        {"role": "assistant", "content": "y" * n_chars},
    ]


def test_summary_should_extract_small_context_false():
    """上下文低于初始化阈值：不触发，且不置 initialized。"""
    svc = SessionSummaryService()
    state = SummaryState()
    assert svc.should_extract(state, [{"role": "user", "content": "hi"}]) is False
    assert state.initialized is False


def test_summary_should_extract_large_context_and_no_overlap():
    """超阈值触发一次后（extracting 置位），提取期间不重叠。"""
    svc = SessionSummaryService()
    state = SummaryState()
    big = _big_messages(16000)  # 32000 字符 ÷ 4 = 8000 tokens > MIN_TOKENS_TO_INIT

    assert svc.should_extract(state, big) is True
    assert state.initialized is True

    # 模拟提取中：永不重叠
    state.extracting = True
    assert svc.should_extract(state, big) is False


def test_summary_state_per_session_isolated():
    """两个 SummaryState 互不影响（随会话生灭）。"""
    s1, s2 = SummaryState(), SummaryState()
    svc = SessionSummaryService()
    svc.should_extract(s1, _big_messages(16000))
    assert s1.initialized is True
    assert s2.initialized is False
