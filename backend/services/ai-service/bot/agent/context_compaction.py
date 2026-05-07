from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from shared.constants import BOT_WORKSPACE_DIR, get_bot_context_dir
from bot.utils.log_utils import log
from constant import (
    CONTEXT_LIMIT,
    KEEP_RECENT_TOOL_RESULTS,
    PERSIST_THRESHOLD,
    PREVIEW_CHARS,
)


class ContextCompactor:
    def _get_state(self, context: Any) -> dict[str, Any]:
        state = context.metadata.get("context_compaction")
        if isinstance(state, dict):
            state.setdefault("has_compacted", False)
            state.setdefault("last_summary", "")
            state.setdefault("recent_files", [])
            state.setdefault("events", [])
            state.setdefault("last_transcript_hash", "")
            state.setdefault("last_transcript_path", "")
            return state

        state = {
            "has_compacted": False,
            "last_summary": "",
            "recent_files": [],
            "events": [],
            "last_transcript_hash": "",
            "last_transcript_path": "",
        }
        context.metadata["context_compaction"] = state
        return state

    def _app_agent_dir(self, context: Any) -> Path:
        app_id = getattr(context, "app_id", "main") or "main"
        return get_bot_context_dir(app_id)

    def _track_recent_file(self, context: Any, path: Path) -> None:
        state = self._get_state(context)
        recent_files = state.setdefault("recent_files", [])
        path_str = str(path)
        if path_str in recent_files:
            recent_files.remove(path_str)
        recent_files.append(path_str)
        if len(recent_files) > 5:
            del recent_files[:-5]

    def estimate_context_size(self, messages: list[dict[str, Any]]) -> int:
        return len(json.dumps(messages, ensure_ascii=False, default=str))

    def _serialize_history(self, context: Any) -> str:
        return json.dumps(self._build_transcript_snapshot(context), ensure_ascii=False, default=str, sort_keys=True)

    def _build_transcript_snapshot(self, context: Any) -> dict[str, Any]:
        metadata = {}
        for key, value in dict(getattr(context, "metadata", {})).items():
            if key in {"planner", "session_manager"}:
                continue
            if key == "context_compaction" and isinstance(value, dict):
                stable_state = dict(value)
                stable_state.pop("last_transcript_hash", None)
                stable_state.pop("last_transcript_path", None)
                stable_state.pop("recent_files", None)
                stable_state.pop("events", None)
                metadata[key] = stable_state
                continue
            metadata[key] = value

        return {
            "app_id": getattr(context, "app_id", "main"),
            "session_id": getattr(context, "session_id", ""),
            "turn_id": getattr(context, "turn_id", ""),
            "user_input": getattr(context, "user_input", ""),
            "state": getattr(getattr(context, "state", None), "value", str(getattr(context, "state", ""))),
            "metadata": metadata,
            "history": list(getattr(context, "history", [])),
        }

    def persist_large_output(self, context: Any, tool_use_id: str, output: str) -> str:
        if len(output) <= PERSIST_THRESHOLD:
            return output

        tool_results_dir = self._app_agent_dir(context) / "tool_results"
        tool_results_dir.mkdir(parents=True, exist_ok=True)
        stored_path = tool_results_dir / f"{tool_use_id}.txt"
        if not stored_path.exists():
            stored_path.write_text(output, encoding="utf-8")
        self._track_recent_file(context, stored_path)

        try:
            rel_path = stored_path.relative_to(BOT_WORKSPACE_DIR)
        except ValueError:
            rel_path = stored_path

        preview = output[:PREVIEW_CHARS]
        return (
            "<persisted-output>\n"
            f"Full output saved to: {rel_path}\n"
            "Preview:\n"
            f"{preview}\n"
            "</persisted-output>"
        )

    def _collect_tool_messages(self, context: Any) -> list[tuple[int, dict[str, Any]]]:
        results: list[tuple[int, dict[str, Any]]] = []
        for index, message in enumerate(context.history):
            if message.get("role") == "tool":
                results.append((index, message))
        return results

    def micro_compact(self, context: Any) -> bool:
        tool_messages = self._collect_tool_messages(context)
        if len(tool_messages) <= KEEP_RECENT_TOOL_RESULTS:
            return False

        changed = False
        for _, message in tool_messages[:-KEEP_RECENT_TOOL_RESULTS]:
            content = message.get("content", "")
            if not isinstance(content, str) or len(content) <= 120:
                continue
            if "<persisted-output>" in content or "<compacted-tool-result>" in content:
                continue

            preview = content[:200]
            message["content"] = (
                "<compacted-tool-result>\n"
                "Preview:\n"
                f"{preview}...\n\n"
                "[Earlier tool result compacted to save context. Re-run if full detail is needed.]\n"
                "</compacted-tool-result>"
            )
            changed = True

        if changed:
            self._get_state(context)["events"].append(
                {
                    "reason": "micro-pre-llm",
                    "history_size": self.estimate_context_size(context.history),
                    "timestamp": time.time(),
                }
            )
        return changed

    def _write_transcript(self, context: Any) -> Path:
        state = self._get_state(context)
        serialized_history = self._serialize_history(context)
        transcript_hash = hashlib.sha256(serialized_history.encode("utf-8")).hexdigest()
        existing_path = state.get("last_transcript_path", "")
        if transcript_hash == state.get("last_transcript_hash") and existing_path:
            existing_transcript = Path(existing_path)
            if existing_transcript.exists():
                self._track_recent_file(context, existing_transcript)
                return existing_transcript

        transcripts_dir = self._app_agent_dir(context) / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcripts_dir / f"transcript_{int(time.time() * 1000)}.jsonl"
        with transcript_path.open("w", encoding="utf-8") as handle:
            handle.write(serialized_history + "\n")
        state["last_transcript_hash"] = transcript_hash
        state["last_transcript_path"] = str(transcript_path)
        self._track_recent_file(context, transcript_path)
        return transcript_path

    def _heuristic_summary(self, context: Any, focus: str | None = None) -> str:
        recent_messages = context.history[-8:]
        summary_lines = [
            f"Current goal: {getattr(context, 'user_input', '').strip()}",
        ]

        turn_summary = context.metadata.get("turn_summary")
        if turn_summary:
            summary_lines.append(f"Latest turn summary: {turn_summary}")

        if focus:
            summary_lines.append(f"Focus: {focus}")

        if getattr(context, "plan_state", ""):
            summary_lines.append(f"Plan state: {context.plan_state}")

        for message in recent_messages:
            role = message.get("role", "unknown")
            content = str(message.get("content", "")).strip()
            if content:
                summary_lines.append(f"{role}: {content[:400]}")

        return "\n".join(summary_lines)

    async def summarize_history(self, context: Any, focus: str | None = None) -> str:
        prompt = (
            "Summarize this coding-agent conversation so work can continue.\n"
            "Preserve:\n"
            "1. The current goal\n"
            "2. Important findings and decisions\n"
            "3. Files read or changed\n"
            "4. Remaining work\n"
            "5. User constraints and preferences\n"
            "Be compact but concrete.\n\n"
        )
        if focus:
            prompt += f"Focus to preserve: {focus}\n\n"

        conversation = json.dumps(context.history, ensure_ascii=False, default=str)[:80000]
        prompt += conversation

        try:
            from bot.llm.async_client import AsyncLLMClient

            client = AsyncLLMClient()
            response = await client.invoke(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.0,
            )
            response_text = (response or "").strip()
            if response_text:
                return response_text
        except Exception as exc:
            log.warning(f"Context summarization failed, using heuristic summary: {exc}")

        return self._heuristic_summary(context, focus=focus)

    async def compact_history(self, context: Any, focus: str | None = None, reason: str = "threshold") -> None:
        state = self._get_state(context)
        history_size_before = self.estimate_context_size(context.history)
        transcript_path = self._write_transcript(context)
        summary = await self.summarize_history(context, focus=focus)

        parts = [
            "This session continues from a previous conversation that was compacted.",
            f"Summary of prior context:\n\n{summary}",
        ]
        if focus:
            parts.append(f"Focus to preserve next: {focus}")

        recent_files = state.get("recent_files", [])
        if recent_files:
            recent_lines = "\n".join(f"- {path}" for path in recent_files[-5:])
            parts.append(f"Recent files to reopen if needed:\n{recent_lines}")

        parts.append("Continue from where we left off without re-asking the user.")
        continuation = "\n\n".join(parts)

        context.history = [{"role": "user", "content": continuation}]
        state["has_compacted"] = True
        state["last_summary"] = continuation
        state.setdefault("events", []).append(
            {
                "reason": reason,
                "history_size_before": history_size_before,
                "history_size_after": self.estimate_context_size(context.history),
                "timestamp": time.time(),
                "transcript_path": str(transcript_path),
            }
        )
        context.metadata["context_compaction"] = state

    async def prepare_for_llm(self, context: Any) -> None:
        self.micro_compact(context)
        current_size = self.estimate_context_size(context.history)
        context.metadata["context_size_before_llm"] = current_size
        if current_size > CONTEXT_LIMIT:
            await self.compact_history(
                context,
                focus="Reduce context before the next model call while preserving current coding task state.",
                reason="threshold-pre-llm",
            )

    async def finalize_turn(self, context: Any) -> None:
        self.micro_compact(context)
        focus = context.metadata.get("turn_summary") or getattr(context, "user_input", "")
        current_size = self.estimate_context_size(context.history)
        context.metadata["context_size_after_turn"] = current_size
        if current_size > CONTEXT_LIMIT:
            await self.compact_history(context, focus=focus, reason="final-post-turn")