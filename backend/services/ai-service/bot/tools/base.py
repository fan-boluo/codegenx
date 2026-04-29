import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Any

from pydantic import BaseModel

from bot.utils.log_utils import log


class Tool(BaseModel):
    name: str
    label: str  # 类别
    description: str
    parameters: dict[str, Any]  # 工具执行的参数
    executor: Callable  # 调用的函数


class ToolResult(BaseModel):
    success: bool  # 工具执行成果的标志
    data: Any  # 返回给llm的数据
    details :Any = None  # 其它执行的细节


class BaseTool(ABC):

    @property
    @abstractmethod
    def label(self) -> str:
        """
        类别
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:  # 工具执行的参数
        ...

    @abstractmethod
    async def execute(
            self,
            params: dict,
            signal: asyncio.Event | None = None,
            # on_update: Callable[[AgentToolResult[TDetails]], None] | None = None,
    ) -> ToolResult:
        """
        params:入参
        signal:终止信号
        on_update:???
        """
        ...

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """Validate tool parameters against JSON schema. Returns error list (empty if valid)."""
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")

        required = schema.get("required", [])
        for param in required:
            if param not in params:
                raise ValueError(f"Missing required parameter: {param}")

        # Basic type checking (simplified)
        properties = schema.get("properties", {})
        for key, value in params.items():
            if key not in properties:
                log.warning(f"Unknown parameter: {key}")
                continue

            prop_schema = properties[key]
            expected_type = prop_schema.get("type")

            # Basic type validation
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"Parameter {key} must be string, got {type(value).__name__}")
            elif expected_type == "integer" and not isinstance(value, int):
                raise ValueError(f"Parameter {key} must be integer, got {type(value).__name__}")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"Parameter {key} must be number, got {type(value).__name__}")
            elif expected_type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Parameter {key} must be boolean, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                raise ValueError(f"Parameter {key} must be array, got {type(value).__name__}")
            elif expected_type == "object" and not isinstance(value, dict):
                raise ValueError(f"Parameter {key} must be object, got {type(value).__name__}")

        return params
