"""ContextService / TurnPrompts 组装测试（docs/SystemApp架构设计.md §8 P1 验证）。

覆盖：
  - 组装确定性：同入参同产物；
  - 每轮可变状态隔离（TurnPrompts 实例间不串话）；
  - normalize_history / assemble 形态；
  - 目录骨架 TTL 缓存与失效；
  - get_context_assembler 陷阱单例已删除、ContextService 未装配时快速失败。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.context.context_service import (
    ContextService,
    TurnPrompts,
)


def _sample_prompts() -> TurnPrompts:
    return TurnPrompts(
        memory_prompt="MEM",
        skill_prompt="SKILL",
        task_prompt="TASK",
        session_summary_prompt="SUMMARY",
        workspace_metadata={
            "code_dir": "/tmp/ws",
            "safe_paths": ["/tmp/ws"],
            "allowed_rw_dirs": ["/tmp/ws"],
            "os_name": "windows",
            "project_skeleton": "- a/\n- b.py",
            "db_name": "sales",
            "csv_data_dirs": ["/tmp/csv"],
            "timestamp": "2026-09-25T00:00:00+00:00",
        },
    )


# ── 确定性与隔离 ─────────────────────────────────────────────────────────────


def test_render_turn_context_is_deterministic():
    service = ContextService()
    first = service.render_turn_context(_sample_prompts())
    second = service.render_turn_context(_sample_prompts())
    assert first == second
    assert "MEM" in first and "SKILL" in first and "TASK" in first
    assert "/tmp/ws" in first and "sales" in first


def test_render_reflects_prompt_changes():
    service = ContextService()
    prompts = _sample_prompts()
    with_memory = service.render_turn_context(prompts)
    prompts.memory_prompt = ""
    without_memory = service.render_turn_context(prompts)
    assert "MEM" in with_memory
    assert "MEM" not in without_memory


def test_turn_prompts_instances_are_isolated():
    a, b = TurnPrompts(), TurnPrompts()
    a.workspace_metadata["code_dir"] = "x"
    a.extra = "reminder"
    assert b.workspace_metadata == {}
    assert b.extra == ""
    # 可变默认字段不共享（field default_factory）
    assert a.workspace_metadata is not b.workspace_metadata


def test_base_prompt_empty_without_workspace():
    service = ContextService()
    assert service.build_base_prompt({}) == ""


# ── normalize_history / assemble ─────────────────────────────────────────────


def test_normalize_history_shapes_and_filters():
    raw = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}], "state": "x"},
        {"role": "tool", "content": "res", "tool_call_id": "1", "name": "t"},
        {"role": "weird", "content": "dropped"},
        "not-a-dict",
    ]
    normalized = ContextService.normalize_history(raw)
    assert [m["role"] for m in normalized] == ["system", "user", "assistant", "tool"]
    assert normalized[2]["tool_calls"] == [{"id": "1"}]
    # 标准化只保留白名单字段
    assert "state" not in normalized[2]
    assert normalized[3]["tool_call_id"] == "1"


def test_assemble_prepends_system_prompt():
    service = ContextService()
    messages = asyncio_run(service.assemble("SYS", [{"role": "user", "content": "q"}]))
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert messages[1] == {"role": "user", "content": "q"}


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ── 骨架 TTL 缓存 ────────────────────────────────────────────────────────────


def test_skeleton_cache_ttl_and_invalidate(tmp_path, monkeypatch):
    service = ContextService()
    calls = {"n": 0}
    real_build = service.build_directory_skeleton

    def counting_build(root: Path, **kw) -> str:
        calls["n"] += 1
        return real_build(root, **kw)

    monkeypatch.setattr(service, "build_directory_skeleton", counting_build)

    s1 = service.get_directory_skeleton("u1", "a1", tmp_path)
    s2 = service.get_directory_skeleton("u1", "a1", tmp_path)
    assert s1 == s2
    assert calls["n"] == 1  # 命中缓存，不重扫

    service.invalidate_skeleton("u1", "a1")
    service.get_directory_skeleton("u1", "a1", tmp_path)
    assert calls["n"] == 2  # 失效后重扫

    # 不同 (user, app) 键互不影响
    service.get_directory_skeleton("u2", "a1", tmp_path)
    assert calls["n"] == 3


# ── 陷阱删除与快速失败 ───────────────────────────────────────────────────────


def test_trap_singleton_removed():
    import codegenx.ai_service.context as ctx_pkg
    import codegenx.ai_service.context.context_service as cs

    # get_context_assembler 随 assembler.py 一并删除
    assert not hasattr(cs, "get_context_assembler")
    assert not hasattr(ctx_pkg, "ContextAssembler")


def test_context_service_fail_fast_without_app():
    from codegenx.ai_service.context.session_context import SessionContext
    from codegenx.ai_service.system_app import reset_app

    reset_app()
    # 未 init_app 时 SessionContext 的服务委托应快速失败
    ctx = SessionContext(session_id="s1")
    with pytest.raises(RuntimeError):
        asyncio_run(ctx.assemble())


def test_build_workspace_metadata_shape(tmp_path, monkeypatch):
    import codegenx.ai_service.context.context_service as cs

    monkeypatch.setattr(cs, "ensure_app_workdir", lambda u, a: Path(tmp_path))
    service = ContextService()
    metadata = asyncio_run(service.build_workspace_metadata("u1", "a1", db_name="dbx"))
    assert metadata["code_dir"] == str(tmp_path)
    assert metadata["db_name"] == "dbx"
    assert str(tmp_path) in metadata["safe_paths"]
    assert "os_name" in metadata and "timestamp" in metadata
