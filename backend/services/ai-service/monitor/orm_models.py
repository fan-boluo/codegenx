"""SQLAlchemy ORM models for the four monitor tables."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SpanModel(Base):
    __tablename__ = "spans"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_id = Column(String(64), nullable=False, default="main")
    user_id = Column(String(64), default="")
    trace_id = Column(String(32), nullable=False)
    span_id = Column(String(16), nullable=False)
    parent_span_id = Column(String(16))
    session_id = Column(String(64), nullable=False)
    turn_id = Column(String(64), default="")
    turn_number = Column(Integer, default=0)
    operation_name = Column(String(128), nullable=False)
    start_time = Column(DateTime(3), nullable=False)
    end_time = Column(DateTime(3))
    duration_ms = Column(Integer)
    status = Column(String(10), default="running")
    attributes = Column(JSON)


class SessionMetricsModel(Base):
    __tablename__ = "session_metrics"

    session_id = Column(String(64), primary_key=True)
    trace_id = Column(String(32), nullable=False)
    app_id = Column(String(64), nullable=False, default="main")
    user_id = Column(String(64))
    model = Column(String(32))
    status = Column(String(16), default="running")
    total_turns = Column(Integer, default=0)
    total_prompt_tokens = Column(BigInteger, default=0)
    total_completion_tokens = Column(BigInteger, default=0)
    token_budget = Column(BigInteger, default=0)
    sum_llm_latency_ms = Column(BigInteger, default=0)
    sum_first_token_ms = Column(BigInteger, default=0)
    max_llm_latency_ms = Column(Integer, default=0)
    min_llm_latency_ms = Column(Integer, default=0)
    total_tool_calls = Column(Integer, default=0)
    total_errors = Column(Integer, default=0)
    recovery_count = Column(Integer, default=0)
    last_recovery_kind = Column(String(32), default="")
    avg_memory_hits = Column(Float, default=0.0)
    total_memory_hits = Column(Integer, default=0)
    end_reason = Column(String(32))
    started_at = Column(DateTime(3), nullable=False)
    ended_at = Column(DateTime(3))
    duration_ms = Column(Integer, default=0)
    updated_at = Column(DateTime(3))


class TurnMetricsModel(Base):
    __tablename__ = "turn_metrics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String(32), nullable=False)
    session_id = Column(String(64), nullable=False)
    request_id = Column(String(64), nullable=False, default="")
    turn_id = Column(String(64), nullable=False)
    turn_number = Column(Integer, nullable=False)
    status = Column(String(16), default="running")
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    llm_latency_ms = Column(Integer)
    first_token_ms = Column(Integer)
    llm_recovery_count = Column(Integer, default=0)
    llm_recovery_kind = Column(String(32), default="")
    tool_calls_count = Column(Integer, default=0)
    tool_calls_detail = Column(JSON)
    memory_hits = Column(Integer, default=0)
    memory_retrieval_ms = Column(Integer)
    context_tokens = Column(Integer)
    context_token_usage = Column(Integer)
    error_count = Column(Integer, default=0)
    started_at = Column(DateTime(3))
    ended_at = Column(DateTime(3))
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime(3))


class MonitorAlertModel(Base):
    __tablename__ = "monitor_alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_name = Column(String(64), nullable=False)
    level = Column(String(16), nullable=False)
    trace_id = Column(String(32), nullable=False)
    session_id = Column(String(64), nullable=False)
    turn_id = Column(String(64), default="")
    status = Column(String(16), default="open")
    message = Column(String(255), nullable=False)
    observed_value = Column(String(128), default="")
    threshold_value = Column(String(128), default="")
    triggered_at = Column(DateTime(3), nullable=False)
    resolved_at = Column(DateTime(3))
    payload = Column(JSON)
