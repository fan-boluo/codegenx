---
name: 当前季度目标（2026 Q2）
description: 项目需要在 6 月底前完成记忆系统的 skill 化改造，7 月初交付 MVP
type: project
---

**事实**：记忆系统重构项目，核心目标是将主 agent 的长 prompt 中的记忆规则外置为 skill，减少主 prompt token 占用。

**Why:** 当前系统提示中记忆部分长达 1500-2000 tokens，用户希望降低主会话开销，同时使规则可热更新。项目 deadline 为 2026-06-30 完成设计，2026-07-07 交付可用版本。

**How to apply:** 在讨论或设计记忆相关功能时，优先考虑 skill 外置方案；评估技术方案时，需确保不增加主 agent 的额外工具调用延迟；提供的代码示例应兼容 `buildMemoryLines` 等现有接口。