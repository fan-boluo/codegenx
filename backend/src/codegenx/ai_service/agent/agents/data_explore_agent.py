from __future__ import annotations

import asyncio
import json as _json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from codegenx.ai_service.agent.action import Action, ActOutput
from codegenx.ai_service.agent.agents.agent import AgentMessage, NextMessageModel
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


class SqlActionInput(BaseModel):
    """SQL 分析动作的输入（LLM 返回内容的解析结果）。"""
    database: str = ""
    sql: str = ""


class SQLAction(Action[SqlActionInput]):
    """SQL 执行动作：解析 LLM 返回的 SQL JSON → 安全检查 → 执行 → 返回 rows。

    dev 环境走宿主机 MysqlManager 直连，非 dev 环境走 docker exec 容器内 mysql 客户端。
    """

    async def act(self, ai_message: ActOutput, session_state=None, **kwargs) -> ActOutput:
        parsed = self.parse_ai_message(ai_message, SqlActionInput)
        if parsed is None:
            return ActOutput(success=False, message="SQL 解析失败（非合法 JSON）")
        if not parsed.sql.strip():
            return ActOutput(success=False, message="SQL 为空，无法分析")

        # ── 统一的 SQL 安全检查（复用 MysqlManager 的只读守卫） ──
        from codegenx.ai_service.agent.db.mysql_manager import _check_sql_safety
        try:
            _check_sql_safety(parsed.sql)
        except ValueError as exc:
            log.warning("SQL 安全检查未通过: {}", exc)
            return ActOutput(success=False, message=f"SQL 不允许执行: {exc}")

        # ── 按环境分支执行 ──
        from shared import settings
        try:
            if settings.env == "dev":
                rows = await self._execute_local(parsed.database, parsed.sql)
            else:
                rows = await self._execute_in_docker(parsed.database, parsed.sql)
        except Exception as exc:
            log.error("SQL 执行异常: {}", exc)
            return ActOutput(success=False, message=f"SQL 执行失败: {exc}")
        log.debug("【SqlAction】 结果：{}", parsed.model_dump())
        return ActOutput(success=True, output={
            "database": parsed.database,
            "sql": parsed.sql,
            "rows": rows,
        })

    @staticmethod
    async def _execute_local(db_name: str, sql: str) -> list[dict]:
        """dev 环境：通过 MysqlManager 连接池直连。"""
        from codegenx.ai_service.agent.db.mysql_manager import get_mysql_manager
        mgr = get_mysql_manager()
        return await mgr.query(db_name, sql)

    @staticmethod
    async def _execute_in_docker(db_name: str, sql: str) -> list[dict]:
        """非 dev 环境：docker exec 容器内调用 mysql 客户端。"""
        from codegenx.ai_service.agent.db.docker_exec import execute_sql_in_docker
        from shared import settings
        return await execute_sql_in_docker(
            container=settings.sql_exec_docker_container,
            db_name=db_name,
            sql=sql,
            mysql_user=settings.userdb_mysql_user,
            mysql_password=settings.userdb_mysql_password,
            mysql_host=settings.userdb_mysql_host,
            mysql_port=settings.userdb_mysql_port,
        )


class DateExploreAgent(BaseReActAgent):
    """ 数据探索 """
    name = "data_explore"

    profile: PromptConfig = PromptConfig(
        role="Data Analyst",
        name="data_explore",  # 和config里面的对应
        goal="根据提供的database信息，使用正确的{{type}} SQL，分析用户输入",
        desc="根据数据库资源进行sql数据分析",
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
        out_schema="""请严格按如下JSON格式输出：
{
    "database": "要查询的database名称",
    "sql": "一条完整、可执行的SQL语句"
}
确保输出仅有JSON，无多余文字，可被python json.loads加载。""",
    )

    def __init__(self):
        super().__init__()
        # 注意：这里是实例而非类，act 是实例方法
        self.actions = [SQLAction()]
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
        """按 app 绑定的数据库实时查询 information_schema，拼出 database + 各表建表语句文本。

        绑定关系来源：app 表 dbName → gateway 注入请求 → session_state.db_name。
        元信息不冗余存储，以 information_schema 为单一事实源（与工具层同一查询通道）。
        """
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
        """观察 SQLAction 执行结果，设置下一跳。

        DataExploreAgent 是纯执行器：执行完就把结果返回给调用方（msg.sender），
        由调用方（通常是 Supervisor）决定总结给用户还是继续编排。

        ReAct 流程（通常 1 步）：
          step 1  think → LLM 生成 SQL JSON
                 act   → SQLAction 执行
                 ── 成功 → next_agent=msg.sender, success=True → 退出
                 ── 失败 → messages 追加错误, success=False → 继续 try（最多 max_steps 次）
        """
        msg.output = act_output.model_dump(mode="json")

        caller = msg.sender or "user"  # 调用方；没设就兜底 user

        if not act_output.success:
            # ── SQL 执行失败：喂回 messages 让 LLM 修正 SQL（最多 max_steps 次） ──
            log.warning("DataExploreAgent SQL 执行失败，让 LLM 重试：{}", act_output.message)
            self._messages.append({
                "role": "user",
                "content": (
                    f"上一次生成的 SQL 执行失败，请修正后重新输出 JSON。\n"
                    f"失败原因：{act_output.message}\n"
                    f"请重新生成正确的 SQL。"
                ),
            })
            msg.success = False
            return msg

        # ── SQL 执行成功：rows 截断后返回给调用方（大表避免塞爆 prompt） ──
        parsed = act_output.output or {}
        database = parsed.get("database", "")
        sql = parsed.get("sql", "")
        rows = parsed.get("rows", [])
        row_count = len(rows) if isinstance(rows, list) else 0

        # 最多传前 20 行给调用方；Supervisor 审查时根据 row_count 判断是否需要二次查询
        truncated = rows[:20] if isinstance(rows, list) else []

        payload = {
            "database": database,
            "sql": sql,
            "rows": truncated,
            "row_count": row_count,
            "truncated": row_count > 20,
        }

        log.info("DataExploreAgent SQL 执行成功 | 返回给 {} | rows={}{}",
                 caller, row_count, " (截断前20行)" if row_count > 20 else "")
        # message 必须携带真实执行结果（rows），供调用方（Supervisor 审查 / 历史
        # 记录 auto_plan_manager.add_agent_result）直接看到数据，metadata 保留结构化原文；
        # default=str 兜底 MySQL 返回的 Decimal/datetime 等非 JSON 原生类型
        rows_text = _json.dumps(truncated, ensure_ascii=False, default=str)
        truncation_hint = f"，数据较大仅返回前 {len(truncated)} 行" if row_count > 20 else ""
        msg.next = NextMessageModel(
            next_agent=caller,
            message=(
                f"任务：{msg.message.get("task")}"
                f"{self.agent_name} agent执行sql完毕，请核查。\n"
                f"SQL: {sql}\n"
                f"查询结果（共 {row_count} 行{truncation_hint}）：\n{rows_text}"
            ),
            metadata=payload
        )
        msg.success = True
        return msg



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
