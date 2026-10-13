import json
from abc import ABC, abstractmethod
from typing import Any, Generic, Type, TypeVar, Union

from pydantic import BaseModel

# 允许 Action 解析出的目标类型：单个 BaseModel / BaseModel 列表 / None
T = TypeVar("T", bound=Union[BaseModel, list[BaseModel], None])


class ActOutput(BaseModel):
    """Action 链式执行的统一输入 / 输出类型。

    - 链路首环的输入由 BaseReActAgent.act 用 think() 的原始返回封装
      （success=True，output=<llm response dict>）。
    - 链路中上一环的返回直接作为下一环的输入，保证类型统一。
    - success=False 表示该 Action 执行/解析失败，BaseReActAgent 据此中断后续 Action。
    """

    success: bool = True
    output: Any = None   # 结构化结果：dict / BaseModel / list[BaseModel]
    message: str = ""    # 失败原因或文本摘要


class Action(ABC, Generic[T]):
    """act 方法要执行的动作基类。

    Action 是 BaseReActAgent.act 链式执行的单元：
    - 输入 ai_message 统一为 ActOutput（首环来自 think()，之后来自上一 Action 的返回）。
    - 子类覆盖 act 实现具体逻辑，覆盖 parse_ai_message 定制解析。
    - act 必须返回 ActOutput，并用 success 标记本次动作是否执行成功，
      失败时链路将中断，不再执行后续 Action。
    """

    @abstractmethod
    async def act(self, ai_message: ActOutput, session_state: Any = None, **kwargs) -> ActOutput:
        """执行具体的逻辑，返回统一的 ActOutput。

        Args:
            ai_message: 上游传入的 ActOutput（含 think() 原始返回或上一 Action 的结果）。
            session_state: 运行时会话状态（需要时可取 request/session 标识）。
            **kwargs: 其它附加参数。

        Returns:
            ActOutput（success 标记执行是否成功，output 为结构化结果）。
        """
        raise NotImplementedError

    def parse_ai_message(self, ai_message: ActOutput | None, cls: Type[T]) -> T | None:
        """解析 ai_message 成 cls 类型（子类可覆盖定制解析逻辑）。

        默认实现：从 ActOutput.output（dict 的 content 字段或纯字符串）中提取内容，
        json.loads 后 model_validate；解析失败返回 None。
        """
        if ai_message is None:
            return None
        # 兼容链中上一环直接传入的 ActOutput 或原始 llm response
        raw = ai_message.output if isinstance(ai_message, ActOutput) else ai_message
        content = ""
        if isinstance(raw, str):
            content = raw.strip()
        elif isinstance(raw, dict):
            content = str(raw.get("content") or "").strip()
        if not content:
            return None
        if isinstance(cls, type) and issubclass(cls, BaseModel):
            try:
                return cls.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValueError):
                # json 解析失败或 pydantic 校验失败（ValidationError 属于 ValueError）
                return None
        return None
