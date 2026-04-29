from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.config.config import get_settings


@dataclass(slots=True)
class CodegenConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    prompt_dir: Path
    max_history_messages: int = 20

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self._normalized_path()}"

    def _normalized_path(self) -> str:
        settings = get_settings()
        path = settings.ai_chat_completions_path or "/v1/chat/completions"
        return path if path.startswith("/") else f"/{path}"


def get_codegen_config() -> CodegenConfig:
    settings = get_settings()
    prompt_dir = Path(__file__).resolve().parents[2] / "services" / "ai-service" / "prompt"
    return CodegenConfig(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        prompt_dir=prompt_dir,
    )
