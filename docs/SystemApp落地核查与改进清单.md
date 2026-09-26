# SystemApp 落地核查与改进清单

- 审查基线：worktree 已 rebase 对齐主工作区（b8be25a agent架构重塑）
- 对照物：`docs/SystemApp架构设计.md` v1.2 + 主工作区 `logs/app.log`（2026-09-25 23:00:40–23:11:05，完整启动 → 三次对话（user_0006 两次 / user_0003 一次）→ 关闭）
- 结论先行：**架构落地与设计方案高度一致，七步启动/逆序关闭/瘦身/换出/注册表校验/模型路由全部验证通过**；但发现 5 个代码 bug（其中 1 个使 P4 的 limits 覆盖完全失效、1 个使续写恢复路径必崩）、1 个资源空转问题、2 项功能在实测中失效（warm 记忆召回零注入 / chat_message 用量列全空）。
- 2026-09-26 补充：用户测试报告 chat_message 表 model/prompt_tokens/completion_tokens/finish_reason 全空（BUG-5）；深挖该链路连带发现 BUG-6（transport_attempts 未定义）与 F-5（token 估算失准）。

---

## 一、方案符合性核查（对照 v1.2 §3–§10）

| # | 设计条目 | 结论 | 证据 |
|---|---|---|---|
| 1 | §3 组合根七步启动、幂等、fail-fast | ✓ | 日志启动段顺序完全一致：hook 冻结（11 类事件）→ warm_memories 就绪 → 23 工具 → AgentRegistry 1 规格校验通过 → LLM 预热 qwen3.8-27b → runtime → 维护任务；`system_app.py` startup |
| 2 | §3 严格逆序关闭 | ✓ | 日志关闭段：maintenance → memory scheduler → session pool → LLM clients → shutdown completed |
| 3 | §4.2 SessionContext 纯状态瘦身 | ✓ | 只剩 ids/agent_name/system_prompt/chat_messages/prompts/compact_breaker/summary_state，行为全部委托 `get_app()` 服务 |
| 4 | §4.3 服务化五件套（ids 进签名） | ✓ | MemoryFacade/SessionPersistence/TaskBoardService/SessionSummaryService/CompactionService 均全局无状态，default_factory 装配 |
| 5 | §5 LLMFacade（预热/释放/观测） | ✓ | 预热日志「LLM 默认模型客户端已预热」；close_llm_clients 收进 shutdown；main.py lifespan 只剩薄壳 |
| 6 | §6 注册表复用（subagent child_view） | ✓ | `app.tools.child_view(excluded={"subagent","compact"}, allowed=...)`，不再 new ToolRegistry 重扫目录 |
| 7 | §7 P3 空闲换出（swap_idle_seconds=300） | ✓ | 「swapped out 3 idle sessions」——最后活跃 23:05:39 的会话 23:10:56 换出（5m17s > 300s），三个会话全部吻合；_is_quiescent 守卫在位 |
| 8 | §10.2 AgentRegistry fail-fast | ✓ | from_config + validate_against（未知工具/skill 名启动即拒），日志「1 个智能体规格校验通过: ['01']」 |
| 9 | §10.3 会话归属 agent_name | ✓ | runtime 从 req.metadata.agent_name 落 session.agent_name；build_system_prompt 读 spec 参数化 persona/skills/read_types |
| 10 | §10.4 四级模型路由（主路径） | ✓ | llm_recovery：primary_model + agent + agent_override 全链传入 resilient_invoke_stream |
| 11 | §10.5 记忆读策略 read_types | ✓（偏差②） | warm 层过滤生效（memory_manager.py 仅过滤 warm_entries） |
| 12 | 真实启动 P0 漏改修复（context 装配 / alive 端点） | ✓ | 三轮「system prompt init success」；router.py `/alive` 端点在位 |
| 13 | LLM 韧性层（P1 已合） | ✓ | conv1 两次 "Request timed out"（各 5s）→ 0.6s/1.0s 退避 → 第 3 次成功；[llm_call] 结构化日志齐全；首 chunk 前重试窗口按 P1-5 设计工作 |

---

## 二、代码 bug（本次审查新发现）

### BUG-1（P0）subagent_runner.py L49-67 重复构造块 → AgentSpec.limits 全库死代码

- 现象：L52-59 构造第一个 AgentRuntime 并应用 `spec.limits.max_steps`；L61-67 原样重复构造第二个 AgentRuntime **覆盖第一个**，limits 应用随之丢失，且白白多构造一次 runtime。
- 放大：grep 证实 `runtime.py`（主路径）完全不消费 `spec.limits`；llm_recovery 只传 model_override。即 **spec.limits（max_steps/max_tool_iterations/temperature 覆盖）在整个代码库零生效**——P4 §10.3/§10.6 的限额覆盖目前是空设计。
- 修法：删除 L61-67 重复块；再决策主路径是否消费 limits（见改进项 6）。

### BUG-2（P0）记忆 patrol 兜底空转死循环

