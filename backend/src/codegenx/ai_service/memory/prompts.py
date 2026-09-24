"""
记忆系统提示词 —— 提炼（对话片段 → 候选记忆，设计方案 v2.1 §4.1/§4.3）。

会话摘要提示词不在这里：已随会话摘要服务移交 compact/session_summary.py。
v1 的相似仲裁（adjudicate）提示词已随「写入时同步 LLM 判重」链路一起移除——
v2 写入不再判重（hot 走 uk_slot upsert、warm append-only），矛盾检测降级为
每日异步 conflict_detect（P1）。
"""
from __future__ import annotations

import json

# ── 提炼：对话片段 → 候选记忆列表 ─────────────────────────────────────────────

MEMORY_EXTRACT_SYSTEM_PROMPT = """\
你是记忆提取助手。从对话片段中提取值得长期保存的记忆条目。

记忆分两层，layer 由 memory_type 决定，无需输出 layer：

hot 层（核心约束，每轮必然生效；只收用户明确表达的内容）：
- hard_constraint: 项目硬性规范、业务强限制（"所有金额计算保留2位小数"）
- system_rule: 系统基准规则（"回复一律用中文"）
- user_preference: 用户明确表达的偏好（"不要 emoji"、"代码用 TypeScript"）
- identity_fact: 用户身份与稳定事实（"我是后端工程师"、"项目 A 的表结构在 xxx"）
- external_resource: 重要资源位置（数据表、文件、外部系统入口、路径）

warm 层（情景记忆，带时间的一次性经历）：
- user_correction: 用户对某次输出的纠正（"2026-09-20 用户指出方案太啰嗦"）
- task_experience: 历史任务的解法与结论（"排查连接池泄漏，定位为 maxLifetime 配错"）
- project_background: 项目阶段性背景（非硬性规范）
- interaction_habit: 交互习惯观察（"用户倾向于先看结论再看推导"）

输出字段：
- memory_type: 上述九种之一
- slot_key: 仅 hot 层必填。该属性的稳定英文标识（如 no_emoji、language_ts、
  deploy_env），同类偏好必须用同一 slot，便于后续更新取代；warm 层留空 ""
- content: 一句话记忆，信息密集，无主语；相对时间转为绝对日期
- source_type: 1=用户明说 2=你的推断。推断内容必须 source_type=2 且
  confidence<0.7，并且不得使用 hot 层类型
- confidence: 0~1，用户明说接近 1

准入（禁止写入，出现即跳过该条）：
1. 临时闲聊、情绪表达、一次性过程性内容（"我打开了这个文件"）
2. 瞬时上下文指代（"刚才那个文件"）
3. 敏感信息：密码、密钥、token、api key、身份证号、银行卡号、完整手机号
4. 不确定的猜测：宁缺毋滥，错误记忆比没有记忆更糟
5. 最多提取 8 条

只输出 JSON 数组，不要输出任何其他文字：
[
  {"memory_type": "...", "slot_key": "...", "content": "...", "source_type": 1, "confidence": 0.95},
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
    """解析 LLM 输出的候选记忆 JSON 数组；容错截断/包裹，坏条目跳过。

    校验（写入前的第一道准入，硬规则不依赖 LLM 自觉，§2.4/§4.3）：
    - memory_type 必须在九种内置类型内，否则丢弃
    - source_type 缺失按 2（推断）保守处理；推断强制 confidence ≤ 0.69
    """
    from codegenx.ai_service.memory.models import BUILTIN_MEMORY_TYPES

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
        memory_type = str(item.get("memory_type", "") or "").strip()
        if not content or memory_type not in BUILTIN_MEMORY_TYPES:
            continue
        try:
            source_type = int(item.get("source_type") or 2)
        except (TypeError, ValueError):
            source_type = 2
        if source_type not in (1, 2):
            source_type = 2
        try:
            confidence = float(item.get("confidence") or 0.6)
        except (TypeError, ValueError):
            confidence = 0.6
        if source_type == 2:
            confidence = min(confidence, 0.69)  # 推断置信度硬上限（§4.3）
        result.append({
            "topic": str(item.get("topic", "") or "").strip()[:32],
            "memory_type": memory_type,
            "slot_key": str(item.get("slot_key", "") or "").strip()[:64],
            "content": content[:500],
            "source_type": source_type,
            "confidence": round(confidence, 2),
        })
    return result
