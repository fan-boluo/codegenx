from pydantic import BaseModel
from sqlalchemy.connectors import asyncio

from codegenx import __main__
from codegenx.ai_service.agent.action import Action
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.agent.tool_executor import ToolExecutor
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from codegenx.ai_service.system_app import get_app
from typing import Optional, Any

from shared.utils.log_util import log

class SqlExe(BaseModel):
    database: str = ""
    sql: str = ""


class SQLAction(Action):

    def act(self, ai_message: Any, **kwargs) -> Any:
        self.parse_ai_message(ai_message, SqlExe)


class DateExploreAgent(BaseReActAgent):
    """ 数据探索 """
    name = "data_explore"

    profile: PromptConfig = PromptConfig(
        role="Data Analyst",
        name="data_explore",  # 和config里面的对应
        goal="根据提供的database信息，使用正确的{{type}} SQL，分析用户输入",
        desc="数据数据库资源进行sql数据分析",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "请确保输出符合要求格式。每次分析**仅输出一条分析结果 SQL**，尽可能完整覆盖分析目标内容。",
            "若存在近期消息记录，分析时请参考历史回答与执行结果，**不要重复生成相同的错误答案**。请仔细校验，保证生成的 SQL 正确。"
            "必须严格遵守给定的数据结构定义，**禁止使用不存在的字段**；注意区分不同表的字段，不可混淆，支持多表关联查询。",
            "如果分析目标所需的数据、字段分布在多张不同表中，建议优先使用多表关联查询，并留意多张表结构之间的关联关系。",
            "**禁止自行编造数据作为查询条件**，仅能使用用户输入中提供的数据值作为查询条件。",
            "结果以表格形式输出"
        ],
        resource_prompt="你可以查询的database及数据表schema如下:{{database_info}}",
        expand_prompt="",
        examples="",
        out_schema="",
    )

    def __init__(self):
        super().__init__()
        self.actions = [SQLAction]

    def _prepare_prompt_params(self) -> dict:
        return {
            "type":"mysql",
            "database_info":
            """
            database:prj_ec (电商)
tables 建表语句:
CREATE TABLE users (
    user_id INT PRIMARY KEY,
    user_name VARCHAR(100) COMMENT '用户名',
    user_email VARCHAR(100) COMMENT '用户邮箱',
    registration_date DATE COMMENT '注册日期',
    user_country VARCHAR(100) COMMENT '用户国家'
) COMMENT '用户信息表';

CREATE TABLE products (
    product_id INT PRIMARY KEY,
    product_name VARCHAR(100) COMMENT '商品名称',
    product_price FLOAT COMMENT '商品价格'
) COMMENT '商品信息表';

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    user_id INT,
    product_id INT,
    quantity INT COMMENT '数量',
    order_date DATE COMMENT '订单日期',
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
) COMMENT '订单信息表';
            """
        }



    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        await super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")
        # tools = self.agent_config.tools or []
        # self.tools = await get_app().tool_registry.build_tools_with_name(tools)
        #
        # self.skill = None
        # self.memory = None






if __name__ == "__main__":
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState
    import asyncio
    def _make_session_state() -> RuntimeSessionState:
        """构造一个最小可用的 RuntimeSessionState（只需 session_id + request）。"""
        from codegenx.ai_service.schema.ai_schema import AiServiceGenerateRequest

        request = AiServiceGenerateRequest(
            appId="test-app",
            userId="test-user",
            message="测试消息",
            sessionId="test-session",
            traceId="t1",
            requestId="r1",
        )
        return RuntimeSessionState(session_id="test-session", request=request)

    async def test():

        agent = DateExploreAgent()
        agent.resolve_agent_config()

        await agent._prepare_agent(session_state=_make_session_state())

    asyncio.run(test())