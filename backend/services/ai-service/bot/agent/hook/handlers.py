from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING
from bot.skill.skill_loader import SkillLoader
from bot.agent.plan.planner import Planner
from bot.memory.manager import MemoryManager
from bot.session.manager import SessionManager
from shared.constants import get_bot_context_dir
from shared.config.log_config import log

if TYPE_CHECKING:
    from bot.agent.runtime import TurnContext

MEMORY_TOOL_NAMES = {
    "memory_search",
    "memory_get",
    "write_short_term",
    "write_long_term",
    "write_identity_memory",
}

class SecurityError(Exception):
    pass


def _is_memory_tool(tool_name: str) -> bool:
    return tool_name in MEMORY_TOOL_NAMES


def _normalize_tool_history_content(result: Any) -> str | None:
    if isinstance(result, dict):
        if result.get("error"):
            return f"Error: {result['error']}"
        if "data" in result and isinstance(result.get("data"), str):
            return result.get("data") or ""
        if "content" in result and isinstance(result.get("content"), str):
            return result.get("content") or ""
    elif isinstance(result, str):
        return result
    return None


def _get_session_manager(context: TurnContext) -> SessionManager:
    session_manager = context.metadata.get("session_manager")
    if session_manager is not None:
        return session_manager

    session_manager = SessionManager(getattr(context, "app_id", "main"))
    context.metadata["session_manager"] = session_manager
    return session_manager


def _load_chat_history(context: TurnContext) -> int:
    if not context.session_id or context.history:
        return len(context.history)

    session_manager = _get_session_manager(context)
    history = session_manager.load_history(context.session_id)
    context.history = history
    context.metadata["chat_history_path"] = str(session_manager._session_file(context.session_id))
    context.metadata["loaded_history_count"] = len(history)
    return len(history)


def _persist_chat_history(context: TurnContext) -> str | None:
    if not context.session_id:
        return None

    session_manager = _get_session_manager(context)
    session_manager.save_history(context.session_id, context.history)
    history_path = session_manager._session_file(context.session_id)
    context.metadata["chat_history_path"] = str(history_path)
    context.metadata["saved_history_count"] = len(context.history)
    return str(history_path)


