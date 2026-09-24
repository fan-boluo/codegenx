import json
from pathlib import Path
from typing import ClassVar, List
from functools import lru_cache
from pydantic import AliasChoices, ConfigDict, Field, BaseModel
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config.json"  # ai_service/utils/config.py → 上跳 4 级为 backend/


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
    dimensions: int = 512

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
    persist_threshold: int = Field(default=30000)  # 持久化阈值
    preview_chars: int = Field(default=2000) # 展示长度
    # exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    # restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    # mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


# memory ----------------------------------
class MemorySearchConfig(Base):
    """记忆检索配置：混合召回（向量+关键词）+ 三因子重排（语义/时间/类型）"""
    enabled: bool = Field(default=True)
    top_k: int = Field(
        default=10,
        validation_alias=AliasChoices("topK", "top_k", "searchTopK"),
    )  # 向量通道召回条数
    score_threshold: float = Field(
        default=0.45,
        validation_alias=AliasChoices("scoreThreshold", "score_threshold", "searchScoreThreshold"),
    )  # 向量原始相似度下限
    keyword_top_k: int = Field(
        default=10,
        validation_alias=AliasChoices("keywordTopK", "keyword_top_k"),
    )  # 关键词通道召回条数
    # 三因子重排权重（语义 / 时间衰减 / 类型），和为 1
    rerank_vector_weight: float = Field(default=0.7, validation_alias=AliasChoices("rerankVectorWeight", "rerank_vector_weight"))
    rerank_time_weight: float = Field(default=0.2, validation_alias=AliasChoices("rerankTimeWeight", "rerank_time_weight"))
    rerank_type_weight: float = Field(default=0.1, validation_alias=AliasChoices("rerankTypeWeight", "rerank_type_weight"))
    time_decay_half_life_days: int = Field(
        default=30,
        validation_alias=AliasChoices("timeDecayHalfLifeDays", "time_decay_half_life_days"),
    )  # 时间衰减半衰期：0.5^(age_days/half_life)
    hot_token_budget: int = Field(
        default=2500,
        validation_alias=AliasChoices("hotTokenBudget", "hot_token_budget"),
    )  # hot 层注入 token 预算（§5.4 B1 建议值，需按上下文压测调整）
    warm_token_budget: int = Field(
        default=8192,
        validation_alias=AliasChoices("warmTokenBudget", "warm_token_budget"),
    )  # warm 层注入 token 预算


class MemoryFlushConfig(Base):
    """记忆写入/生命周期配置（条件触发离线提取 + 衰减归档）"""
    enabled: bool = Field(default=True)
    model_name: str | None = Field(default=None)  # 离线提取小模型；空则用默认模型
    extract_max_concurrency: int = Field(default=2)  # 离线 LLM 并发上限（信号量隔离，避免挤占在线对话资源）

    consolidate_hour: int = Field(default=3)  # 每日整理/衰减/对账任务触发小时（24h 制）
    decay_days: int = Field(
        default=30,
        validation_alias=AliasChoices("decayDays", "decay_days"),
    )  # 未访问软删除天数
    archive_days: int = Field(
        default=90,
        validation_alias=AliasChoices("archiveDays", "archive_days"),
    )  # 归档天数（导出 + Qdrant 物理删除；软删后再满 archive_days-decay_days 天）
    hot_max_entries: int = Field(
        default=80,
        validation_alias=AliasChoices("hotMaxEntries", "hot_max_entries"),
    )  # hot 层写入期条数硬上限（§5.4：达限拒绝写入转投 consolidate）
    # v1 写入判重阈值：v2 已移除写入时 LLM 判重，字段暂留避免旧配置文件报错
    shortDuplicatedScoreThreshold: float = Field(default=0.90)
    longMatchesScoreThreshold: float = Field(default=0.7)
    longMatchesTopK: int = Field(default=3)


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

class ModelsConfig(Base):
    name: str = Field(default="qwen3.8-flash")
    provider: str = Field(default="dashscope")

class Config(BaseSettings):
    agents: List[AgentConfig] = Field(default_factory=list)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    models:List[ModelsConfig] = Field(default_factory=lambda: [ModelsConfig()])
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

    def get_provider_by_model_name(self, model_name: str | None = None) -> ProviderConfig:
        normalized = str(model_name or "").strip().lower()
        if  normalized:
            for model in self.models:
                if normalized == str(model.name or "").strip().lower():
                    provider = model.provider
                    return getattr(self.providers, provider)
        return self.providers.custom


    def get_default_model(self) -> str:
        if not self.models:
            return  ModelsConfig().name
        return self.models[0].name  # 默认选第一个

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


config = load_config()

if __name__ == '__main__':
    config = load_config()
    print(config)