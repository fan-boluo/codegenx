"""SQLAlchemy ORM models for the four monitor tables."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, Integer, JSON, String
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
    request_id = Column(String(64), default="")
    step_counter = Column(Integer, default=0)
    operation_type = Column(String(128), nullable=False)
    start_time = Column(DateTime(3), nullable=False)
    end_time = Column(DateTime(3))
    duration_ms = Column(Integer)
    status = Column(String(10), default="running")
    attributes = Column(JSON)


class SessionMetricsModel(Base):
    __tablename__ = "session_metrics"

    session_id = Column(String(64), primary_key=True)
    trace_id = Column(String(32), nullable=False)
    request_id = Column(String(64), nullable=False, default="")
    app_id = Column(String(64), nullable=False, default="main")
    user_id = Column(String(64))
    model = Column(String(32), nullable=False, default="unknown")
    span_id = Column(String(64), nullable=False, default="")
    status = Column(String(16), default="running")
    end_reason = Column(String(32))
    turn_number = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    token_usage = Column(Float, default=0.0)
    is_compress = Column(Boolean, default=False)
    total_prompt_tokens = Column(BigInteger, default=0)
    total_completion_tokens = Column(BigInteger, default=0)
    total_tokens = Column(BigInteger, default=0)
    llm_recovery_count = Column(Integer, default=0)
    last_recovery_kind = Column(String(32), default="")
    total_tool_calls = Column(Integer, default=0)
    total_tool_call_errors = Column(Integer, default=0)
    total_memory_hits = Column(Integer, default=0)
    memory_is_error = Column(Boolean, default=False)
    started_at = Column(DateTime(3), nullable=False)
    ended_at = Column(DateTime(3))
    duration_ms = Column(Integer, default=0)
    updated_at = Column(DateTime(3))


class TurnMetricsModel(Base):
    __tablename__ = "turn_metrics"

    turn_id = Column(String(64), primary_key=True)
    session_id = Column(String(64), nullable=False)
    trace_id = Column(String(32), nullable=False)
    request_id = Column(String(64), nullable=False, default="")
    app_id = Column(String(64), nullable=False, default="main")
    user_id = Column(String(64))
    model = Column(String(32), nullable=False, default="unknown")
    span_id = Column(String(64), nullable=False, default="")
    turn_number = Column(Integer, default=0)
    status = Column(String(16), default="running")
    end_reason = Column(String(32), default="")
    token_count = Column(Integer, default=0)
    token_usage = Column(Float, default=0.0)
    is_compress = Column(Boolean, default=False)
    total_prompt_tokens = Column(BigInteger, default=0)
    total_completion_tokens = Column(BigInteger, default=0)
    total_tokens = Column(BigInteger, default=0)
    llm_recovery_count = Column(Integer, default=0)
    last_recovery_kind = Column(String(32), default="")
    total_tool_calls = Column(Integer, default=0)
    total_tool_call_errors = Column(Integer, default=0)
    total_memory_hits = Column(Integer, default=0)
    memory_is_error = Column(Boolean, default=False)
    started_at = Column(DateTime(3))
    ended_at = Column(DateTime(3))
    duration_ms = Column(Integer, default=0)
    updated_at = Column(DateTime(3))


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
