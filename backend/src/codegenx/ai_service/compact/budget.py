"""
统一预算分配器（上下文工程排期③）——模块级 token 管控。

设计（docs/上下文工程设计.md §1.1）：
  总预算 = 模型窗口 × 预算比例（预留输出 max_tokens 与安全余量）；
  分区软硬限 + 固定驱逐优先级：工具结果原文 → 中段历史 → 记忆注入 → 会话摘要；
  驱逐不是删除，而是降级为占位符——工具结果可重新调用回捞、历史原文在
  chat_messages 快照与会话记录中可回捞。

原语（CompactionService.token_budget 统一编排，驱逐级联）：
  1. enforce_budget_inplace —— 消息占位降级（就地改真相源：工具结果 → 中段历史）
  2. compress_tool_catalog + schema_tokens —— tools schema 瘦身（只截描述，不删工具；
     以 tool_schema 分区排在 SECTION_EVICTION_ORDER 最后，不到必要不降级）
  3. 消息摘要 / 分区驱逐 / 硬截断由 token_budget 编排兜底

边界（单一职责）：
  本模块只提供纯函数原语，不持有会话状态；system 分区驱逐基于
  PromptConfig.resolve_fields() 的结果，不解析任意 prompt 文本含义。
"""

import json
from dataclasses import dataclass

from codegenx.ai_service.compact.thresholds import estimate_tokens
from shared import log
# ── 占位文案（尽量短，驱逐本身也要省 tokens） ────────────────────────────────
TOOL_PLACEHOLDER = "[工具 {name} 的结果已因上下文预算移出，需要时请重新调用该工具]"
HISTORY_PLACEHOLDER = "[该历史消息已因上下文预算移出，原文见会话记录]"
MEMORY_EVICTED_PLACEHOLDER = "（长期记忆已因上下文预算临时移出）"
SUMMARY_EVICTED_PLACEHOLDER = "（历史会话摘要已因上下文预算临时移出）"


@dataclass
class BudgetReport:
    """单轮预算执行报告（日志观测用）。"""

    budget: int
    tokens_before: int
    tokens_after: int
    evicted_tools: int = 0
    evicted_histories: int = 0

    @property
    def ok(self) -> bool:
        return self.tokens_after <= self.budget


@dataclass
class SectionReport:
    """system 分区预算结果（token_budget 日志逐项输出）。"""

    name: str
    before: int
    after: int
    truncated: bool = False
    evicted: bool = False


def enforce_budget_inplace(
    messages: list[dict],
    *,
    budget: int,
    protect_last_n_tool_results: int = 5,
) -> BudgetReport:
    """
    历史消息驱逐
    就地执行预算：超限按驱逐优先级降级消息内容，原列表原地修改并返回报告。

    分区语义（工作列表形态，system 不在列表内、由 context.system_prompt 承载）：
      [*history]        —— 最后一条 user 之前的 assistant 文本消息（中段历史，
                           驱逐优先级 2，最旧优先；user 提问承载任务上下文，永不驱逐）；
      [user(task)]      —— 最后一条 user = 当前任务输入，永不驱逐；
      [assistant/tool]  —— ReAct 循环区，tool 消息为工具结果（驱逐优先级 1，
                           保护最近 N 条不被驱逐）。
    幂等：已是占位符的消息跳过（system 分区驱逐后复量时不会虚增计数）。
    """
    tokens_before = estimate_tokens(messages)
    report = BudgetReport(budget=budget, tokens_before=tokens_before, tokens_after=tokens_before)
    if tokens_before <= budget:
        return report

    # 分区：最后一条 user 之前 = 中段历史（只降级 assistant 文本，user 提问永不驱逐）；
    # tool 消息全列表收集（均在循环区）
    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    history_idx = [
        i for i in range(0, last_user_idx)
        if messages[i].get("role") == "assistant" and messages[i].get("content")
    ]
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]

    # 优先级 1：工具结果原文降级（保护最近 N 条；原文可经重新调用工具回捞）
    evictable_tools = (
        tool_idx[:-protect_last_n_tool_results] if protect_last_n_tool_results > 0 else tool_idx
    )
    for i in evictable_tools:
        if report.ok:
            break
        m = messages[i]
        placeholder = TOOL_PLACEHOLDER.format(name=str(m.get("name") or "tool"))
        if m.get("content") == placeholder:
            continue  # 已降级过（幂等）
        m["content"] = placeholder
        report.evicted_tools += 1
        report.tokens_after = estimate_tokens(messages)

    # 优先级 2：中段历史降级（最旧优先；原文在 chat_messages 快照可回捞）
    for i in history_idx:
        if report.ok:
            break
        m = messages[i]
        if m.get("content") == HISTORY_PLACEHOLDER:
            continue  # 已降级过（幂等）
        m["content"] = HISTORY_PLACEHOLDER
        report.evicted_histories += 1
        report.tokens_after = estimate_tokens(messages)

    return report


