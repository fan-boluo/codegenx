import json
from pathlib import Path
from typing import List

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
    model: str = "dashscope-qwen/qwen3.5-plus"
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_max_tokens: int = Field(
        default=8192,
        validation_alias=AliasChoices("contextMaxTokens", "context_max_tokens", "contextWindowTokens", "context_window_tokens"),
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
    max_tool_iterations: int = 40
    # Deprecated compatibility field: accepted from old configs but ignored at runtime.
    memory_window: int | None = Field(default=None, exclude=True)
    reasoning_effort: str | None = None  # low / medium / high — enables LLM thinking mode

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
    model_name: str | None = None
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
class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "brave"  # brave, tavily, duckduckgo, searxng, jina
    api_key: str = ""
    base_url: str = ""  # SearXNG base URL
    max_results: int = 5


class WebToolsConfig(Base):
    """Web tools configuration."""

    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class KBSearchConfig(Base):
    kb_name: str = "report"


class DataSearchConfig(Base):
    db_name: str = "test"


class ToolsConfig(Base):
    """Tools configuration."""
    websearch: WebToolsConfig = Field(default_factory=WebToolsConfig)
    kb_search: KBSearchConfig = Field(default_factory=KBSearchConfig)
    db_search: DataSearchConfig = Field(default_factory=DataSearchConfig)
    # exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    # restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    # mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


# memory----------------------------------
class MemorySearchConfig(BaseModel):
    """Memory search configuration - mirrors TS memorySearch (src/agents/memory-search.ts)"""
    enabled: bool = Field(default=True)
    # sources: list[Literal["memory", "sessions"]] = Field(default=["memory"])
    # extraPaths: list[str] = Field(default_factory=list)
    # provider: Literal["openai", "local", "gemini", "voyage", "mistral", "ollama", "auto"] = Field(default="auto")
    # remote: MemorySearchRemoteConfig | None = Field(default=None)
    # experimental: MemorySearchExperimentalConfig = Field(default_factory=MemorySearchExperimentalConfig)
    # fallback: Literal["openai", "gemini", "local", "voyage", "mistral", "ollama", "none"] = Field(default="none")
    # model: str = Field(default="")
    # local: dict[str, Any] | None = Field(default=None)
    # store: MemorySearchStoreConfig = Field(default_factory=MemorySearchStoreConfig)
    # sync: MemorySearchSyncConfig = Field(default_factory=MemorySearchSyncConfig)
    # query: MemorySearchQueryConfig = Field(default_factory=MemorySearchQueryConfig)
    # chunking: MemorySearchChunkingConfig = Field(default_factory=MemorySearchChunkingConfig)
    # cache: MemorySearchCacheConfig = Field(default_factory=MemorySearchCacheConfig)


class MemoryFlushConfig(BaseModel):
    """Memory flush configuration - mirrors TS memoryFlush (src/auto-reply/reply/memory-flush.ts)"""
    enabled: bool = Field(default=True)
    softThresholdTokens: int = Field(default=4000)
    forceFlushTranscriptBytes: int | None = Field(default=2 * 1024 * 1024)  # 2MB
    prompt: str | None = Field(default=None)
    systemPrompt: str | None = Field(default=None)


class MemoryConfig(Base):
    search: MemorySearchConfig = Field(default_factory=MemorySearchConfig)
    store: MemoryFlushConfig = Field(default_factory=MemoryFlushConfig)


class Config(BaseSettings):
    agents: List[AgentConfig] = Field(default_factory=list)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

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