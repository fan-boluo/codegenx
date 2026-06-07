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

from compact import should_auto_compact
from shared.config.log_config import log
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
    "edit_file",
    "write_file",
    "echo",
})

# Tools whose inputs (arguments) may also be cleared when their results are cleared.
# Typically large-output tools like write_file where the full content is in the
# arguments and the result is a short "Successfully wrote..." message.
# Clearing the arguments prevents context bloat from stale full-file payloads.
CLEAR_TOOL_INPUTS = frozenset({
    "write_file",
})

# Token threshold above which a single tool result gets cleared.
MAX_TOOL_RESULT_TOKENS = 2000


from compact.thresholds import AUTOCOMPACT_THRESHOLD, estimate_tokens


# ── Token estimation ──────────────────────────────────────────────────────────
def _rough_tokens(text: str) -> int:
    return estimate_tokens([{"content": text}])


# ── Core micro-compaction ──────────────────────────────────────────────────────

def microcompact_messages(
    messages: list[dict],
    protect_last_n_results: int = 1,
    max_result_tokens: int | None = None,
    min_total_tokens: int | None = None,
) -> list[dict]:
    """
    将工具执行结果超长的压缩
    Return a shallow-copied message list with oversized tool results cleared.

    Messages are expected in the normalized OpenAI-compatible schema:
      - tool:      {"role":"tool", "content":"<str>", "tool_call_id":"<str>", "name":"<str>"}
      - assistant: {"role":"assistant", "content":"<str>",
                     "tool_calls":[{"id":"...", "function":{"name":"..."}}]}

    protect_last_n_results: 保护最近 N 次的工具调用结果不被清除，保证大模型
                            在后续 turn 中能看到刚刚执行的结果。默认 1。
    max_result_tokens: 工具结果 token 阈值，超过则清除。不传则使用 MAX_TOOL_RESULT_TOKENS。
    min_total_tokens: 消息整体 token 低于此值时完全跳过压缩。默认取压缩触发阈值的 60%，
                      确保上下文空间充裕时不会丢失信息。

    The function:
      1. Builds an index: tool_call_id → msg_index for every tool message.
      2. For every assistant tool_call whose function.name is in COMPACTABLE_TOOLS,
         checks the corresponding tool message content length.
      3. If above MAX_TOOL_RESULT_TOKENS, replaces the tool message's content
         with CLEARED_MARKER.
      4. Additionally, for tools in CLEAR_TOOL_INPUTS, the assistant message's
         tool_call arguments are replaced with a summary marker to prevent
         large stale payloads (e.g. full notebook JSON in write_file) from
         accumulating across turns.

    Returns a new list; the input is never mutated.
    """

    # 单个执行的结果，现在这个数字是3000,来自配置文件
    threshold = max_result_tokens if max_result_tokens is not None else MAX_TOOL_RESULT_TOKENS
    min_total = min_total_tokens if min_total_tokens is not None else int(AUTOCOMPACT_THRESHOLD * 0.6)
    log.debug("micro compact total messages shreshold: {} ,single reuslt shreshold",min_total,threshold)
    # 上下文还很宽松，无需清理，保留所有信息
    if estimate_tokens(messages) < min_total:
        return messages
    log.debug("micro compact 超过上下文压缩窗口阈值")
    # Build tool_call_id → msg_index map (one result per tool message)
    result_index: dict[str, int] = {}
    for msg_i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id", "")
        if tc_id:
            result_index[tc_id] = msg_i

    # Identify which tool_call_ids should be cleared (results and/or inputs)
    # Collect all assistant tool_call IDs in order
    ordered_tool_call_ids: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            uid = tc.get("id", "")
            if uid:
                ordered_tool_call_ids.append(uid)

    # 最近 N 个 tool_call_id 受到保护 ，这个数字是5
    protected_ids: set[str] = set()
    if protect_last_n_results > 0 and ordered_tool_call_ids:
        protected_ids = set(ordered_tool_call_ids[-protect_last_n_results:])

    # Identify which tool_call_ids should be cleared
    to_clear: set[str] = set()
    to_clear_inputs: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            name = tc.get("function", {}).get("name", "")
            uid = tc.get("id", "")
            if uid in protected_ids:
                continue
            if name not in COMPACTABLE_TOOLS or not uid:
                continue
            msg_i = result_index.get(uid)
            if msg_i is None:
                continue
            result_content = messages[msg_i].get("content", "")
            if isinstance(result_content, str) and _rough_tokens(result_content) > threshold:
                to_clear.add(uid)
            # Also mark for input clearing if tool is in CLEAR_TOOL_INPUTS
            if name in CLEAR_TOOL_INPUTS:
                to_clear_inputs.add(uid)

    if not to_clear and not to_clear_inputs:
        return messages  # nothing to do; return original reference
    log.debug("micro compact has tool result to clear:{},{}",to_clear,to_clear_inputs)
    # Build new list, cloning only mutated messages
    new_messages: list[dict] = []
    for msg_i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id in to_clear:
                new_messages.append({**msg, "content": CLEARED_MARKER})
            else:
                new_messages.append(msg)
        elif msg.get("role") == "assistant":
            tcs = msg.get("tool_calls")
            if not tcs or not to_clear_inputs:
                new_messages.append(msg)
                continue
            new_tcs = []
            for tc in tcs:
                uid = tc.get("id", "")
                name = tc.get("function", {}).get("name", "")
                if uid in to_clear_inputs:
                    new_tc = {**tc}
                    f = {**new_tc.get("function", {})}
                    # 用合法 JSON 替代原 arguments，保留 path 信息，省略实际写入内容
                    import json as _json
                    try:
                        orig_args = _json.loads(f.get("arguments", "{}"))
                    except Exception:
                        orig_args = {}
                    file_path = orig_args.get("path", orig_args.get("file_path", ""))
                    f["arguments"] = _json.dumps({
                        "path": file_path,
                        "note": "Content successfully written, omitted for brevity",
                    }, ensure_ascii=False)
                    new_tc["function"] = f
                    new_tcs.append(new_tc)
                    log.debug("micro compact clear_inputs: {}",f["arguments"])

                else:
                    new_tcs.append(tc)
            new_messages.append({**msg, "tool_calls": new_tcs})
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
        if msg.get("role") == "tool" and msg.get("content") == CLEARED_MARKER
    )
    return {"cleared_results": cleared, "message_count": len(after)}
