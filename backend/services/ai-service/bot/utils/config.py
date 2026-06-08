import json
from pathlib import Path
from typing import ClassVar, List
from functools import lru_cache
from pydantic import AliasChoices, ConfigDict, Field, BaseModel
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config.json"


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys.
    可包含除了configmodel里面明确的其它字段
    """
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentConfig(Base):
    id: str = ""
    token: str = ""
    name: str = ""
    defaults: bool = False
    workspace: str = "~/.bot/workspace"
    model: str = "dashscope-qwen/qwen3.7-plus"
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_max_tokens: int = Field(
        default=8192,
        validation_alias=AliasChoices("contextMaxTokens"),
    )
    context_max_history_turns: int = Field(
        default=5,
        validation_alias=AliasChoices("contextMaxHistoryTurns", "context_max_history_turns"),
    )
    context_summary_max_length: int = Field(
        default=150,
        validation_alias=AliasChoices("contextSummaryMaxLength", "context_summary_max_length"),
    )
    temperature: float = 0.1
    max_tool_iterations: int = 20
    session_worker_idle_seconds: int = Field(
        default=1800,
        validation_alias=AliasChoices("sessionWorkerIdleSeconds", "session_worker_idle_seconds"),
    )
    session_stop_grace_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices("sessionStopGraceSeconds", "session_stop_grace_seconds"),
    )
    # Deprecated compatibility field: accepted from old configs but ignored at runtime.
    memory_window: int | None = Field(default=None, exclude=True)
    max_same_tool_calls: ClassVar[int] = 3
    max_continuation_attempts: ClassVar[int] = 3
    max_compact_attempts: ClassVar[int] = 2
    max_transport_attempts: ClassVar[int] = 3
    transport_backoff_base_seconds: ClassVar[float] = 1.0
    transport_backoff_max_seconds: ClassVar[float] = 8.0
    llm_stream_timeout_seconds: ClassVar[float] = 300.0  # 单次 LLM 流式调用总超时（秒）
    max_steps: int = Field(
        default=50,
        validation_alias=AliasChoices("maxSteps", "max_steps"),
    )  # 一次turn的最大llm推理步数
    session_cleanup_interval_seconds: ClassVar[int] = 300 # 每隔5分钟就进行闲置sessioni清理一次
    session_idle_timeout_seconds : ClassVar[int] = 1800 # session空闲时间 30分钟就清除
    max_sessions : ClassVar[int] = 100  # sessoion pool的session的最多个数

    @property
    def context_window_tokens(self) -> int:
        return self.context_max_tokens

    @property
    def resolved_model_name(self) -> str:
        model_name = str(self.model or "").strip()
        if "/" in model_name:
            return model_name.split("/", 1)[1]
        return model_name


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    # model_name: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(Base):
    """Configuration for LLM providers."""
    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)


class EmbeddingConfig(Base):
    api_key: str = ""
    api_base: str | None = None
    model_name: str = "text-embedding-v4"
    model_path: str = Field(
        default="",
        validation_alias=AliasChoices("modelPath", "model_path"),
    )
    max_text_length: int = Field(
        default=8000,
        validation_alias=AliasChoices("maxTextLength", "max_text_length"),
    )
    api_timeout_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices("apiTimeoutSeconds", "api_timeout_seconds", "apiTimeout"),
    )
    dimensions: int = 1024

    @property
    def embedding_path(self) -> str:
        return self.model_path or self.model_name


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


#  tool -------------------------
class ToolsConfig(Base):
    """Tools configuration."""
    excluded:list = Field(default_factory=list)
    # exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    # restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    # mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


# memory ----------------------------------
class MemorySearchConfig(Base):
    """Memory search configuration """
    enabled: bool = Field(default=True)
    search_top_k:int = Field(default=10)
    search_score_threshold :float = Field(default=0.60)
    short_lookback_days:int =Field(default=7)  # 短期记忆搜索天数范围
    hybrid_vector_weight:float = Field(default=0.7)
    long_term_weight:float = Field(default=0.7)
    merge_result_similarity:float = Field(default=0.8)


class MemoryFlushConfig(Base):
    """Memory flush configuration )"""
    enabled: bool = Field(default=True)
    softThresholdTokens: int = Field(default=4000)
    forceFlushTranscriptBytes: int | None = Field(default=2 * 1024 * 1024)  # 2MB
    prompt: str | None = Field(default=None)
    systemPrompt: str | None = Field(default=None)
    ttlDays: int = Field(default=7)  # 短期记忆过期天数
    shortDuplicatedScoreThreshold: float = Field(default=0.90)  # 短期记忆去重分数阈值，超过这个分数的记忆会被认为是重复的，不会被写入记忆库
    longMatchesScoreThreshold: float = Field(default=0.7)  # 长期记忆写入匹配分数阈值，分数大于这个值的被认为与当前的短期记忆相关，会交给大模型判断
    longMatchesTopK: int = Field(default=3)  # 长期记忆写入匹配结果的最大数量
    longDirectWriteImportanceThreshold: float = Field(default=0.79)  # 长期记忆直接写入的重要性阈值，无匹配且超过这个分数的记忆会被直接写入长期记忆库


class MemoryConfig(Base):
    search: MemorySearchConfig = Field(default_factory=MemorySearchConfig)
    store: MemoryFlushConfig = Field(default_factory=MemoryFlushConfig)


class MonitorStorageConfig(Base):
    enabled: bool = Field(default=True)
    persistSpans: bool = Field(default=True)
    persistSessionMetrics: bool = Field(default=True)
    persistTurnMetrics: bool = Field(default=True)
    useRedisWindows: bool = Field(default=True)


class MonitorAlertLevel(str):
    WARN = "WARN"
    ERROR = "ERROR"


class MonitorLatencyWindowRuleConfig(Base):
    enabled: bool = Field(default=True)
    level: str = Field(default=MonitorAlertLevel.WARN)
    windowSize: int = Field(default=5)
    thresholdSeconds: int = Field(default=10)


class MonitorLatencyThresholdRuleConfig(Base):
    enabled: bool = Field(default=True)
    level: str = Field(default=MonitorAlertLevel.ERROR)
    thresholdSeconds: int = Field(default=60)


class MonitorRatioRuleConfig(Base):
    enabled: bool = Field(default=True)
    level: str = Field(default=MonitorAlertLevel.WARN)
    thresholdRatio: float = Field(default=0.90)


class MonitorConsecutiveFailureRuleConfig(Base):
    enabled: bool = Field(default=True)
    level: str = Field(default=MonitorAlertLevel.ERROR)
    thresholdCount: int = Field(default=3)


class MonitorTurnThresholdRuleConfig(Base):
    enabled: bool = Field(default=True)
    level: str = Field(default=MonitorAlertLevel.WARN)
    thresholdTurns: int = Field(default=50)


class MonitorAlertRulesConfig(Base):
    llmAvgLatencyLast5: MonitorLatencyWindowRuleConfig = Field(
        default_factory=MonitorLatencyWindowRuleConfig,
        validation_alias=AliasChoices("llmAvgLatencyLast5", "llmLast5AvgLatency"),
    )
    llmSingleTimeout: MonitorLatencyThresholdRuleConfig = Field(
        default_factory=MonitorLatencyThresholdRuleConfig,
        validation_alias=AliasChoices("llmSingleTimeout", "llmInvokeLatency"),
    )
    tokenQuotaUsage: MonitorRatioRuleConfig = Field(
        default_factory=MonitorRatioRuleConfig,
        validation_alias=AliasChoices("tokenQuotaUsage", "TokenUsagePercent"),
    )
    toolConsecutiveFailures: MonitorConsecutiveFailureRuleConfig = Field(
        default_factory=MonitorConsecutiveFailureRuleConfig,
        validation_alias=AliasChoices("toolConsecutiveFailures", "ToolContinueFailCount"),
    )
    sessionMaxTurns: MonitorTurnThresholdRuleConfig = Field(
        default_factory=MonitorTurnThresholdRuleConfig,
        validation_alias=AliasChoices("sessionMaxTurns", "TurnMax"),
    )


class MonitorConfig(Base):
    enabled: bool = Field(default=True)
    storage: MonitorStorageConfig = Field(default_factory=MonitorStorageConfig)
    alerts: MonitorAlertRulesConfig = Field(default_factory=MonitorAlertRulesConfig)


class CompactConfig(Base):
    maxToolResultTokens: int = Field(
        default=3000,
        validation_alias=AliasChoices("maxToolResultTokens", "max_tool_result_tokens"),
    )


class Config(BaseSettings):
    agents: List[AgentConfig] = Field(default_factory=list)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    monitor:MonitorConfig = Field(default_factory=MonitorConfig)
    compact: CompactConfig = Field(default_factory=CompactConfig)

    def get_default_agent(self) -> AgentConfig:
        if not self.agents:
            return AgentConfig()
        for agent in self.agents:
            if agent.defaults:
                return agent
        return self.agents[0]

    def get_agent(self, identifier: str | None = None) -> AgentConfig:
        if not self.agents:
            return AgentConfig()
        if identifier:
            normalized = str(identifier).strip().lower()
            for agent in self.agents:
                if normalized in {str(agent.id).strip().lower(), str(agent.token).strip().lower(), str(agent.name).strip().lower()}:
                    return agent
        return self.get_default_agent()

    def get_provider(self, provider_name: str | None = None) -> ProviderConfig:
        normalized = str(provider_name or "").strip().lower()
        if normalized and hasattr(self.providers, normalized):
            return getattr(self.providers, normalized)
        return self.providers.custom

@lru_cache(maxsize=1)
def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.
    Args:
        config_path: Optional path to config file. Uses default if not provided.
    Returns:
        Loaded configuration object.
    """
    path = config_path or _CONFIG_PATH

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()

if __name__ == '__main__':
    config = load_config()
    print(config)