from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


def _bootstrap_paths() -> Path:
	test_file = Path(__file__).resolve()
	ai_service_root = test_file.parents[1]
	backend_root = ai_service_root.parents[1]
	for path in (ai_service_root, backend_root):
		normalized = str(path)
		if normalized not in sys.path:
			sys.path.insert(0, normalized)
	return ai_service_root


AI_SERVICE_ROOT = _bootstrap_paths()

from bot.agent.tool_executor import ToolExecutor
from bot.agent.tool_handler import ToolRegistry


@dataclass
class TestToolContext:
	app_id: str
	user_id: str
	session_id: str
	turn_id: str
	metadata: dict[str, Any] = field(default_factory=dict)
	plan_state: str = ""


def _build_context(app_id: str, user_id: str) -> TestToolContext:
	return TestToolContext(
		app_id=app_id,
		user_id=user_id,
		session_id=f"tool-test-session-{uuid4().hex[:8]}",
		turn_id=f"tool-test-turn-{uuid4().hex[:8]}",
		metadata={"user_id": user_id},
	)


async def run_memory_tool_test(app_id: str = "main", user_id: str | None = None) -> None:
	resolved_user_id = user_id or f"tool-test-user-{uuid4().hex[:8]}"
	tool_executor = ToolExecutor(ToolRegistry(), safe_paths=[str(AI_SERVICE_ROOT)])
	context = _build_context(app_id=app_id, user_id=resolved_user_id)

	marker = f"tool-test-marker-{uuid4().hex}"
	content = f"{marker} {marker} memory write and search integration test"

	write_result = await tool_executor.execute(
		{
			"name": "write_short_term",
			"arguments": {
				"content": content,
				"memory_type": "fact",
				"importance": 0.95,
			},
		},
		context,
	)
	print("write_short_term =>", write_result)

	if not write_result.get("success"):
		raise RuntimeError(f"write_short_term failed: {write_result}")

	search_result = await tool_executor.execute(
		{
			"name": "memory_search",
			"arguments": {
				"query": marker,
				"top_k": 5,
				"score_threshold": 0.65,
			},
		},
		context,
	)
	print("memory_search =>", search_result)

	if not search_result.get("success"):
		raise RuntimeError(f"memory_search failed: {search_result}")

	results = ((search_result.get("details") or {}).get("results") or [])
	if not any(marker in str(item.get("text", "")) for item in results):
		raise AssertionError(f"search results do not contain marker {marker}: {results}")

	print("user_id =>", resolved_user_id)
	print("memory tool test passed")


if __name__ == "__main__":
	asyncio.run(run_memory_tool_test())
