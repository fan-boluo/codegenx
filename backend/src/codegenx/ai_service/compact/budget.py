"""
统一预算分配器（上下文工程排期③）——模块级 token 管控。

设计（docs/上下文工程设计.md §1.1）：
  总预算 = 模型窗口 × 预算比例（预留输出 max_tokens 与安全余量）；
  分区软硬限 + 固定驱逐优先级：工具结果原文 → 中段历史 → 记忆注入 → 会话摘要；
  驱逐不是删除，而是降级为占位符——工具结果可重新调用回捞、历史原文在
  chat_messages 快照与会话记录中可回捞。

边界（单一职责）：
  本模块只做消息级「就地降级」：ReAct 循环消息列表（context.chat_messages）内
  tool/历史消息的占位符替换（列表长度不变，tool_call_id 配对完整保留）。
  system 内部可驱逐分区（记忆/摘要）由 BaseReActAgent._rebuild_system_prompt
  经 expand 变量占位替换重建承载，编排在 BaseReActAgent._enforce_context_budget。
"""

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


def enforce_budget_inplace(
    messages: list[dict],
    *,
    budget: int,
    protect_last_n_tool_results: int = 5,
) -> BudgetReport:
    """就地执行预算：超限按驱逐优先级降级消息内容，原列表原地修改并返回报告。

    分区语义（工作列表形态，system 不在列表内、由 context.system_prompt 承载）：
      [*history]        —— 最后一条 user 之前的 user/assistant 文本消息（中段历史，
                           驱逐优先级 2，最旧优先）；
      [user(task)]      —— 最后一条 user = 当前任务输入，永不驱逐；
      [assistant/tool]  —— ReAct 循环区，tool 消息为工具结果（驱逐优先级 1，
                           保护最近 N 条不被驱逐）。
    幂等：已是占位符的消息跳过（system 分区驱逐后复量时不会虚增计数）。
    """
    tokens_before = estimate_tokens(messages)
    report = BudgetReport(budget=budget, tokens_before=tokens_before, tokens_after=tokens_before)
    if tokens_before <= budget:
        return report

    # 分区：最后一条 user 之前 = 中段历史；tool 消息全列表收集（均在循环区）
    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    history_idx = [
        i for i in range(0, last_user_idx)
        if messages[i].get("role") in ("user", "assistant") and messages[i].get("content")
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


def _apply_history_budget(history: list[dict]) -> list[dict]:
    """会话历史 token 预算：超限从最旧截断（单次遍历，保留预算内最新消息）。

    历史仅 user/assistant 文本消息（无 tool 消息），microcompact 对其无效，
    直接做最旧截断；预算阈值经 config.compact.leader_history_budget_tokens 配置。
    """
    from codegenx.ai_service.utils.config import config as cfg
    budget = cfg.compact.leader_history_budget_tokens
    if estimate_tokens(history) <= budget:
        return history
    kept: list[dict] = []
    total = 0
    for m in reversed(history):
        cost = estimate_tokens([m])
        if total + cost > budget and kept:
            break
        kept.append(m)
        total += cost
    kept.reverse()
    log.info("leader 历史超预算截断：{} 条 -> {} 条（budget={} tokens）",
             len(history), len(kept), budget)
    return kept