# ── system 分区驱逐 ───────────────────────────────────────────────────────────

# 分区驱逐优先级：排在越前面越先被驱逐（skill/memory 可用工具重新捞回，最先走；
# resource/expand/taskbord 等任务地基类信息后走；tool_schema 最后——工具描述是
# function calling 契约，agent 靠调用工具完成任务，不到必要不修改）
SECTION_EVICTION_ORDER = (
    "skill_prompt",
    "memory_prompt",
    "resource_prompt",
    "expand_prompt",
    "taskbord_prompt",
    "examples",
    "tool_schema"
)

# 分区驱逐占位文案：清空分区时在原位留一句短提示，让模型感知该信息被临时移出、
# 需要时可尝试经相应工具重新加载（占位要短，驱逐本身也要省 tokens）
SECTION_LABELS = {
    "skill_prompt": "技能说明",
    "memory_prompt": "长期记忆",
    "resource_prompt": "资源说明",
    "expand_prompt": "扩展上下文",
    "taskbord_prompt": "任务看板",
    "examples": "示例",
}


def section_evicted_placeholder(name: str) -> str:
    """分区驱逐后在原位留下的提示文案（含分区名，模型可感知）。"""
    label = SECTION_LABELS.get(name, name)
    return f"（{label}已因上下文预算临时移出，需要时可通过相应工具重新加载）"


# ── tools schema 预算 ────────────────────────────────────────────────────────


def schema_tokens(tools: list[dict]) -> int:
    """tools schema 的 token 估算（function calling 参数不计入 messages）。"""
    if not tools:
        return 0
    return estimate_tokens([{"content": json.dumps(tools, ensure_ascii=False)}])


def _strip_param_descriptions(schema):
    """递归移除 JSON Schema 里的 description（保留 name/type/enum/required）。"""
    if isinstance(schema, dict):
        return {
            k: _strip_param_descriptions(v)
            for k, v in schema.items()
            if k != "description"
        }
    if isinstance(schema, list):
        return [_strip_param_descriptions(item) for item in schema]
    return schema


def _truncate_descriptions(tools: list[dict], limit: int, *, strip_params: bool) -> list[dict]:
    """复制并截断工具描述；工具本身与参数结构保持完整。"""
    result: list[dict] = []
    for tool in tools:
        fn = dict(tool.get("function") or {})
        desc = str(fn.get("description") or "")
        if limit > 0 and len(desc) > limit:
            fn["description"] = desc[:limit].rstrip() + "…"
        if strip_params and isinstance(fn.get("parameters"), dict):
            fn["parameters"] = _strip_param_descriptions(fn["parameters"])
        result.append({**tool, "function": fn})
    return result


def compress_tool_catalog(
    tools: list[dict],
    *,
    budget: int,
    description_max_chars: int = 240,
) -> tuple[list[dict], int, int]:
    """tools schema 瘦身：先截描述，再剥离参数描述；工具一个不删。

    返回（瘦身后目录, before tokens, after tokens）。schema 是 function
    calling 契约，删工具会让模型能力静默退化，故只做描述层降级。
    """
    if not tools:
        return [], 0, 0
    before = schema_tokens(tools)
    if budget <= 0 or before <= budget:
        return tools, before, before

    # 第 1 级：截断工具描述
    compressed = _truncate_descriptions(tools, description_max_chars, strip_params=False)
    after = schema_tokens(compressed)
    if after <= budget:
        return compressed, before, after

    # 第 2 级：剥离参数级 description
    compressed = _truncate_descriptions(compressed, description_max_chars, strip_params=True)
    after = schema_tokens(compressed)
    if after <= budget:
        return compressed, before, after

    # 第 3 级：描述压到首句长度（仍不删工具）
    compressed = _truncate_descriptions(compressed, 80, strip_params=True)
    after = schema_tokens(compressed)
    log.warning("tools schema 压缩后仍超预算：{} -> {} tokens（budget={}），保留全部工具",
                before, after, budget)
    return compressed, before, after