def _memory_tool_preview(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {"tool_name": tool_name, "app_id": tool_args.get("app_id", "main")}
    if tool_name == "memory_search":
        preview["query"] = str(tool_args.get("query", ""))[:160]
        preview["limit"] = tool_args.get("limit")
    elif tool_name == "memory_get":
        preview["path"] = tool_args.get("path")
        preview["from"] = tool_args.get("from")
        preview["lines"] = tool_args.get("lines")
    elif tool_name in {"write_short_term", "write_long_term", "write_identity_memory"}:
        preview["memory_type"] = tool_args.get("memory_type", "short")
        preview["content_preview"] = str(tool_args.get("content", ""))[:160]
        preview["session_id"] = tool_args.get("session_id", "")
        preview["turn_id"] = tool_args.get("turn_id", "")
    return preview


def _count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _build_memory_audit_view(context: TurnContext) -> dict[str, Any]:
    tool_calls = list(context.metadata.get("memory_tool_calls", []))
    extraction_events = list(context.metadata.get("memory_candidate_extractions", []))
    persisted_candidates = list(context.metadata.get("persisted_memory_candidates", []))

    tool_summary = {
        "total": len(tool_calls),
        "by_stage": _count_by_key(tool_calls, "stage"),
        "by_tool": _count_by_key(tool_calls, "tool_name"),
        "success": sum(1 for item in tool_calls if item.get("stage") == "post" and item.get("success") is True),
        "failed": sum(1 for item in tool_calls if item.get("stage") == "post" and item.get("success") is False),
    }
    extraction_summary = {
        "turns_recorded": len(extraction_events),
        "by_mode": _count_by_key(extraction_events, "mode"),
        "accepted_total": sum(int(item.get("accepted_count", 0) or 0) for item in extraction_events),
        "raw_total": sum(int(item.get("raw_count", 0) or 0) for item in extraction_events),
        "duplicates_filtered_total": sum(int(item.get("duplicates_filtered", 0) or 0) for item in extraction_events),
        "existing_memory_refs_total": sum(int(item.get("existing_memory_count", 0) or 0) for item in extraction_events),
    }

    return {
        "session_id": context.session_id,
        "trace_id": context.metadata.get("trace_id", ""),
        "turn_count": int(context.metadata.get("turn_count", 0) or 0),
        "total_tokens": int(getattr(context, "session_total_tokens", 0) or 0),
        "last_error": context.metadata.get("last_error", ""),
        "memory_tool_summary": tool_summary,
        "memory_candidate_summary": extraction_summary,
        "memory_tool_calls": tool_calls,
        "memory_candidate_extractions": extraction_events,
        "persisted_memory_candidates": persisted_candidates,
        "memory_summary": context.metadata.get("memory_summary", {}),
        "updated_at": time.time(),
    }


def _render_memory_audit_markdown(audit_view: dict[str, Any]) -> str:
    tool_summary = audit_view.get("memory_tool_summary", {})
    candidate_summary = audit_view.get("memory_candidate_summary", {})
    lines = [
        "# Memory Audit",
        f"session_id: {audit_view.get('session_id', '')}",
        f"trace_id: {audit_view.get('trace_id', '')}",
        f"turn_count: {audit_view.get('turn_count', 0)}",
        f"total_tokens: {audit_view.get('total_tokens', 0)}",
        "",
        "## Memory Tools",
        f"total: {tool_summary.get('total', 0)}",
        f"by_stage: {json.dumps(tool_summary.get('by_stage', {}), ensure_ascii=False)}",
        f"by_tool: {json.dumps(tool_summary.get('by_tool', {}), ensure_ascii=False)}",
        f"success: {tool_summary.get('success', 0)}",
        f"failed: {tool_summary.get('failed', 0)}",
        "",
        "## Candidate Extraction",
        f"turns_recorded: {candidate_summary.get('turns_recorded', 0)}",
        f"by_mode: {json.dumps(candidate_summary.get('by_mode', {}), ensure_ascii=False)}",
        f"accepted_total: {candidate_summary.get('accepted_total', 0)}",
        f"raw_total: {candidate_summary.get('raw_total', 0)}",
        f"duplicates_filtered_total: {candidate_summary.get('duplicates_filtered_total', 0)}",
        f"existing_memory_refs_total: {candidate_summary.get('existing_memory_refs_total', 0)}",
        "",
        f"persisted_memory_candidates: {len(audit_view.get('persisted_memory_candidates', []))}",
    ]

    if audit_view.get("last_error"):
        lines.extend(["", f"last_error: {audit_view['last_error']}"])

    return "\n".join(lines)


def _persist_memory_audit_log(context: TurnContext, memory_mgr: MemoryManager, audit_view: dict[str, Any]) -> str:
    context_dir = Path(get_bot_context_dir(getattr(context, "app_id", "main")))
    context_dir.mkdir(parents=True, exist_ok=True)
    audit_path = context_dir / f"memory_audit_{context.session_id}.jsonl"

    records: list[dict[str, Any]] = []
    for item in audit_view.get("memory_tool_calls", []):
        records.append({"record_type": "memory_tool_call", **item})
    for item in audit_view.get("memory_candidate_extractions", []):
        records.append({"record_type": "memory_candidate_extraction", **item})
    records.append(
        {
            "record_type": "memory_audit_summary",
            "session_id": context.session_id,
            "trace_id": context.metadata.get("trace_id", ""),
            "summary": {
                "turn_count": audit_view.get("turn_count", 0),
                "total_tokens": audit_view.get("total_tokens", 0),
                "memory_tool_summary": audit_view.get("memory_tool_summary", {}),
                "memory_candidate_summary": audit_view.get("memory_candidate_summary", {}),
                "persisted_memory_candidates": len(audit_view.get("persisted_memory_candidates", [])),
            },
        }
    )

    audit_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return str(audit_path)


async def _persist_memory_candidates(context: TurnContext, memory_mgr: MemoryManager) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = {
        (str(item.get("memory_type", "")), str(item.get("content", "")))
        for item in context.metadata.get("persisted_memory_candidates", [])
        if item.get("memory_type") and item.get("content")
    }

    for candidate in context.metadata.get("memory_candidates", []):
        memory_type = candidate.get("memory_type")
        content = str(candidate.get("content", "")).strip()
        if not memory_type or not content:
            continue

        key = (str(memory_type), content)
        if key in seen:
            continue
        seen.add(key)

        try:
            await memory_mgr.write_memory(content, memory_type, context.session_id)
            persisted.append({"memory_type": str(memory_type), "content": content})
        except Exception as exc:
            logger.warning(f"Failed to persist memory candidate: {exc}")

    return persisted

async def on_session_start(context: TurnContext, **kwargs):
    context.app_id = context.app_id or "main"
    context.metadata.setdefault("start_time", time.time())
    context.metadata.setdefault("create_time", time.time())
    context.metadata.setdefault("memory_tool_calls", [])
    context.metadata.setdefault("memory_candidate_extractions", [])
    context.metadata.setdefault("tool_result_overrides", {})
    context.metadata.setdefault("persisted_memory_candidates", [])

    try:
        loaded_count = _load_chat_history(context)
        log.info("[Hook] Loaded {} history messages for session {}", loaded_count, context.session_id)
    except Exception as e:
        log.warning("Failed to load chat history for session {}: {}", context.session_id, e)

    try:
        memory_mgr = context.metadata.get("memory_manager")
        if memory_mgr is None:
            memory_mgr = MemoryManager(context.app_id)
        context.metadata["memory_manager"] = memory_mgr
        if not context.metadata.get("memory_bootstrapped"):
            bootstrap_summary = await memory_mgr.bootstrap()
            context.metadata["memory_summary"] = bootstrap_summary
            context.metadata["static_memory_context"] = memory_mgr.get_static_memory_context()
            context.metadata["memory_bootstrapped"] = True
            log.info(
                "Memory bootstrap complete session={} files={} chunks={}",
                context.session_id,
                bootstrap_summary.get("indexed_files", 0),
                bootstrap_summary.get("indexed_chunks", 0),
            )
        else:
            context.metadata.setdefault("memory_summary", memory_mgr.build_bootstrap_summary())
            context.metadata.setdefault("static_memory_context", memory_mgr.get_static_memory_context())
    except Exception as e:
        log.warning("Failed to initialize MemoryManager: {}", e)
        context.metadata["memory_summary"] = {}
        context.metadata["static_memory_context"] = ""

    try:
        planner = context.metadata.get("planner") or Planner()
        context.metadata["planner"] = planner
        if not context.metadata.get("plan_state_locked"):
            context.plan_state = planner.get_state()
        log.info("Planner initialized. Current plan state: {}", context.plan_state)
    except Exception as e:
        log.warning("Failed to initialize Planner: {}", e)
        context.plan_state = "INIT"

    try:
        if "skill_catalog" not in context.metadata:
            skills = SkillLoader().load_all_skills()
            if skills:
                context.metadata["skill_catalog"] = [
                    {"name": s.name, "description": s.metadata.get("description", "")} 
                    for s in skills
                ]
                log.info("SkillLoader successfully loaded {} skills.", len(skills))
            else:
                context.metadata["skill_catalog"] = []
                log.info("SkillLoader found 0 skills.")
    except Exception as e:
        log.warning("Failed to load skills: {}", e)
        context.metadata["skill_catalog"] = []

    log.info("[Hook] OnSessionStart: Session {} started, trace_id={}", context.session_id, context.metadata["trace_id"])

async def on_turn_start(context: TurnContext, **kwargs):
    context.metadata["turn_count"] = context.metadata.get("turn_count", 0) + 1
    context.metadata["turn_start_time"] = time.time()
    
    # Update plan state
    planner = context.metadata.get("planner")
    if planner and not context.metadata.get("plan_state_locked"):
        try:
            planner.note_round()
            context.plan_state = planner.get_state()
        except Exception as e:
            log.warning("Failed to update planner state: {}", e)
            
    log.info("[Hook] OnTurnStart: Turn {}, count={}", context.turn_id, context.metadata["turn_count"])

async def pre_llm_call(context: TurnContext, **kwargs):
    budget = int(kwargs.get("token_budget", context.token_budget) or context.token_budget or 0)
    projected_total_tokens = int(kwargs.get("projected_total_tokens", context.projected_total_tokens) or 0)
    context.token_budget = budget
    if budget and projected_total_tokens >= budget:
        log.warning("[Hook] PreLLMCall: Token budget exceeded ({} >= {})", projected_total_tokens, budget)

    memory_mgr = context.metadata.get("memory_manager")
    if memory_mgr and context.user_input:
        try:
            recall_payload = await memory_mgr.search_for_prompt(context.user_input, limit=5)
            context.metadata["retrieved_memories"] = recall_payload.get("text", "")
            context.metadata["retrieved_memory_count"] = recall_payload.get("count", 0)
        except Exception as exc:
            log.warning("[Hook] PreLLMCall: memory recall failed: {}", exc)
            context.metadata["retrieved_memories"] = ""
            context.metadata["retrieved_memory_count"] = 0
    
    log.debug("[Hook] PreLLMCall: Ready for LLM call in turn {}", context.turn_id)

async def post_llm_call(context: TurnContext, **kwargs):
    response = kwargs.get("response", {})
    tool_calls = response.get("tool_calls", [])

    usage = kwargs.get("usage") or {}
    usage = {
        "prompt_tokens": int(usage.get("prompt_tokens", context.current_prompt_tokens) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
    context.last_llm_usage = usage

    log.debug("[Hook] PostLLMCall: LLM yielded {} tool calls. Tokens used: {}", len(tool_calls), usage["total_tokens"])

async def pre_tool_use(context: TurnContext, **kwargs):
    tool_call = kwargs.get("tool_call", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("arguments", {}) or tool_call.get("args", {})

    if _is_memory_tool(tool_name):
        context.metadata.setdefault("memory_tool_calls", []).append(
            {
                "stage": "pre",
                "tool_call_id": tool_call.get("id", tool_name),
                **_memory_tool_preview(tool_name, args if isinstance(args, dict) else {}),
            }
        )

    context.metadata["current_tool_start_time"] = time.time()
    log.info("[Hook] PreToolUse: Executing tool {}", tool_name)
    return {}

async def post_tool_use(context: TurnContext, **kwargs):
    tool_call = kwargs.get("tool_call", {})
    tool_name = tool_call.get("name", "")
    result = kwargs.get("result")
    args = tool_call.get("arguments", {}) or tool_call.get("args", {})
    
    start_time = context.metadata.pop("current_tool_start_time", time.time())
    execution_time = time.time() - start_time
    
    log.info("[Hook] PostToolUse: Executed tool {} in {:.3f}s", tool_name, execution_time)
    
    observation = str(result)
    max_len = 5000
    if observation and isinstance(observation, str) and len(observation) > max_len:
        log.warning("[Hook] PostToolUse: Capturing preview for long observation from {} (length {})", tool_name, len(observation))
        context.metadata.setdefault("tool_result_previews", {})[tool_call.get("id", tool_name)] = (
            observation[:max_len] + "\n... [TRUNCATED FOR PREVIEW]"
        )

    history_override = _normalize_tool_history_content(result)
    if history_override is not None:
        context.metadata.setdefault("tool_result_overrides", {})[tool_call.get("id", tool_name)] = history_override

    if observation:
        context.metadata.setdefault("tool_metrics", []).append(
            {
                "tool_name": tool_name,
                "execution_time": execution_time,
                "observation_length": len(observation),
            }
        )
        context.metrics = list(context.metadata.get("tool_metrics", []))
        log.debug("[Hook] PostToolUse: Observation length: {}", len(str(observation)))

    if _is_memory_tool(tool_name):
        context.metadata.setdefault("memory_tool_calls", []).append(
            {
                "stage": "post",
                "tool_call_id": tool_call.get("id", tool_name),
                "execution_time": execution_time,
                "success": not (isinstance(result, dict) and result.get("success") is False),
                "result_preview": observation[:160],
                **_memory_tool_preview(tool_name, args if isinstance(args, dict) else {}),
            }
        )

async def on_turn_end(context: TurnContext, **kwargs):
    start_time = context.metadata.get("turn_start_time", time.time())
    duration = time.time() - start_time
    context.metadata["turn_processing_time"] = duration
    context.metadata["memory_candidate_count"] = len(context.metadata.get("memory_candidates", []))

    try:
        _persist_chat_history(context)
    except Exception as e:
        log.warning("Failed to persist chat history for session {}: {}", context.session_id, e)

    memory_mgr = context.metadata.get("memory_manager")
    if memory_mgr:
        try:
            persisted = await _persist_memory_candidates(context, memory_mgr)
            if persisted:
                context.metadata.setdefault("persisted_memory_candidates", []).extend(persisted)
            context.metadata["memory_candidates"] = []
        except Exception as exc:
            log.warning("Failed to persist memory candidates for session {}: {}", context.session_id, exc)

    audit_view = _build_memory_audit_view(context)
    context.metadata["memory_audit_view"] = audit_view
    context.metadata["memory_audit_display"] = _render_memory_audit_markdown(audit_view)
    
    log.info("[Hook] OnTurnEnd: Turn {} completed in {:.3f}s", context.turn_id, duration)

async def on_error(context: TurnContext, **kwargs):
    error = kwargs.get("error", "Unknown Error")
    error_type = type(error).__name__ if isinstance(error, Exception) else "UnknownType"
    
    context.metadata["last_error"] = str(error)
    context.metadata["last_error_type"] = error_type

    try:
        _persist_chat_history(context)
    except Exception as persist_error:
        log.warning("Failed to persist chat history after error for session {}: {}", context.session_id, persist_error)
    
    log.error("[Hook] OnError: [{}] in session {}: {}", error_type, context.session_id, error)

async def on_session_end(context: TurnContext, **kwargs):
    start_time = context.metadata.get("create_time", time.time())
    duration = time.time() - start_time
    
    total_turns = context.metadata.get("turn_count", 0)
    total_tokens = context.session_total_tokens
    
    # Clean up MemoryManager
    memory_mgr = context.metadata.get("memory_manager")
    if memory_mgr:
        try:
            audit_view = _build_memory_audit_view(context)
            context.metadata["memory_audit_view"] = audit_view
            context.metadata["memory_audit_display"] = _render_memory_audit_markdown(audit_view)
            audit_log_path = _persist_memory_audit_log(context, memory_mgr, audit_view)
            context.metadata["memory_audit_log_path"] = audit_log_path
        except Exception as e:
            log.warning("Failed to finalize memory audit: {}", e)
            
    log.info("[Hook] OnSessionEnd: Session {} ended. Duration={:.2f}s, Turns={}, Tokens={}", context.session_id, duration, total_turns, total_tokens)