- 现象（日志）：会话 0aa919cc（signals=0 tokens=269）与 c2881a34（signals=0 tokens=13）从启动第 1 分钟起，每轮 patrol（~60s）被「兜底补投 warm_extract」，任务 0.0s 完成但**计数永不清零，无限重复**。
- 根因链（三处代码共同作用）：
  1. `memory_task_store.py` scan_stale_pending：`pending_signals>0 OR pending_tokens>0` 且 `updated_at < NOW()-15min` 即命中；
  2. `schedule/memory.py` `_warm_extract_locked`：无新记录 / 无 assistant 收尾轮两条路径提前 `return 0, None`，**不调 advance_watermark**；
  3. advance_watermark 是唯一清零 pending_* 且刷新 updated_at 的地方 → 这些会话永久满足 stale 条件。
- 影响：每分钟 ≥2 个无效任务 + 日志噪音 + DB 轮询；每个「带未完成轮次的历史会话」都会加入该集合，随时间线性累积。
- 修法：no-op 提前返回处也清账——新增 `clear_pending(session_id)`（清零计数 + 刷新 updated_at），或直接 advance_watermark(当前 from_seq)。半截轮次场景安全：后续轮次完成时漏斗（signal/timeout 触发）会重新累加并投递；兜底的设计原意是「投递前崩溃」一次性补投（§4.1），补投过即应退场。

### BUG-3（P1）file.py L740-745 空目录 render 误报「路径解析失败」

- 日志证据：`list_directory 路径解析失败：code` 且 `state:"success"`——data 正确（「目录为空」），render 是复制粘贴错误。LLM 读 data 说对了（「目录目前是空的」），但前端 UI 展示错误文案。
- 修法：`render=f"{self.name} 目录为空：{absolute_path}"`。

### BUG-4（P2，轻微）两处日志小错

- `session_context.py` L52：`log.info(self.session_id,"SessonContext 启动完毕")`——第二参数被当 format 参数吞掉（日志只出 session_id），且 SessonContext 拼写错。应为 `log.info("{} SessionContext 启动完毕", self.session_id)`。
- `compact/micro.py` L100：`"single reuslt shreshold"` 缺 `{}` 占位符（threshold 值被吞）+ 拼写（shreshold→threshold、reuslt→result）。

### BUG-5（P1，用户测试补充）chat_message 表 model/prompt_tokens/completion_tokens/finish_reason 四列从未被写入

- 现象：这四列全空，含 role=assistant 的行。DDL（init/chat_message.sql L13-16）有列定义。
- 根因（两层断链）：
  1. `chat_message/store.py` append_message 的 INSERT 只有 9 列（uid/user/session/app/seq/role/content/content_bytes/content_tokens），四列不在列清单——**死列**；
  2. 数据上游其实都拿得到，但没接过来：
     - finish_reason：async_client.invoke_stream 末尾 `response_info` 已回传，llm_recovery 已提升进 round_response（日志 step 结果 `finish_reason: 'stop'` 可见）；
     - usage：invoke_stream 的 `captured_usage` **已采集成功**（日志 `[llm_usage] prompt_tokens=5626 completion_tokens=247`），但只喂了 `_record_usage`（指标），未 yield 给调用方；
     - model：AsyncLLMClient/resilience 已知实际服务模型（含 fallback 后的真实模型），同样未回传。
- 修法（纯管道接线，4 处）：
  1. invoke_stream 的 response_info 扩为 `{"finish_reason":…, "usage": {...} 或 None, "model": self.model_name}`；
  2. llm_recovery L98-101 同步提升 usage/model 进 round_response；
  3. runtime._execute_step L604-611：把 model/finish_reason/真实 usage（缺省回退本地估算）作为**独立 meta 参数**传 append_message——不要塞进 assistant_message dict（否则会随 assemble 回传 LLM，且冗余进 content 列 JSON）；
  4. store.append_message 增加可选 `meta` 参数，INSERT 补上四列（user/tool 行自然为 NULL）。

### BUG-6（P1）llm_recovery.py L112 引用未定义变量 transport_attempts → 续写恢复路径触发即崩

- L112 `continuation_attempts + compact_attempts + transport_attempts`——函数内从未定义 transport_attempts（韧性层接管传输重试后残留的旧变量名，全库仅此一处引用）。
- 输出截断（finish_reason=length/max_tokens）走进 Strategy 1 时立即 NameError，被外层 except 捕获后按 LOGIC 分类直接抛出——**续写恢复功能实际是坏的**。
- 修法：L112 删去 transport_attempts（与 L151 compact 分支的写法保持一致）。

---

## 三、日志功能性发现

### F-1（P0）warm 召回 3/3 全部超时 → warm 记忆层在实测中零注入

- 三轮对话全部「warm 召回超时(>500ms)，本轮降级为无 warm 记忆」。降级行为符合 §9 原则 1（不阻断对话）✓，但意味着**测试全程 warm 记忆一次都没注入成功**。
- 根因（`memory/retriever.py` search_warm）：
  1. 热路径 = embed_query（dashscope 网络往返）→ Qdrant search → MySQL 关键词通道 → 回表 → 重排，冷启动轻松超 500ms；
  2. **双通道串行**：`await _vector_channel()` 完成后才 `await _keyword_channel()`，本可 `asyncio.gather` 并行；
  3. warm_load_timeout_ms=500 对该链路过紧。
