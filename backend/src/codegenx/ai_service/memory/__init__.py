"""
codegenx.ai_service.memory — 记忆系统（跨会话持久记忆，v2）。

与上下文工程的边界：
  - 会话内摘要（压缩边界）已移交 compact/session_summary.py，属于上下文工程；
  - 本包只负责跨会话的持久记忆：hot（核心约束，每轮全量注入）+ warm（历史经验，按需召回）。

数据架构（详见 docs/Agent记忆系统设计方案v2.md）：
  MySQL agent_memory 为唯一真源（hot/warm 同表分层 memory_layer=1/2），
  Qdrant agent_memory_warm 仅是 warm 层可重建检索索引，json 文件仅为迁移源。

模块地图：
  models.py        MemoryEntry + 类型字典/权重/半衰期 + ULID
  memory_store.py  agent_memory DAO（hot upsert / warm append / 生命周期 / 合规删除）
  hot_store.py     hot 层注入（token 预算 + 类型优先级裁剪，MySQL 直读）
  writer.py        在线/离线写入统一入口（准入校验 + 敏感信息过滤 + 向量同步队列）
  vector_store.py  agent_memory_warm 检索层（Qdrant 瘦 payload，点 id=自增主键）
  embedding.py     向量化（DashScope 兼容模式）
  retriever.py     混合召回（向量+关键词）+ 回表 + 三因子重排 + token 窗口
  memory_manager.py 门面：SessionContext 每轮调用组装注入（含 hot>warm 冲突规则）
  lifecycle.py     每日整理（warm 合并去重）+ 衰减归档（hot 豁免）
  sync.py          双数据源对账（正向补写 + 反向清幽灵，断点仅续跑用）
  compliance.py    合规删除级联（MySQL → Qdrant，scope=all/memory_id/subject/source_msg_id）
"""
