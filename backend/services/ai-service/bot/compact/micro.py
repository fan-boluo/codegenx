"""
Micro-compaction — clear oversized tool results before each LLM call.

Mirrors src/services/compact/microCompact.ts (the legacy / non-cached path):

  Walk every assistant message; for each tool_use block whose name is in
  COMPACTABLE_TOOLS, check the corresponding tool_result in the following
  user message. If that result's token count exceeds MAX_TOOL_RESULT_TOKENS,
  replace its content with the cleared marker string.

This keeps individual tool results from blowing up the context window — e.g.
a long file read or grep output that the model already acted on.

The production implementation has an additional "cached microcompact" path
that uses Anthropic's cache-editing API to avoid invalidating the prompt
cache. We don't have that here; implement it when you add a real Anthropic
client.

Key difference from full compaction:
  - No LLM call required (pure message mutation)
  - Runs on EVERY query loop iteration, before autocompact check
  - Only clears individual results; does not trim message count
"""
from __future__ import annotations

# ── Configuration ─────────────────────────────────────────────────────────────

# Marker written in place of cleared tool results.
# Mirrors TIME_BASED_MC_CLEARED_MESSAGE in microCompact.ts.
CLEARED_MARKER = "[Old tool result content cleared]"

# Tools whose results may be cleared (file content, shell output, search results).
# Extend as you add more tools.
COMPACTABLE_TOOLS = frozenset({
    "read_file",
    "bash",
    "shell",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
    "edit_file",
    "write_file",
    "calculator",   # example tool from mock LLM
    "echo",
})

# Token threshold above which a single tool result gets cleared.
# Production uses time-based clearing (after cache expiry) plus token pressure.
# Here we use a simple per-result token cap.
MAX_TOOL_RESULT_TOKENS = 200   # scale up for real deployment


# ── Token estimation (reuse from thresholds to avoid circular import) ─────────

def _rough_tokens(text: str) -> int:
    return len(text) // 4


# ── Core micro-compaction ──────────────────────────────────────────────────────

def microcompact_messages(messages: list[dict]) -> list[dict]:
    """
    Return a shallow-copied message list with oversized tool results cleared.

    Mirrors the legacy microcompactMessages() path (non-cached).

    The function:
      1. Builds an index: tool_use_id → position of the tool_result in messages.
      2. For every assistant tool_use whose name is in COMPACTABLE_TOOLS,
         checks the corresponding tool_result token count.
      3. If above MAX_TOOL_RESULT_TOKENS, replaces the result content with
         CLEARED_MARKER.

    Returns a new list; the input is never mutated.
    """
    # Build tool_use_id → (msg_index, result_index_within_content) map
    result_index: dict[str, tuple[int, int]] = {}
    for msg_i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for res_i, item in enumerate(content):
                uid = item.get("tool_use_id", "")
                if uid:
                    result_index[uid] = (msg_i, res_i)

    # Identify which tool_use_ids should be cleared
    to_clear: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            name = tc.get("name", "")
            uid = tc.get("id", "")
            if name not in COMPACTABLE_TOOLS or not uid:
                continue
            loc = result_index.get(uid)
            if loc is None:
                continue
            msg_i, res_i = loc
            result_content = (
                messages[msg_i].get("content", [])[res_i].get("content", "")
            )
            if isinstance(result_content, str) and _rough_tokens(result_content) > MAX_TOOL_RESULT_TOKENS:
                to_clear.add(uid)

    if not to_clear:
        return messages  # nothing to do; return original reference

    # Build new list, cloning only mutated messages
    new_messages: list[dict] = []
    for msg_i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            new_messages.append(msg)
            continue

        content = msg.get("content", [])
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        new_content = list(content)
        mutated = False
        for res_i, item in enumerate(new_content):
            uid = item.get("tool_use_id", "")
            if uid in to_clear:
                new_content[res_i] = {**item, "content": CLEARED_MARKER}
                mutated = True

        if mutated:
            new_messages.append({**msg, "content": new_content})
        else:
            new_messages.append(msg)

    return new_messages


def microcompact_stats(
    before: list[dict], after: list[dict]
) -> dict:
    """Return a summary dict for logging/debug."""
    cleared = sum(
        1
        for msg in after
        if msg.get("role") == "tool"
        for item in (msg.get("content") or [])
        if isinstance(item, dict) and item.get("content") == CLEARED_MARKER
    )
    return {"cleared_results": cleared, "message_count": len(after)}