- 修法：① 双通道 gather 并行；② warm_load_timeout_ms 提到 1500–2000ms；③ 复测确认有「warm 注入成功」的日志证据。

### F-2（P1）conv1 流式首 chunk 两次超时——韧性层按设计工作，但 5s 预算偏紧

- 两次 "Request timed out"（5.06s/5.00s，来自 `cfg.llm_stream_timeout_seconds`），退避后第 3 次 4s 出首 chunk。P1-5 修复（首 chunk 前重试窗口）验证有效。
- 建议：qwen3.8-27b 首 chunk 常 >5s，llm_stream_timeout_seconds 提到 8–10s 可少一次重试往返。

### F-3（P1）maintenance 每周期 DB_cleanup_status=partial = 有监控表清理在报错

- `maintenance_service.py`：任一表清理抛异常 → overall=partial + 回滚。两个周期（23:05:56 / 23:10:56）均 partial，说明某个 monitor 表清理**持续失败**，但汇总日志没打出具体表名。
- 修法：汇总日志带失败表名与 errorMessage；排查缺表/权限。

### F-4（P2）环境与路由观察（非代码 bug）

- MySQL `readonly@localhost (using password: NO)`：prj_ec 只读凭据缺密码。Agent 处理优雅（说明原因并给建议）✓。补配置即可。
- 记忆提取 LLM 12–20s（后台任务不阻塞对话 ✓）；memory 场景当前与主模型同池（qwen3.8-27b），可考虑 model_roles.memory 指到更小模型。

### F-5（P1）token 估算对中文严重失准 → 压缩阈值口径失配

- 日志证据：同一轮 assembled messages，本地估算 prompt_tokens=798（LLM_Thinking_Start 事件），provider 真实 usage=5626（[llm_usage]）——**约 7 倍低估**。len//4 按 4 字符/token 校准，中文实际约 1-1.5 字符/token。
- 影响：AUTOCOMPACT_THRESHOLD = EFFECTIVE_CONTEXT_WINDOW − buffer（按模型真实窗口推导），但比较值是 len//4 估算口径 → 中文长会话压缩触发显著滞后，实际靠 CONTEXT_OVERFLOW 恢复路径（force_compact + 重试）兜底，每次多烧一次失败调用。
- 修法：估算器对 CJK 字符按 ~1 字符/token 计（收紧到 2-3 倍误差以内）；BUG-5 修好后可用真实 usage 定期校准估算系数。

---

## 四、设计偏差记录（需确认是否有意）

| # | 偏差 | 现状 | 建议 |
|---|---|---|---|
| ① | 写侧门控未实现 | MemoryPolicy.write_enabled/write_types 只在 agent_registry/config 定义，schedule/ 提取管道无任何消费；source_agent 元数据未落 | P4 收尾：提取任务携带 agent_name → write_memories 前按 spec.memory 门控 |
| ② | hot 层不做 read_types 过滤 | memory_manager 仅过滤 warm；注释声明「hot 层为核心约束不过滤」 | 与 §10.5 核对：若多智能体共享 hot 成立，记录为有意决策 |
| ③ | spec.limits 零生效 | 见 BUG-1：主路径不消费 + 子路径被覆盖 | 保留则主路径消费；不保留则从 AgentSpec 删除字段 |

---

## 五、已知坏测试（历史遗留，非本次引入）

- test_member_storage_isolation 5 例（JWTUser int IDs，ID 迁移字符串前缀进行中）
- test_runtime_optimization / test_session_pool* 引用已删除的 bot 模块

---

## 六、改进清单（按优先级）

**P0（功能失效 / 资源空转级）**
1. BUG-1：删除 subagent_runner.py L61-67 重复块
2. BUG-2：patrol no-op 提取清零 pending 计数，终结空转
3. F-1：warm 召回双通道并行 + warm_load_timeout_ms 调大 + 复测注入

**P1（体验 / 可观测 / 潜伏崩溃）**
4. BUG-5：chat_message 四列接线（response_info 扩 usage/model → append_message 增 meta 参数）
5. BUG-6：llm_recovery L112 删未定义的 transport_attempts
6. BUG-3：空目录 render 文案改正
7. F-5：token 估算器 CJK 校准（压缩阈值口径）
8. F-2：llm_stream_timeout_seconds 提到 8–10s
9. 偏差③决策：spec.limits 删除或主路径落地消费
10. F-3：maintenance partial 日志带表名 + 排查失败表
11. 偏差①：write_enabled/source_agent 写门控落地（P4 收尾）

**P2（打磨）**
12. BUG-4：两处日志修正（顺手）
13. F-4：readonly 凭据配置；memory 场景小模型路由
14. 坏测试清理（随 ID 迁移）
