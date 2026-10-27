"""
语义层在线检索：embedding + 混合召回 + 上下文组装。

职责边界：
  - 只做“召回与格式化”，不生成 SQL、不调用 LLM；
  - embedding 失败/超时自动降级为关键词通道（不要让向量服务拖垮分析主链路）。
"""
from __future__ import annotations

import asyncio

from shared import log
from codegenx.ai_service.semantic.model.cards import (
    fmt_metric_card,
    fmt_table_card,
    fmt_verified_query,
)
from codegenx.ai_service.semantic.store import pg_store
from codegenx.ai_service.utils.config import config

# embedding 属于共享基础设施（与记忆系统共用模型配置），此处只借用客户端


async def embed_query(text: str) -> list[float] | None:
    if not (text or "").strip():
        return None
    try:
        from codegenx.ai_service.llm.embedding import get_embedding_client

        return await asyncio.wait_for(
            get_embedding_client().embed_query(text),
            timeout=config.semantic.embed_timeout_seconds,
        )
    except Exception as exc:  # 网络/额度/超时均为可降级错误
        log.warning("语义层 embedding 失败，降级为关键词召回: {!r}", exc)
        return None


async def _with_keyword_fallback(call, *args, embedding: list[float] | None, **kwargs):
    """先走混合检索；向量维度/扩展异常时退化为纯关键词检索一次。"""
    try:
        return await call(*args, embedding=embedding, **kwargs)
    except (LookupError, TypeError):
        raise
    except Exception as exc:
        if not embedding:
            raise
        log.warning("混合检索失败，退化为关键词检索: {}", exc)
        return await call(*args, embedding=None, **kwargs)


async def search_tables(
    query: str, *, datasource: str | None = None, domain: str | None = None,
    top_k: int | None = None, embedding: list[float] | None = None,
) -> list[dict]:
    s = config.semantic
    emb = embedding if embedding is not None else await embed_query(query)
    return await _with_keyword_fallback(
        pg_store.search_tables, query, embedding=emb,
        datasource=datasource, domain=domain,
        top_k=top_k or s.recall_top_k,
        min_score=s.min_score, vector_weight=s.hybrid_vector_weight,
    )


async def search_metrics(
    query: str, *, datasource: str | None = None,
    top_k: int | None = None, embedding: list[float] | None = None,
) -> list[dict]:
    s = config.semantic
    emb = embedding if embedding is not None else await embed_query(query)
    return await _with_keyword_fallback(
        pg_store.search_metrics, query, embedding=emb,
        datasource=datasource, top_k=top_k or s.metric_top_k,
        min_score=s.min_score, vector_weight=s.hybrid_vector_weight,
    )


async def search_verified_queries(
    question: str, *, datasource: str | None = None,
    top_k: int | None = None, embedding: list[float] | None = None,
) -> list[dict]:
    s = config.semantic
    emb = embedding if embedding is not None else await embed_query(question)
    return await _with_keyword_fallback(
        pg_store.search_verified_queries, question, embedding=emb,
        datasource=datasource, top_k=top_k or s.verified_query_top_k,
        min_score=s.min_score, vector_weight=s.hybrid_vector_weight,
    )


async def build_context(question: str, datasource: str | None = None) -> str:
    """组装注入 prompt 的语义上下文（表卡 + 指标卡 + 已验证示例）。

    空语义层/数据源未注册时返回占位文案，不抛异常——调用方是 prompt 组装链路，
    不能因为元数据缺失让整个分析请求失败。
    """
    s = config.semantic
    if not s.enabled:
        return ""

    emb = await embed_query(question)
    try:
        tables, metrics, examples = await asyncio.gather(
            search_tables(question, datasource=datasource, embedding=emb),
            search_metrics(question, datasource=datasource, embedding=emb),
            search_verified_queries(question, datasource=datasource, embedding=emb),
        )
    except LookupError as exc:
        return f"（语义层不可用：{exc}）"
    except Exception as exc:
        log.warning("语义上下文组装失败: {}", exc)
        return "（语义层检索异常，已降级为无上下文）"

    parts: list[str] = []
    if tables:
        parts.append("相关表:\n" + "\n".join(fmt_table_card(t, t.get("score")) for t in tables))
    if metrics:
        parts.append("相关指标:\n" + "\n".join(fmt_metric_card(m, m.get("score")) for m in metrics))
    if examples:
        parts.append("已验证示例:\n" + "\n".join(fmt_verified_query(e, e.get("score")) for e in examples))

    if not parts:
        return "（语义层未召回相关元数据；可调用 search_tables 换关键词重试）"

    text = "\n\n".join(parts)
    if len(text) > s.context_max_chars:
        text = text[: s.context_max_chars] + "\n...(语义上下文已截断)"
    return text
