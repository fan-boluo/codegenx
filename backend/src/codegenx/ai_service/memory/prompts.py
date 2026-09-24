"""
记忆系统提示词 —— 提取（对话 → 候选记忆）与仲裁（新记忆 vs 已有记忆）。

会话摘要提示词不在这里：已随会话摘要服务移交 compact/session_summary.py。
"""
from __future__ import annotations

import json

# ── 提取：对话片段 → 候选记忆列表 ─────────────────────────────────────────────

MEMORY_EXTRACT_SYSTEM_PROMPT = """\
你是记忆提取助手。从对话片段中提取值得长期保存的记忆条目。

只提取以下四种类型（memory_type 必须是其中之一）：
- user_correction: 用户对助手行为的纠正或明确指引（该做什么、不要做什么）。权重最高。
- user_preference: 用户角色、职责、偏好、知识水平、沟通风格。
- project_background: 项目背景、业务目标、进行中的工作、约束条件（无法从代码直接看出的）。
- resource_path: 重要资源位置（数据表、文件、外部系统入口）。

判定标准：
1. 只提取持久有用的事实，一次性过程性内容（"我打开了这个文件"）不要提取。
2. 用户显式表达的偏好和纠正必须提取，即使看似琐碎。
3. 每条记忆压缩为一句话，信息密集，无主语（面向未来对话的助手阅读）。
4. 相对时间转为绝对日期（"下周三" → "2026-09-30"）。
5. 不确定就跳过：宁缺毋滥，错误记忆比没有记忆更糟。
6. 最多提取 8 条。

只输出 JSON 数组，不要输出任何其他文字：
[
  {"topic": "简短话题标签(2-6字)", "memory_type": "四种之一", "content": "一句话记忆"},
  ...
]

无值得提取的内容时输出 []。
"""

MEMORY_EXTRACT_USER_PROMPT = """\
对话片段（按时间顺序，可能包含工具调用摘要）：

{conversation}
"""


def format_conversation_for_extract(turns_text: str) -> str:
    return MEMORY_EXTRACT_USER_PROMPT.format(conversation=turns_text)


def parse_extracted_memories(raw: str) -> list[dict]:
    """解析 LLM 输出的候选记忆 JSON 数组；容错截断/包裹，坏条目跳过。"""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # 剥离 markdown 代码块包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 容错：截取首个 [ 到最后一个 ] 之间再试
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        result.append({
            "topic": str(item.get("topic", "") or "").strip()[:32],
            "memory_type": str(item.get("memory_type", "") or "").strip(),
            "content": content[:500],
        })
    return result


# ── 仲裁：新候选 vs 已有相似记忆 ───────────────────────────────────────────────

MEMORY_ADJUDICATE_SYSTEM_PROMPT = """\
你是记忆库仲裁助手。现在有一条新提取的记忆，和记忆库中与它最相似的旧记忆。
判断新记忆应该如何处理：

- DUPLICATE: 旧记忆已完整表达同一事实（措辞不同也算重复），无需写入。
- UPDATE: 旧记忆部分过时或信息不完整，新记忆是更完整/更新的版本。在 content 字段给出合并后的最终表述（以新信息为准，保留旧记忆中仍然有效的细节）。
- CONFLICT: 新旧记忆相互矛盾（如用户改变了决定）。新记忆胜出，旧记忆将失效。
- KEEP_BOTH: 两者相关但表达不同事实，都应保留。

只输出 JSON 对象，不要输出任何其他文字：
{"action": "DUPLICATE|UPDATE|CONFLICT|KEEP_BOTH", "content": "仅 UPDATE 时需要，其余留空"}
"""

MEMORY_ADJUDICATE_USER_PROMPT = """\
新记忆：
{new_content}

已有相似记忆：
{existing_list}
"""


def format_adjudicate_user_prompt(new_content: str, matches: list[tuple[str, str]]) -> str:
    """matches: [(memory_id, content), ...]"""
    lines = [f"- [{mid}] {content}" for mid, content in matches]
    return MEMORY_ADJUDICATE_USER_PROMPT.format(
        new_content=new_content,
        existing_list="\n".join(lines) or "（无）",
    )


def parse_adjudication(raw: str) -> dict:
    """解析仲裁结果；解析失败按 KEEP_BOTH 处理（保守：都保留）。"""
    if not raw or not raw.strip():
        return {"action": "KEEP_BOTH", "content": ""}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {"action": "KEEP_BOTH", "content": ""}
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {"action": "KEEP_BOTH", "content": ""}
    action = str(data.get("action", "") or "").strip().upper()
    if action not in {"DUPLICATE", "UPDATE", "CONFLICT", "KEEP_BOTH"}:
        action = "KEEP_BOTH"
    return {"action": action, "content": str(data.get("content", "") or "").strip()}
