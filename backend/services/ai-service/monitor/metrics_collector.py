from opentelemetry import metrics

class MetricsCollector:
    """基于 OTel Meter 的指标采集"""

    def __init__(self):
        self.meter = metrics.get_meter(__name__)

        # --- Counter（只增不减） ---
        self.turn_counter = self.meter.create_counter(
            name="agent.turn.count",
            description="Total turns per session"
        )
        self.token_counter = self.meter.create_counter(
            name="agent.token.usage",
            description="Token consumption"
        )
        self.tool_call_counter = self.meter.create_counter(
            name="agent.tool_call.count",
            description="Tool call count by name and status"
        )
        self.error_counter = self.meter.create_counter(
            name="agent.error.count",
            description="Error count by type"
        )
        self.session_counter = self.meter.create_counter(
            name="agent.session.count",
            description="Session count by end_reason"
        )

        # --- Histogram（分布统计） ---
        self.llm_latency_histogram = self.meter.create_histogram(
            name="agent.llm.latency_ms",
            description="LLM call latency in ms"
        )
        self.first_token_histogram = self.meter.create_histogram(
            name="agent.llm.first_token_ms",
            description="Time to first token in ms"
        )
        self.tool_latency_histogram = self.meter.create_histogram(
            name="agent.tool.latency_ms",
            description="Tool execution latency in ms"
        )
        self.memory_retrieval_histogram = self.meter.create_histogram(
            name="agent.memory.retrieval_latency_ms",
            description="Memory retrieval latency in ms"
        )

        # --- Gauge（瞬时值） ---
        self.context_tokens_gauge = self.meter.create_gauge(
            name="agent.context.tokens",
            description="Current context window token count"
        )
        self.quota_tokens_gauge = self.meter.create_gauge(
            name="agent.quota.tokens_remaining",
            description="Remaining token quota"
        )

    # --- 会话级指标 ---
    def record_turn(self, session_id: str) -> None:
        self.turn_counter.add(1, {"session.id": session_id})

    def record_token_usage(
        self,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> None:
        attrs = {"session.id": session_id}
        self.token_counter.add(prompt_tokens, {**attrs, "token.type": "prompt"})
        self.token_counter.add(completion_tokens, {**attrs, "token.type": "completion"})

    def record_llm_latency(
        self,
        session_id: str,
        model: str,
        first_token_ms: int,
        total_ms: int
    ) -> None:
        attrs = {"session.id": session_id, "model.name": model}
        self.llm_latency_histogram.record(total_ms, attrs)
        self.first_token_histogram.record(first_token_ms, attrs)

    def record_tool_call(
        self,
        session_id: str,
        tool_name: str,
        latency_ms: int,
        status: str
    ) -> None:
        attrs = {"session.id": session_id, "tool.name": tool_name, "status": status}
        self.tool_call_counter.add(1, attrs)
        self.tool_latency_histogram.record(latency_ms, attrs)

    def record_memory_retrieval(
        self,
        session_id: str,
        hits: int,
        latency_ms: int
    ) -> None:
        self.memory_retrieval_histogram.record(
            latency_ms,
            {"session.id": session_id, "hits": hits}
        )

    def record_context_size(
        self,
        session_id: str,
        token_count: int
    ) -> None:
        self.context_tokens_gauge.set(
            token_count,
            {"session.id": session_id}
        )

    # --- 配额追踪 ---
    def record_quota_usage(
        self,
        session_id: str,
        tokens_remaining: int
    ) -> None:
        self.quota_tokens_gauge.set(
            tokens_remaining,
            {"session.id": session_id}
        )

    # --- 错误记录 ---
    def record_error(
        self,
        session_id: str,
        error_type: str
    ) -> None:
        self.error_counter.add(
            1,
            {"session.id": session_id, "error.type": error_type}
        )

    # --- 会话结束 ---
    def record_session_end(
        self,
        session_id: str,
        end_reason: str,
        total_turns: int,
        total_tokens: int
    ) -> None:
        self.session_counter.add(
            1,
            {
                "session.id": session_id,
                "end.reason": end_reason,
                "total.turns": total_turns,
                "total.tokens": total_tokens
            }
        )