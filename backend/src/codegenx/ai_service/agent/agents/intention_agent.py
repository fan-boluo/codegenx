from enum import Enum

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from shared import log


class IntentionType(str, Enum):
    """意图类型：四选一。"""
    GREETING = "greeting"                 # 打招呼 / 问候
    NEED_CONFIRM = "need_confirm"         # 信息有歧义，需向用户确认
    CALL_PLANNER = "call_planner"         # 正常业务问题，交给 Planner 规划
    UNABLE_ANSWER = "unable_answer"       # 无关 / 违规内容，直接拦截


class IntentionOutput(BaseModel):
    """意图识别输出（LLM 返回内容的解析结果）。"""
    intent: IntentionType
    ask_user: str = ""                    # 仅 need_confirm 时填写确认提问内容


class IntentionAction(Action[IntentionOutput]):
    """意图识别动作：解析 LLM 返回的 JSON 为 IntentionOutput，失败则中断动作链。"""

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, IntentionOutput)
        if parsed is None:
            return ActOutput(success=False, message="意图识别结果解析失败（非合法 JSON）")
        # mode="json" 将枚举值序列化为字符串，便于上游按字符串分情况处理
        return ActOutput(success=True, output=parsed.model_dump(mode="json"))


class IntentionRecognitionAgent(BaseReActAgent):
    """ 意图识别"""
    name = "intent_recognition"

    profile: PromptConfig = PromptConfig(
        role="intent_recognition",
        name="intent_recognition",
        goal="根据用户输入识别用户意图，选择匹配结果，并按要求输出信息。",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "只能从4种固定类型里选择结果，禁止自行新增其他意图类型。",
            "greeting：用户输入属于打招呼、问候类语句。"
            "need_confirm：用户的请求信息存在歧义，需要向用户确认信息，才能继续处理。"
            "call_planner：属于正常业务问题，交给Planner Agent进行任务规划处理。"
            "unable_answer：属于无关问题、违规非法内容，直接拦截，不可回答。"
            "只输出指定JSON，禁止额外文字解释，保证可以被python json.loads正常解析。",
            "ask_user字段：只有判定为need_confirm时填写确认提问内容；其余场景该字段填空字符串。",
        ],
        resource_prompt="",
        expand_prompt="",
        examples="",
        out_schema="""请严格按如下JSON格式输出：
    {
        "intent": "四选一：greeting / need_confirm / call_planner / unable_answer",
        "ask_user": "当intent=need_confirm时填写向用户确认的提问，其余情况填空字符串"
    }
    确保输出仅有JSON，无多余文字，可被python json.loads加载。"""
    )

    def __init__(self):
        super().__init__()
        # 注意：这里是实例而非类，act 是实例方法
        self.actions = [IntentionAction()]

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        await super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")
