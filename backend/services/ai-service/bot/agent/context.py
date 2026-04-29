from __future__ import annotations
import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING

from bot.memory.schema import MemoryType
from shared.config.log_config import log
from bot.utils.context_utils import ensure_context_workdir

if TYPE_CHECKING:
    from bot.agent.runtime import TurnContext


DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="


class SystemPromptBuilder:
    def __init__(self, tool_catalog: list[dict[str, Any]] | None = None):
        self.tool_catalog = tool_catalog or []

    def build(
        self,
        *,
        context: TurnContext,
        workdir: Path,
        plan_context: str,
        memory_sections: list[str],
        skill_catalog: list[dict[str, Any]],
    ) -> str:
        sections: list[str] = []

        core = self._build_core_section(workdir)
        if core:
            sections.append(core)

        tools = self._build_tool_section()
        if tools:
            sections.append(tools)

        skills = self._build_skill_section(skill_catalog)
        if skills:
            sections.append(skills)

        memory = self._build_memory_section(memory_sections)
        if memory:
            sections.append(memory)

        claude_chain = self._build_claude_md_chain(workdir)
        if claude_chain:
            sections.append(claude_chain)

        sections.append(DYNAMIC_BOUNDARY)
        sections.append(self._build_dynamic_context(context, workdir, plan_context))
        return "\n\n".join(part for part in sections if part)

    def _build_core_section(self, workdir: Path) -> str:
        return (
            f"You are a coding agent operating in {workdir}.\n"
            "Use the provided tools to inspect, edit, and validate work.\n"
            "Prefer verification over guessing. Keep working until the task is resolved or blocked."
        )

    def _build_tool_section(self) -> str:
        if not self.tool_catalog:
            return ""
        lines = ["# Available tools"]
        for tool in self.tool_catalog:
            params = ", ".join(tool.get("parameters", []))
            signature = f"{tool['name']}({params})" if params else tool["name"]
            lines.append(f"- {signature}: {tool.get('description', '')}")
        return "\n".join(lines)

    def _build_skill_section(self, skill_catalog: list[dict[str, Any]]) -> str:
        if not skill_catalog:
            return ""
        lines = ["# Available skills"]
        for skill in skill_catalog:
            name = str(skill.get("name", "")).strip()
            description = str(skill.get("description", "")).strip()
            if not name:
                continue
            lines.append(f"- {name}: {description}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _build_memory_section(self, memory_sections: list[str]) -> str:
        cleaned = [section.strip() for section in memory_sections if str(section or "").strip()]
        if not cleaned:
            return ""
        return "# Memory\n" + "\n\n".join(cleaned)

    def _build_claude_md_chain(self, workdir: Path) -> str:
        sources: list[tuple[str, Path]] = []
        user_claude = Path.home() / ".claude" / "CLAUDE.md"
        if user_claude.exists():
            sources.append(("user global (~/.claude/CLAUDE.md)", user_claude))

        project_root_claude = Path.cwd() / "CLAUDE.md"
        if project_root_claude.exists():
            sources.append(("project root (CLAUDE.md)", project_root_claude))

        workdir_claude = workdir / "CLAUDE.md"
        if workdir_claude.exists() and workdir_claude != project_root_claude:
            sources.append((f"workdir ({workdir.name}/CLAUDE.md)", workdir_claude))

        if not sources:
            return ""

        parts = ["# CLAUDE.md instructions"]
        for label, path in sources:
            try:
                content = path.read_text(encoding="utf-8").strip()
            except Exception as exc:
                log.warning("Failed to read {}: {}", path, exc)
                continue
            if not content:
                continue
            parts.append(f"## From {label}")
            parts.append(content)
        return "\n\n".join(parts) if len(parts) > 1 else ""

    def _build_dynamic_context(self, context: TurnContext, workdir: Path, plan_context: str) -> str:
        lines = [
            "# Dynamic context",
            f"Current date: {datetime.date.today().isoformat()}",
            f"Working directory: {workdir}",
            f"App id: {context.app_id}",
            f"Turn id: {context.turn_id}",
            f"Session id: {context.session_id}",
        ]

        if context.metadata.get("requested_code_gen_type"):
            lines.append(f"Requested code generation type: {context.metadata['requested_code_gen_type']}")
        if context.metadata.get("turn_count") is not None:
            lines.append(f"Turn count: {context.metadata['turn_count']}")

        lines.append("Session plan state:")
        lines.append(plan_context)
        return "\n".join(lines)

class ContextAssembler:
    """
    Assembles the context for the LLM call.
    Integrates memory, planner, skills, and subagent context.
    """
    def __init__(self):
        # We can dynamically load or mock these for now
        self.memory_manager = None
        self.planner = None
        self.skill_loader = None
        self.tool_catalog = self._load_tool_catalog()
        self.prompt_builder = SystemPromptBuilder(self.tool_catalog)
        
        # Try to import for Phase 4 stubs
        try:
            from bot.agent.plan.planner import Planner
            self.planner = Planner()
        except ImportError as e:
            log.debug("Components not available yet: {}", e)

    def _load_tool_catalog(self) -> list[dict[str, Any]]:
        try:
            from bot.agent.tool_handler import ToolsHandler

            handler = ToolsHandler()
            catalog: list[dict[str, Any]] = []
            for tool in handler.tools:
                parameters = list((tool.parameters or {}).get("properties", {}).keys())
                catalog.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters,
                    }
                )
            return catalog
        except Exception as exc:
            log.debug("Failed to build tool catalog for system prompt: {}", exc)
            return []

    def _resolve_memory_manager(self, context: TurnContext):
        memory_manager = context.metadata.get("memory_manager") or getattr(self, "memory_manager", None)
        if memory_manager is not None:
            return memory_manager
        from bot.memory.manager import MemoryManager
        return MemoryManager(getattr(context, "app_id", "main"))

    def _build_system_reminder(self, context: TurnContext, planner: Any) -> Dict[str, Any] | None:
        reminders: list[str] = []
        if planner is not None:
            rounds_since_update = getattr(getattr(planner, "state", None), "rounds_since_update", 0)
            interval = getattr(planner, "plan_reminder_interval", 0)
            if interval and rounds_since_update >= interval:
                reminders.append("Refresh the current session plan before continuing.")

        extra_reminder = str(context.metadata.get("system_reminder", "")).strip()
        if extra_reminder:
            reminders.append(extra_reminder)

        if not reminders:
            return None

        return {
            "role": "user",
            "content": "<system-reminder>\n" + "\n".join(reminders) + "\n</system-reminder>",
        }

    def _is_empty_message(self, message: Dict[str, Any]) -> bool:
        return not str(message.get("content", "") or "").strip() and not message.get("tool_calls")

    @staticmethod
    def _model_message_view(message: Dict[str, Any]) -> Dict[str, Any]:
        allowed_keys = {"role", "content", "tool_calls", "tool_call_id", "name"}
        normalized = {key: value for key, value in message.items() if key in allowed_keys}
        if "content" not in normalized:
            normalized["content"] = ""
        return normalized

    @staticmethod
    def _extract_tool_call_refs(message: Dict[str, Any]) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        for tool_call in message.get("tool_calls", []) or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id", "") or "").strip()
            function_payload = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            tool_name = str(tool_call.get("name") or function_payload.get("name") or "").strip()
            if tool_call_id:
                refs.append({"id": tool_call_id, "name": tool_name})
        return refs

    @staticmethod
    def _synthesize_missing_tool_result(tool_call: dict[str, str]) -> Dict[str, Any]:
        tool_call_id = tool_call.get("id", "")
        tool_name = tool_call.get("name", "tool") or "tool"
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": (
                "<missing-tool-result>\n"
                f"Missing tool result for {tool_name} ({tool_call_id}). "
                "Treat the call as unresolved and re-run it if the output is still needed.\n"
                "</missing-tool-result>"
            ),
        }

    def _append_normalized_message(self, normalized: List[Dict[str, Any]], message: Dict[str, Any]) -> None:
        if self._is_empty_message(message):
            return
        if normalized and self._can_merge_messages(normalized[-1], message):
            previous_content = str(normalized[-1].get("content", "")).rstrip()
            current_content = str(message.get("content", "")).lstrip()
            normalized[-1]["content"] = f"{previous_content}\n\n{current_content}".strip()
            return
        normalized.append(message)

    def _can_merge_messages(self, previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
        if previous.get("role") != current.get("role"):
            return False
        if previous.get("role") not in {"system", "user", "assistant"}:
            return False
        if previous.get("tool_calls") or current.get("tool_calls"):
            return False
        if previous.get("tool_call_id") or current.get("tool_call_id"):
            return False
        prev_content = str(previous.get("content", ""))
        curr_content = str(current.get("content", ""))
        if prev_content.startswith("<system-reminder>") or curr_content.startswith("<system-reminder>"):
            return False
        return True

    def normalize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        pending_tool_calls: list[dict[str, str]] = []
        for message in messages:
            current = self._model_message_view(dict(message))

            if current.get("role") != "tool" and pending_tool_calls:
                for tool_call in pending_tool_calls:
                    self._append_normalized_message(normalized, self._synthesize_missing_tool_result(tool_call))
                pending_tool_calls = []

            if current.get("role") == "tool":
                tool_call_id = str(current.get("tool_call_id", "") or "").strip()
                if tool_call_id:
                    pending_tool_calls = [item for item in pending_tool_calls if item.get("id") != tool_call_id]

            self._append_normalized_message(normalized, current)

            if current.get("role") == "assistant":
                pending_tool_calls = self._extract_tool_call_refs(current)

        if pending_tool_calls:
            for tool_call in pending_tool_calls:
                self._append_normalized_message(normalized, self._synthesize_missing_tool_result(tool_call))
        return normalized

    async def assemble(self, context: TurnContext) -> List[Dict[str, Any]]:
        """Build the context messages for the LLM."""
        messages: List[Dict[str, Any]] = []
        workdir = ensure_context_workdir(context)
        memory_manager = self._resolve_memory_manager(context)
        planner = context.metadata.get("planner") or self.planner

        plan_context = context.plan_state
        if not plan_context:
            if planner:
                plan_context = planner.get_state()
            else:
                plan_context = "No active plan."
        context.plan_state = str(plan_context)

        memory_sections: list[str] = []
        static_memory_context = context.metadata.get("static_memory_context", "")
        if static_memory_context:
            memory_sections.append(static_memory_context)

        retrieved_memories = context.metadata.get("retrieved_memories", "")
        if not retrieved_memories and memory_manager and context.user_input:
            try:
                recall_payload = await memory_manager.search_for_prompt(context.user_input, limit=5)
                retrieved_memories = recall_payload.get("text", "")
                context.metadata["retrieved_memories"] = retrieved_memories
                context.metadata["retrieved_memory_count"] = recall_payload.get("count", 0)
            except Exception as e:
                log.error("Error fetching memory: {}", e)
                retrieved_memories = ""

        if retrieved_memories:
            memory_sections.append(retrieved_memories)

        skill_catalog = context.metadata.get("skill_catalog", []) or []
        context.active_skill_names = [
            str(skill.get("name", "")).strip()
            for skill in skill_catalog
            if isinstance(skill, dict) and str(skill.get("name", "")).strip()
        ]
        system_prompt = self.prompt_builder.build(
            context=context,
            workdir=workdir,
            plan_context=str(plan_context),
            memory_sections=memory_sections,
            skill_catalog=skill_catalog,
        )
        messages.append({"role": "system", "content": system_prompt})

        reminder = self._build_system_reminder(context, planner)
        if reminder is not None:
            messages.append(reminder)

        messages.extend(context.history)

        return self.normalize_messages(messages)


class TurnReducer:
    """
    Reduces the turn context after execution.
    Updates memory, summarizes history, updates plans.
    """
    def __init__(self):
        self.memory_manager = None
        self.planner = None
        self.candidate_extractor = None
        
        # Try to import for Phase 4 stubs
        try:
            from bot.agent.plan.planner import Planner
            from bot.llm.async_client import AsyncLLMClient
            self.planner = Planner()
            self.candidate_extractor = AsyncLLMClient()
        except ImportError as e:
            log.debug("Components not available yet: {}", e)

    def _resolve_memory_manager(self, context: TurnContext):
        memory_manager = context.metadata.get("memory_manager") or getattr(self, "memory_manager", None)
        if memory_manager is not None:
            return memory_manager
        from bot.memory.manager import MemoryManager
        return MemoryManager(getattr(context, "app_id", "main"))

    def _latest_assistant_reply(self, history: list[dict[str, Any]]) -> str:
        for message in reversed(history):
            if message.get("role") == "assistant" and message.get("content"):
                return str(message.get("content", "")).strip()
        return ""

    def _recent_tool_results(self, history: list[dict[str, Any]], limit: int = 3) -> list[str]:
        results: list[str] = []
        for message in reversed(history):
            if message.get("role") == "tool" and message.get("content"):
                results.append(str(message.get("content", "")).strip())
                if len(results) >= limit:
                    break
        results.reverse()
        return results

    def _build_turn_summary(self, context: TurnContext) -> str:
        assistant_reply = self._latest_assistant_reply(context.history)
        tool_results = self._recent_tool_results(context.history)

        lines = [f"User request: {self._summarize_text(context.user_input.strip(), max_len=180)}"]
        if assistant_reply:
            lines.append(f"Assistant outcome: {self._summarize_text(assistant_reply, max_len=240)}")
        if tool_results:
            summarized_results = "; ".join(self._summarize_text(item, max_len=120) for item in tool_results)
            lines.append(f"Tool outcomes: {summarized_results}")
        if context.plan_state:
            lines.append(f"Plan state: {self._summarize_text(context.plan_state, max_len=180)}")

        return "\n".join(lines)

    def _summarize_text(self, text: str, max_len: int = 240) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) <= max_len:
            return normalized
        return normalized[: max_len - 3].rstrip() + "..."

    def _extract_preference_fact(self, user_input: str) -> str:
        patterns = [
            r"(?:我|请|以后)?(?:更)?偏好(.+)",
            r"(?:默认|以后)(.+)",
            r"(?:请)?总是(.+)",
            r"(?:请)?不要(.+)",
            r"remember\s+that\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                return self._summarize_text(match.group(1))
        return self._summarize_text(user_input)

    def _extract_correction_fact(self, user_input: str) -> str:
        patterns = [
            r"不是(.+?)而是(.+)",
            r"纠正一下[，,:：]?(.+)",
            r"更正[，,:：]?(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                if len(match.groups()) == 2:
                    wrong_part = self._summarize_text(match.group(1))
                    right_part = self._summarize_text(match.group(2))
                    return f"Correction: not {wrong_part}, but {right_part}"
                return self._summarize_text(match.group(1))
        return ""

    def _extract_rule_fact(self, turn_summary: str, user_input: str) -> str:
        candidate_source = user_input if len(user_input) >= 20 else turn_summary
        return self._summarize_text(candidate_source, max_len=320)

    def _build_memory_candidate(
            self,
            memory_type: MemoryType,
            category: str,
            fact: str,
            source_turn_id: str,
            reason: str,
            confidence: str,
    ) -> dict[str, Any]:
        content = "\n".join(
            [
                f"category: {category}",
                f"source_turn_id: {source_turn_id}",
                f"reason: {reason}",
                f"confidence: {confidence}",
                f"fact: {fact}",
            ]
        )
        return {
            "memory_type": memory_type,
            "category": category,
            "fact": fact,
            "reason": reason,
            "confidence": confidence,
            "content": content,
        }

    def _normalize_for_dedupe(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def _dedupe_memory_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (str(candidate["memory_type"]), candidate["content"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _extract_memory_candidates_rule_based(self, context: TurnContext, turn_summary: str) -> list[dict[str, Any]]:
        user_input = context.user_input.strip()
        lowered = user_input.lower()
        candidates: list[dict[str, Any]] = []

        preference_markers = ["prefer", "default", "always", "never", "以后", "默认", "偏好", "总是", "不要"]
        correction_markers = ["不是", "而是", "纠正", "更正", "correction", "actually"]
        rule_markers = ["规则", "约定", "规范", "必须", "记住", "remember", "policy", "workflow"]

        if any(marker in lowered or marker in user_input for marker in preference_markers):
            fact = self._extract_preference_fact(user_input)
            candidates.append(
                self._build_memory_candidate(
                    MemoryType.USER,
                    "preference",
                    fact,
                    context.turn_id,
                    "explicit preference statement",
                    "high",
                )
            )

        if any(marker in lowered or marker in user_input for marker in correction_markers):
            fact = self._extract_correction_fact(user_input)
            if fact:
                candidates.append(
                    self._build_memory_candidate(
                        MemoryType.USER,
                        "correction",
                        fact,
                        context.turn_id,
                        "explicit correction from user",
                        "high",
                    )
                )

        if any(marker in lowered or marker in user_input for marker in rule_markers):
            fact = self._extract_rule_fact(turn_summary, user_input)
            candidates.append(
                self._build_memory_candidate(
                    MemoryType.LONG,
                    "project-rule",
                    fact,
                    context.turn_id,
                    "explicit rule or convention statement",
                    "medium",
                )
            )

        return self._dedupe_memory_candidates(candidates)

    def _build_candidate_extraction_messages(self, context: TurnContext, turn_summary: str) -> list[dict[str, str]]:
        existing_memory_context = context.metadata.get("existing_memory_dedupe_context", "")
        assistant_reply = self._summarize_text(self._latest_assistant_reply(context.history), max_len=500)
        tool_results = [self._summarize_text(item, max_len=320) for item in self._recent_tool_results(context.history)]

        user_message_lines = [
            "Extract durable memory candidates from this turn.",
            "Only keep information that will still be useful in future turns.",
            "Return strict JSON only. No markdown, no prose, no code fences.",
            "",
            "Allowed memory_type values: user, long, identity.",
            "Allowed confidence values: low, medium, high.",
            "Allowed categories examples: preference, correction, project-rule, identity.",
            "",
            "Output schema:",
            '[{"memory_type":"user","category":"preference","fact":"...","reason":"...","confidence":"high"}]',
            "",
            "Rules:",
            "- Prefer 0 candidates over weak or speculative candidates.",
            "- Do not store transient requests, one-off tasks, or tool output noise.",
            "- If the user states a lasting preference, rule, correction, or identity fact, keep the distilled fact.",
            "- Keep fact concise and reusable.",
            "",
            f"Turn summary:\n{turn_summary}",
            f"User input:\n{context.user_input.strip()}",
        ]

        if assistant_reply:
            user_message_lines.append(f"Assistant reply:\n{assistant_reply}")
        if tool_results:
            user_message_lines.append("Recent tool results:")
            user_message_lines.extend(f"- {item}" for item in tool_results)
        if existing_memory_context:
            user_message_lines.extend(
                [
                    "",
                    "Existing relevant memories already stored. Avoid duplicating them unless this turn adds a new durable delta, correction, or refinement:",
                    existing_memory_context,
                ]
            )

        return [
            {
                "role": "system",
                "content": (
                    "You extract long-lived memory candidates for an agent memory system. "
                    "Output valid JSON only and be conservative."
                ),
            },
            {"role": "user", "content": "\n".join(user_message_lines)},
        ]

    def _parse_candidate_json(self, raw_response: str) -> Any:
        payload = raw_response.strip()
        if payload.startswith("```"):
            payload = re.sub(r"^```(?:json)?\s*", "", payload)
            payload = re.sub(r"\s*```$", "", payload)

        return json.loads(payload)

    async def _load_existing_memory_dedupe_context(self, context: TurnContext) -> tuple[str, int]:
        existing_memory_context = context.metadata.get("retrieved_memories", "")
        existing_memory_count = int(context.metadata.get("retrieved_memory_count", 0) or 0)
        if existing_memory_context:
            context.metadata["existing_memory_dedupe_context"] = existing_memory_context
            return existing_memory_context, existing_memory_count

        memory_manager = self._resolve_memory_manager(context)
        if memory_manager and hasattr(memory_manager, "search_for_prompt") and context.user_input:
            try:
                recall_payload = await memory_manager.search_for_prompt(context.user_input, limit=5, max_chars=1500)
                existing_memory_context = recall_payload.get("text", "")
                existing_memory_count = int(recall_payload.get("count", 0) or 0)
            except Exception as exc:
                log.warning("Failed to load existing memory dedupe context: {}", exc)
                existing_memory_context = ""
                existing_memory_count = 0

        context.metadata["existing_memory_dedupe_context"] = existing_memory_context
        context.metadata["existing_memory_dedupe_count"] = existing_memory_count
        return existing_memory_context, existing_memory_count

    def _filter_existing_memory_duplicates(
        self,
        candidates: list[dict[str, Any]],
        existing_memory_context: str,
    ) -> tuple[list[dict[str, Any]], int]:
        normalized_existing = self._normalize_for_dedupe(existing_memory_context)
        if not normalized_existing:
            return candidates, 0

        filtered: list[dict[str, Any]] = []
        duplicates_filtered = 0
        for candidate in candidates:
            fact = self._normalize_for_dedupe(str(candidate.get("fact", "")))
            if len(fact) >= 16 and fact in normalized_existing:
                duplicates_filtered += 1
                continue
            filtered.append(candidate)

        return filtered, duplicates_filtered

    def _record_candidate_extraction_audit(
        self,
        context: TurnContext,
        candidates: list[dict[str, Any]],
        duplicates_filtered: int,
        existing_memory_count: int,
    ) -> None:
        current_meta = dict(context.metadata.get("memory_candidate_extraction", {}))
        current_meta.update(
            {
                "turn_id": context.turn_id,
                "duplicates_filtered": duplicates_filtered,
                "existing_memory_count": existing_memory_count,
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        "memory_type": str(candidate.get("memory_type", "")),
                        "category": candidate.get("category", ""),
                        "fact": candidate.get("fact", ""),
                        "confidence": candidate.get("confidence", ""),
                    }
                    for candidate in candidates
                ],
            }
        )
        context.metadata["memory_candidate_extraction"] = current_meta
        context.metadata.setdefault("memory_candidate_extractions", []).append(current_meta)

    def _normalize_llm_candidate(self, raw_candidate: dict[str, Any], source_turn_id: str) -> dict[str, Any] | None:
        try:
            memory_type = MemoryType(str(raw_candidate.get("memory_type", "")).strip().lower())
        except ValueError:
            return None

        category = self._summarize_text(str(raw_candidate.get("category", "")).strip().lower(), max_len=64)
        fact = self._summarize_text(str(raw_candidate.get("fact", "")).strip(), max_len=320)
        reason = self._summarize_text(str(raw_candidate.get("reason", "")).strip(), max_len=160)
        confidence = str(raw_candidate.get("confidence", "medium")).strip().lower()

        if not category or not fact or not reason:
            return None
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"

        return self._build_memory_candidate(
            memory_type=memory_type,
            category=category,
            fact=fact,
            source_turn_id=source_turn_id,
            reason=reason,
            confidence=confidence,
        )

    async def _extract_memory_candidates_with_llm(
        self,
        context: TurnContext,
        turn_summary: str,
    ) -> list[dict[str, Any]] | None:
        extractor = context.metadata.get("candidate_extractor") or getattr(self, "candidate_extractor", None)
        if extractor is None:
            return None

        try:
            existing_memory_context, existing_memory_count = await self._load_existing_memory_dedupe_context(context)
            messages = self._build_candidate_extraction_messages(context, turn_summary)
            raw_response = await extractor.invoke(messages=messages, max_tokens=900, temperature=0.0)
            parsed = self._parse_candidate_json(raw_response)
            raw_candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else parsed
            if not isinstance(raw_candidates, list):
                raise ValueError("memory candidate response is not a list")

            normalized = []
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                candidate = self._normalize_llm_candidate(item, context.turn_id)
                if candidate is not None:
                    normalized.append(candidate)

            filtered_candidates, duplicates_filtered = self._filter_existing_memory_duplicates(
                normalized,
                existing_memory_context,
            )

            context.metadata["memory_candidate_extraction"] = {
                "mode": "llm",
                "raw_count": len(raw_candidates),
                "accepted_count": len(filtered_candidates),
                "duplicates_filtered": duplicates_filtered,
                "existing_memory_count": existing_memory_count,
            }
            return self._dedupe_memory_candidates(filtered_candidates)
        except Exception as exc:
            log.warning("LLM memory candidate extraction failed, falling back to rules: {}", exc)
            return None

    async def _extract_memory_candidates(self, context: TurnContext, turn_summary: str) -> list[dict[str, Any]]:
        llm_candidates = await self._extract_memory_candidates_with_llm(context, turn_summary)
        if llm_candidates is not None:
            return llm_candidates

        _, existing_memory_count = await self._load_existing_memory_dedupe_context(context)
        fallback_candidates = self._extract_memory_candidates_rule_based(context, turn_summary)
        filtered_candidates, duplicates_filtered = self._filter_existing_memory_duplicates(
            fallback_candidates,
            context.metadata.get("existing_memory_dedupe_context", ""),
        )
        context.metadata["memory_candidate_extraction"] = {
            "mode": "rule-fallback",
            "accepted_count": len(filtered_candidates),
            "duplicates_filtered": duplicates_filtered,
            "existing_memory_count": existing_memory_count,
        }
        return filtered_candidates

    async def reduce(self, context: TurnContext) -> None:
        """
        Post-turn updates.
        - Summarize too-long histories.
        - Update Planner progress.
        - Store new facts into memory.
        """
        memory_manager = self._resolve_memory_manager(context)
        planner = context.metadata.get("planner") or self.planner

        turn_summary = self._build_turn_summary(context)
        context.metadata["turn_summary"] = turn_summary
        context.metadata.setdefault("turn_summaries", []).append(turn_summary)
        if len(context.metadata["turn_summaries"]) > 20:
            context.metadata["turn_summaries"] = context.metadata["turn_summaries"][-20:]

        candidates = await self._extract_memory_candidates(context, turn_summary)
        extraction_meta = context.metadata.get("memory_candidate_extraction", {})
        self._record_candidate_extraction_audit(
            context,
            candidates,
            int(extraction_meta.get("duplicates_filtered", 0) or 0),
            int(extraction_meta.get("existing_memory_count", 0) or 0),
        )
        if candidates:
            existing = context.metadata.get("memory_candidates", [])
            context.metadata["memory_candidates"] = existing + candidates

        if planner:
            try:
                context.plan_state = planner.get_state()
            except Exception as e:
                log.error("Error updating planner state: {}", e)
        
        log.info("Turn reduced for session {}, turn {}", context.session_id, context.turn_id)
