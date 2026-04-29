from app.adapter.openai_adapter import OpenAIAdapter


class DeepSeekAdapter(OpenAIAdapter):
    supported_providers = {"deepseek"}
