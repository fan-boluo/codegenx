import json
from abc import ABC
from typing import Any, Type, TypeVar, Union

from pydantic import BaseModel

T = TypeVar("T", bound=Union[BaseModel, list[BaseModel], None])


class Action(ABC):
    """act 方法要执行的动作基类。

    Action 是 BaseReActAgent.act 链式执行的单元：
    - ai_message 从上游传入（首个 Action 收到 think() 的返回 {"content","tool_calls"}，
      后续 Action 收到上一个 Action 的返回值）。
    - 子类覆盖 act 实现具体逻辑，覆盖 parse_ai_message 定制解析。
    """

    async def act(self, ai_message: Any, **kwargs) -> Any:
        """执行具体的逻辑，返回执行结果。

        Args:
            ai_message: 上游传入的消息（LLM 返回或链中上一个 Action 的返回值）。
            **kwargs: 附加参数（如 session_state）。

        Returns:
            执行结果，传给链中的下一个 Action。
        """

    def parse_ai_message(self, ai_message: Any, cls: Type[T]) -> T:
        """解析 ai_message 成 cls 类型（子类可覆盖定制解析逻辑）。

        默认实现：若 cls 为 BaseModel 子类，尝试从 ai_message["content"]
        中解析 JSON 并 model_validate；其余情况返回 None。
        """
        if ai_message is None:
            return None
        if isinstance(cls, type) and issubclass(cls, BaseModel):
            content = ""
            if isinstance(ai_message, dict):
                content = (ai_message.get("content") or "").strip()
            elif isinstance(ai_message, str):
                content = ai_message.strip()
            if content:
                try:
                    data = json.loads(content)
                    return cls.model_validate(data)
                except (json.JSONDecodeError, ValueError, Exception):
                    pass
        return None
