这是对claud code某些模块的设计框架的记录


# Claude Code 的上下文压缩机制
核心思路：主动压缩 vs 被动无限增长
Claude Code 选择了**主动压缩（proactive compaction）*策略，在上下文溢出之前*就触发，而不是等到 API 返回 prompt_too_long 错误。这就是"永不溢出"的本质。

## 第一层：触发阈值计算
代码在 autoCompact.ts：

例如 Claude Sonnet (200K 窗口)：

effectiveWindow = 200,000 − 20,000 = 180,000
触发阈值 = 180,000 − 13,000 = 167,000 tokens
距离硬上限还有 33,000 tokens 的双重缓冲区，压缩过程本身有足够空间运行。

## 第二层：触发位置 — query loop 的每次迭代
在每个 LLM 调用之前（见 query.ts 第 454 行）：

即每次工具调用结果返回后、下一次 LLM 调用前都会检查，确保压缩机会不被错过。

## 第三层：两条压缩路径
路径 A：Session Memory 快速压缩（优先尝试）
见 sessionMemoryCompact.ts，在 autoCompactIfNeeded() 中优先尝试：

条件：session memory 文件必须已存在且非空。

路径 B：LLM 摘要压缩（回退路径）
见 compact.ts compactConversation()：

压缩前后的消息数量变化：几百条 → 十几条。

## 第四层：Prompt-Too-Long 兜底
如果压缩请求本身也撞上了 prompt_too_long（见 compact.ts truncateHeadForPTLRetry()）：

最坏情况最多重试 3 次，每次删掉更多头部历史。

## 第五层：电路断路器（Circuit Breaker）
防止已知失败的场景浪费 API 调用（见 autoCompact.ts）：

## 完整数据流
压缩后上下文的内容保证
压缩后 context 包含（buildPostCompactMessages()）：

内容	来源
compact_boundary 标记	元数据（时间戳、前/后 token 数）
摘要 user message	Sonnet 生成的 9-section 摘要
最近 N 条原始消息	保留完整的最新工具交互
最多 5 个读过文件的当前内容	createPostCompactFileAttachments()
plan / skill / tool-delta 附件	重新注入，防止模型"忘记"工具
这样模型压缩后仍然知道：当前任务、最近的操作、文件内容——只是丢失了久远的中间步骤。