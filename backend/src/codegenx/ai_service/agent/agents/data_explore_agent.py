from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from codegenx.ai_service.agent.action import ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage
from codegenx.ai_service.agent.agents.base_react_agent import BaseReActAgent
from codegenx.ai_service.prompt.prompt_manager import PromptConfig
from shared import log

if TYPE_CHECKING:
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState


# prompt 中注入的表数量上限，防止大库塞爆上下文
MAX_TABLES_IN_PROMPT = 50

_TABLES_META_SQL = (
    "SELECT TABLE_NAME AS table_name, TABLE_COMMENT AS table_comment "
    "FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
    "ORDER BY TABLE_NAME "
    f"LIMIT {MAX_TABLES_IN_PROMPT}"
)

_COLUMNS_META_SQL = (
    "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
    "COLUMN_TYPE AS column_type, COLUMN_COMMENT AS column_comment, "
    "COLUMN_KEY AS column_key "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = %s "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_FK_META_SQL = (
    "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
    "REFERENCED_TABLE_NAME AS ref_table, REFERENCED_COLUMN_NAME AS ref_column "
    "FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL"
)


class DateExploreAgent(BaseReActAgent):
    """数据探索（工具化 agent：自主调用 execute_sql 完成查询并作答）

    执行循环由 BaseReActAgent 的原生 tool_calls 循环承担：
    think →（LLM 发起 execute_sql）→ 工具结果回填 → 继续 think → … → 无工具调用时自由文本作答。
    简单查询一轮即可自总结答复，复杂任务多轮迭代。
    """

    name = "data_explore"

    profile: PromptConfig = PromptConfig(
        role="Data Analyst",
        name="data_explore",  # 和config里面的对应
        goal="使用 execute_sql 工具查询 {{type}} 数据库，基于真实查询结果回答任务并给出结论",
        desc="根据数据库资源进行sql数据分析",
        is_retry_chat=False,
        retry_goal="",
        retry_constraints=[],
        constraints=[
            "可用工具：execute_sql（执行只读 SQL）。数据库及表结构已在下方提供，直接编写 SQL 即可，无需再查询元信息。",
            "严格按表结构编写 SQL，禁止使用不存在的字段；注意区分不同表的字段，支持多表关联查询，留意表间外键关系。",
            "仅允许只读语句（SELECT/SHOW/DESC），禁止 INSERT/UPDATE/DELETE/DDL 等写操作。",
            "禁止自行编造数据作为查询条件，仅能使用任务中提供的数据值作为查询条件。",
            "查询结果行数较多时合理使用聚合或 LIMIT；一次查询不够时可分步多次查询。",
            "查询完成后必须基于工具返回的真实数据给出结论：直接回答任务问题，简明陈述关键数值与依据，禁止编造数值。",
            "若 SQL 执行报错，根据工具返回的错误信息修正后重试；多次失败则如实说明原因与已排除的方向。",
        ],
        resource_prompt="你可以查询的database及数据表schema如下:{{database_info}}",
        expand_prompt="",
        examples="",
        out_schema="最终答复直接用自然语言给出：先给结论，再列关键数据依据。不要输出 JSON，不要描述你的执行过程。",
    )

    def __init__(self):
        super().__init__()
        self._db_info = ""  # _prepare_agent 中填充

    def _prepare_prompt_params(self) -> dict:
        return {
            "type": "mysql",
            "database_info": self._db_info,
        }

    async def _prepare_agent(self, session_state):
        """ 准备agent所需的记忆，工具，skill，prompt 上下文等 """
        # db 元信息查询是 async，须在 super()（内部 build prompt）之前完成
        self._db_info = await self._query_available_db_info(session_state)
        await super()._prepare_agent(session_state)
        log.info(f"agent {self.agent_name} system_prompt: {self._system_prompt}")

    @staticmethod
    async def _query_available_db_info(session_state: RuntimeSessionState) -> str:
        """
        测试阶段为方便直接给结果
        """
        return """
database:prj_ec

tables 建表语句:

CREATE TABLE orders (
    order_id int,
    user_id int,
    product_id int,
    quantity int COMMENT '数量',
    order_date date COMMENT '订单日期',
    PRIMARY KEY (order_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
) COMMENT '订单信息表';

CREATE TABLE products (
    product_id int,
    product_name varchar(100) COMMENT '商品名称',
    product_price float COMMENT '商品价格',
    PRIMARY KEY (product_id)
) COMMENT '商品信息表';

CREATE TABLE users (
    user_id int,
    user_name varchar(100) COMMENT '用户名',
    user_email varchar(100) COMMENT '用户邮箱',
    registration_date date COMMENT '注册日期',
    user_country varchar(100) COMMENT '用户国家',
    PRIMARY KEY (user_id)
) COMMENT '用户信息表';
        """
    @staticmethod
    async def _query_available_db_info_bak(session_state: RuntimeSessionState) -> str:
        """按 app 绑定的数据库实时查询 information_schema，拼出 database + 各表建表语句文本。

        绑定关系来源：app 表 dbName → gateway 注入请求 → session_state.db_name。
        元信息不冗余存储，以 information_schema 为单一事实源（与工具层同一查询通道）。
        """
        # TODO 这种查询所有信息的太冗余了，只给出一些表来先，后续换成根据提示词从向量数据库查询可能涉及的表
        db_name = session_state.db_name
        if not db_name:
            return "（当前应用未绑定数据库，无法执行 SQL 分析）"

        from codegenx.ai_service.agent.db.mysql_manager import get_mysql_manager
        mgr = get_mysql_manager()
        tables, columns, fks = await asyncio.gather(
            mgr.query(db_name, _TABLES_META_SQL, (db_name,)),
            mgr.query(db_name, _COLUMNS_META_SQL, (db_name,)),
            mgr.query(db_name, _FK_META_SQL, (db_name,)),
        )
        if not tables:
            return f"database:{db_name}\n（库中暂无数据表）"

        cols_by_table: dict[str, list[dict]] = {}
        for c in columns:
            cols_by_table.setdefault(c["table_name"], []).append(c)
        fks_by_table: dict[str, list[dict]] = {}
        for f in fks:
            fks_by_table.setdefault(f["table_name"], []).append(f)

        parts = [f"database:{db_name}", "tables 建表语句:"]
        for t in tables:
            name = t["table_name"]
            col_defs = []
            pk_cols = []
            for c in cols_by_table.get(name, []):
                comment = f" COMMENT '{c['column_comment']}'" if c.get("column_comment") else ""
                col_defs.append(f"    {c['column_name']} {c['column_type']}{comment}")
                if c.get("column_key") == "PRI":
                    pk_cols.append(c["column_name"])
            if pk_cols:
                col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")
            for f in fks_by_table.get(name, []):
                col_defs.append(f"    FOREIGN KEY ({f['column_name']}) REFERENCES {f['ref_table']}({f['ref_column']})")
            table_comment = f" COMMENT '{t['table_comment']}'" if t.get("table_comment") else ""
            parts.append(f"CREATE TABLE {name} (\n" + ",\n".join(col_defs) + f"\n){table_comment};")
        return "\n\n".join(parts)

    async def observation(self, msg: AgentMessage, act_output: ActOutput, llm_response: dict[Any, Any]) -> AgentMessage:
        """观察最终答复：工具化循环结束后 LLM 的自由文本即结论，直接回传调用方。

        工具化模式下 act_output 只是 LLM 响应的封装（无 Action 链）；
        SQL 执行失败已通过 tool 消息回填由 LLM 自行修正重试，走到这里的
        都是已生成最终答复的正常路径。任务自包含由 Supervisor 派发保证，
        本 agent 只对自身任务负责，不感知总体目标。
        """
        final_text = str(llm_response.get("content") or "").strip()
        if not final_text:
            # 防御：run 循环已兜底空回复，正常不会走到这里
            msg.success = False
            return msg

        # 直答/回传路由：派发标记 direct_reply 且工具未全失败 → 直答用户；否则回传调用方
        target = self._resolve_final_target(msg)
        log.info("DataExploreAgent 完成 | 返回 {} | 答复长度={}", target, len(final_text))
        # 结论写进 result（回传跳 result 承载结果文本，task 保留原任务便于追溯）
        return self._reply(msg, target, final_text)



if __name__ == "__main__":
    from codegenx.ai_service.agent.runtime_schema import RuntimeSessionState

    def _make_session_state() -> RuntimeSessionState:
        """构造一个最小可用的 RuntimeSessionState（只需 session_id + request）。"""
        from codegenx.ai_service.schema.ai_schema import UserAIRequest

        request = UserAIRequest(
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
