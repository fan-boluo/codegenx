"""
codegenx.ai_service.memory — 记忆系统（跨会话持久记忆）。

与上下文工程的边界：
  - 会话内摘要（压缩边界）已移交 compact/session_summary.py，属于上下文工程；
  - 本包只负责跨会话的持久记忆：hot（核心约束，始终注入）+ warm（话题记忆，按需召回）。

分层数据模型（详见 docs/记忆系统详细设计方案.md）：
  models.py        记忆条目结构 + 类型权重 + ULID（json 事实源的行格式）
  paths.py         记忆目录/文件布局
  hot_store.py     hot.json 读写（始终注入，≤2K token）
  warm_store.py    warm_*.jsonl 滚动写入 / 增量扫描 / 软删除 / 归档（事实源）
  vector_store.py  warm_memories 向量检索层（Qdrant，可重建的加速层）
  embedding.py     向量化（DashScope 兼容模式）
  retriever.py     混合召回 + 三因子重排 + token 窗口（读取链路）
  memory_manager.py 门面：SessionContext 每轮调用组装注入
"""
