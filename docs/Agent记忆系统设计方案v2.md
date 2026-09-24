# Agent 记忆系统设计方案 v2.1

> 版本：v2.1（整合修订版）
> 修订日期：2026-09-24
> 适用场景：多用户在线服务
> 存储栈：MySQL（真源）+ Redis（hot 缓存）+ Qdrant（warm 语义索引）+ 对象存储（归档 / 审阅快照）
> 本版重点：将方案表结构与现有库（`memory_task` / `memory_watermark` / `memory_sync_checkpoint`）整合，消除重复建设

---

## 0. 修订说明与差异总览

### 0.1 v2.0 的一句话概括

原方案的分层思路和"真相源 + 检索加速层"的双介质理念是正确的，v2.0 **不推翻架构**，只做三件事：

1. **把真相源从 JSON 文件换成 MySQL**——多用户在线服务下，文件做主存会在并发覆盖、多副本一致性、部分加载、合规删除四处踩坑。
2. **重新划分 hot / warm 的职责边界**——用户偏好、外部资源属于"必须确定性生效"的语义记忆，应上移到 hot 层；warm 层收敛为情景记忆。
3. **补齐原方案缺失的四块**——多租户隔离、容灾降级、合规删除、可观测性。

### 0.3 与原始方案（v1.0）的差异点对照表

按改造优先级排序。`工作量` 指相对原方案的增量改造成本。

| # | 维度 | 原设计 | 新设计 | 变更原因 | 工作量 | 优先级 |
|---|---|---|---|---|---|---|
| 1 | 真相源介质 | JSON 文件 | MySQL（`agent_memory` 表） | 文件无法做并发 upsert、多副本不同步、无法部分加载、合规删除脆弱 | 大 | P0 |
| 2 | 分层归属 | 用户偏好、资源路径在 warm | 用户偏好、资源路径、硬性规范全部上移到 hot | 这类记忆要求"确定性全量生效"，不能依赖概率性 top-k 召回 | 中 | P0 |
| 3 | warm 定位 | 主题记忆（混合了偏好与经历） | 收敛为情景记忆（带时间、只追加） | 两类记忆的更新语义完全不同，混在一层无法设计正确的失效策略 | 中 | P0 |
| 4 | 多租户隔离 | 未提及 | `app_id` + `user_id` 两级隔离，MySQL 索引前缀 + Qdrant `is_tenant` | 越权读到别人的记忆是安全事故级问题 | 小 | P0 |
| 5 | hot 去重方式 | 按主题语义召回 + LLM 判重 | 数据库唯一约束 `uk_slot` 做 upsert | 确定性去重不需要 LLM，也不会漏；LLM 判重降为异步兜底 | 小 | P0 |
| 6 | hot 裁剪策略 | "hot 截断后面的" | hot 层设写入期硬上限 + 触发压缩任务，**运行期不静默截断** | 强约束被静默丢弃 = 约束失效，这是正确性问题不是体验问题 | 中 | P0 |
| 7 | 跨层冲突消解 | 统一"以最新时间为准" | **hot 优先于 warm**，同层内才按时间 | warm 的旧情景记忆时间上可能更新，但不应推翻 hot 的硬约束 | 小 | P0 |
| 8 | hot 读取性能 | 每轮读文件 | Redis 缓存（key=user_id，写时 DEL） | 每轮必加载是全系统最高频读操作 | 小 | P1 |
| 9 | 向量库 payload | 未明确 | 只存 `memory_id / app_id / user_id / memory_type / status / created_at`，**正文不进 payload** | payload 参与过滤与加载，塞长文本直接推高内存成本 | 小 | P1 |
| 10 | 提炼触发 | "每轮结束后触发判断" + "每 10 轮触发一次提取"（两句矛盾） | 两级漏斗：每轮轻量信号判断 → 信号数或 token 量达阈值触发 LLM 提炼 | 每轮调 LLM 成本与延迟不可接受；固定 10 轮又会让记忆生效滞后；纯轮次计量对长短消息严重失真 | 中 | P1 |
| 11 | checkpoint 语义 | 一个同步 checkpoint 混用两件事 | 拆成两个：**提炼位点**（`memory_watermark.last_seq`）+ **同步状态**（`agent_memory.vector_synced_at`） | 两者语义不同，混用会导致重启后重复提炼或漏提炼 | 中 | P1 |
| 12 | 同步对账 | 定时比对，以文件为准补全缺失向量 | 双向对账：补写缺失向量 **+ 清理 MySQL 已 revoked / 已删除但向量库仍存在的记录** | 原方案只有单向补全，撤销的记忆会在向量库里变成幽灵数据 | 小 | P1 |
| 13 | 并发控制 | 分布式锁保护文件写 | 移除文件锁。MySQL 事务 + 唯一约束 + `memory_task.uk_idempotency` 替代 | 不再有多实例写同一文件的问题，锁是纯负担 | 小（减法） | P1 |
| 14 | 文件 MD5 监听 | 原方案已自行标注忽略 | 正式移除 | — | — | P1 |
| 15 | 最后访问时间更新 | 检索链路内同步更新 | 异步批量刷回（Redis 聚合 → 定时写 MySQL） | 同步写会让每次检索都带一次写操作，高并发下形成热点行 | 小 | P1 |
| 16 | 时间衰减公式 | 只说"新记忆权重更高" | 明确半衰期指数衰减：`decay = 0.5 ^ (age_days / half_life)` | 没有公式无法实现，也无法调参 | 小 | P1 |
| 17 | 重排位置 | 未明确 | Qdrant 召回 top-N（N≫k）→ **应用层重排** → 截 top-k | Qdrant 原生排序只按向量距离，不做 decay 加权 | 小 | P1 |
| 18 | 类型权重表 | 4 类（含 preference / resource_path） | 按新分层拆成两套：hot 用裁剪优先级，warm 用召回权重 | preference / resource_path 已上移到 hot，不再参与召回打分 | 小 | P1 |
| 19 | 类型可扩展 | "做成可扩展的"（未落地） | `memory_type_dict` 字典表 + 应用层校验，**不用 DB enum**；权重配置化 | enum 每加一类都要 DDL 变更；权重硬编码无法灰度调参 | 中 | P2 |
| 20 | hot 衰减 | 30 天未访问软删除（对 hot/warm 一视同仁） | **hot 层不做基于访问时间的衰减**，只靠"被推翻"失效；衰减只作用于 warm | hot 是全量加载不走召回，`hit_count` 不会增长，会导致偏好被静默删除 | 小 | P0 |
| 21 | 归档后召回 | 90 天未访问 → 对象存储 + 向量库物理删除 | 保留策略，但**必须显式决策**是否保留冷召回能力（见 6.3） | 彻底物理删除后，用户问及 3 个月前的事将永久召回不到 | 中 | P1 |
| 22 | 合规删除 | 未提及 | 新增完整级联删除流程（见 6.4） | 《个保法》要求可删除，且必须删干净（含派生向量与归档） | 中 | P0 |
| 23 | 容灾降级 | 未提及 | 新增各组件的降级策略（见第 9 章） | Qdrant 挂了不能让整个对话链路挂掉 | 中 | P1 |
| 24 | 可观测性 | 未提及 | 新增核心指标与告警清单（见第 10 章） | 记忆系统的问题（漏召回、脏记忆）不会报错，只表现为"助手变笨了" | 中 | P1 |
| 25 | 人工审阅 / 编辑 | 直接编辑 JSON 文件 | 文件降级为**只读导出视图**；编辑走管理接口写 MySQL | 直接改文件在新架构下不会生效，会造成"改了没用"的排查黑洞 | 中 | P2 |
| 26 | 文件 20M 切分 | 单文件上限 20M，超出新建 | 移除。MySQL 单表按时间分区，无需应用层切分 | — | — | P2 |
| 27 | token 计量 | 未提及 | `chat_message` 新增 `content_tokens`；`memory_watermark` 新增 `pending_tokens` | 提炼触发与窗口装填必须按 token 而非轮次计量 | 小 | P1 |

### 0.4 保留不变的部分

以下原方案设计合理，完整保留：

- **双介质理念**：真相源 + 检索加速层分离，写入以真源为准、检索走向量库。
- **最终一致性而非分布式事务**：向量库允许短暂落后，不影响正确性。
- **软删除优先**：不物理删原始数据，保障可回溯、可审计、可恢复。
- **重排序三路加权框架**：语义相似度 0.7 + 时间衰减 0.2 + 类型权重 0.1。
- **冷热分层 + 对象存储归档**的整体思路。
- **写入门槛**：只提取确定性、长效性信息；临时闲聊、不确定猜测、瞬时内容禁止写入。
- **冲突处理规则注入 Prompt** 的做法（仅调整优先级规则，见 5.5）。
- **`memory_task` 的 at-least-once + 退避重试状态机**：设计合理，仅增补幂等键。
- **`task_type` 枚举划分**：`warm_extract / consolidate / decay_archive / sync_check` 与本方案的四类异步任务天然对应。

---

## 1. 架构总览

### 1.1 存储分层

```
┌─────────────────────────────────────────────────────────────┐
│  Hot 层 · 结构化语义记忆（强约束 / 必然生效）                  │
│  真源：MySQL agent_memory (memory_layer=1)                    │
│  加速：Redis  mem:hot:{app_id}:{user_id}                      │
│  加载：每轮对话开始，全量 SQL 查询 → 注入 system prompt        │
│  不进向量库 · 无时间衰减                                       │
├─────────────────────────────────────────────────────────────┤
│  Warm 层 · 情景记忆（带时间 / 按需召回）                       │
│  真源：MySQL agent_memory (memory_layer=2)                    │
│  索引：Qdrant  agent_memory_warm                              │
│  加载：当前用户输入 → 向量召回 top-N → 应用层重排 → top-k      │
├─────────────────────────────────────────────────────────────┤
│  冷层 · 归档                                                   │
│  对象存储（OSS/S3）：90 天未命中的 warm 记忆 + 审阅快照         │
├─────────────────────────────────────────────────────────────┤
│  派生视图（不参与在线链路）                                     │
│  对象存储：每用户 hot 记忆的 markdown/JSON 快照，供人工审阅     │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 组件与数据流

```
                       ┌──────────────┐
   用户对话 ───────────▶│  对话服务     │
                       └──────┬───────┘
              ①加载 hot        │        ②召回 warm
        ┌─────────────────────┤
        ▼                     ▼
  ┌──────────┐         ┌─────────────┐      miss
  │  Redis   │────────▶│   MySQL     │◀────────────┐
  └──────────┘  miss   │ agent_memory│             │
                       └──────┬──────┘      ┌──────┴─────┐
                              │             │   Qdrant   │
                              │             └──────▲─────┘
                       ③消息落库                    │⑥异步同步
                              ▼                     │
                       ┌─────────────┐              │
                       │ chat_message│              │
                       └──────┬──────┘              │
                     ④更新水位 │                     │
                              ▼                     │
                    ┌──────────────────┐            │
                    │ memory_watermark │            │
                    │  last_seq        │            │
                    │  pending_signals │            │
                    │  pending_tokens  │            │
                    └────────┬─────────┘            │
                   ⑤阈值达成投任务                    │
                             ▼                      │
                    ┌────────────────┐              │
                    │  memory_task   │──── 提炼 Worker（LLM）
                    │ uk_idempotency │              │
                    └────────────────┘        写回 agent_memory
                                                     │
                                          memory_sync_checkpoint
                                            （对账进度，非正确性依据）
```

关键时序：

| 步骤 | 动作 | 同步/异步 | 失败影响 |
|---|---|---|---|
| ① | 加载 hot 记忆（Redis → MySQL） | 同步 | 降级：无 hot 约束，需告警 |
| ② | warm 向量召回 | 同步 | 降级：跳过 warm，对话继续 |
| ③ | 消息落 MySQL（含 `content_tokens`） | 同步 | 阻塞：必须成功 |
| ④ | 推进 `memory_watermark` 计数 | 同步（轻量） | 提炼滞后，不影响对话 |
| ⑤ | 阈值达成投 `memory_task` | 同步（轻量） | 兜底扫描补投 |
| ⑥ | Worker 提炼 + 写记忆 + 同步向量 | 异步 | 位点不前进，自动续跑 |

### 1.3 与原架构的核心区别

原方案是"**文件为主、向量库为辅**"；新方案是"**MySQL 为唯一真源，Redis 和 Qdrant 都是可重建的派生层**"。

这个定位带来的直接好处：Redis 丢了、Qdrant 索引重建、换 embedding 模型、换向量库厂商，都不影响数据正确性——全部可以从 MySQL 重放。原方案里文件损坏或并发覆盖是**不可恢复**的数据丢失。

---

## 2. 记忆分层职责与生命周期

### 2.1 Hot 层：结构化语义记忆

**定位**：高频、硬性、不可突破的约束与全局事实。**每轮必然全量生效**，不依赖召回。

| 类型 | 说明 | 示例 |
|---|---|---|
| `hard_constraint` | 项目硬性规范、业务强限制 | "所有金额计算保留 2 位小数" |
| `system_rule` | 系统 Prompt 基准规则 | "回复一律用中文" |
| `user_preference` | 用户明确表达的偏好 | "不要使用 emoji"、"代码用 TypeScript" |
| `identity_fact` | 用户身份与稳定事实 | "用户是后端工程师"、"用户位于河北保定" |
| `external_resource` | 资源指针 | "项目 A 的需求文档在 OSS 的 xxx 路径" |

**生命周期**：
- **创建**：LLM 提炼 + `uk_slot` upsert
- **更新**：同 `(app_id, user_id, subject, memory_type, slot_key)` 再次写入 → 新记录 active，旧记录 `status=2 superseded`
- **失效**：**只由"被推翻"触发，不由时间触发**。hot 层没有基于访问时间的衰减
- **删除**：仅合规删除或人工撤销

**体量控制**：写入期硬上限（建议 ≤ 80 条 / ≤ 2500 token，见 5.4）。达到上限触发 `consolidate` 任务（合并同类偏好、提升抽象层级），而不是运行期截断。

### 2.2 Warm 层：情景记忆

**定位**：带具体时间和上下文的一次性经历。数量随对话无限增长，只能按需语义召回。

| 类型 | 说明 | 示例 |
|---|---|---|
| `user_correction` | 用户对某次具体输出的纠正 | "2026-09-20 用户指出方案太啰嗦，要求先给结论" |
| `task_experience` | 历史任务的解法与结论 | "排查连接池泄漏，最终定位为 HikariCP maxLifetime 配错" |
| `project_background` | 项目的阶段性背景（非硬性规范） | "项目 A 正在做从单体到微服务的拆分" |
| `interaction_habit` | 交互习惯的观察 | "用户倾向于先看结论再看推导" |

**生命周期**：
- **创建**：只追加（append-only），不覆盖。发生过就是发生过
- **失效**：`status=3 revoked`（提炼出错、幻觉、用户要求撤销）
- **衰减**：召回打分时按 `COALESCE(last_hit_at, created_at)` 做时间衰减
- **软删**：30 天未命中 → `status=4 archived`
- **归档**：90 天未命中 → 导出对象存储 + Qdrant 物理删除（trade-off 见 6.3）

### 2.3 分层归属判定规则

新记忆片段产出时，按以下顺序判定。**这是 Worker 的硬编码规则，不交给 LLM 自由发挥**：

```
Q1  数量是否有上限（不随对话无限增长）？
      否 → warm
      是 → Q2
Q2  漏召回会不会导致行为错误？
      会 → hot
      不会 → Q3
Q3  会不会被新信息推翻、需要覆盖旧值？
      会 → hot
      不会 → Q4
Q4  是否绑定具体时间点或具体一次事件？
      是 → warm
      否 → hot
```

**边界案例约定**：

- "用户偏好简洁回复" → hot（稳定偏好，每轮必生效）
- "用户这次嫌回复太啰嗦" → warm（一次性反馈事件）
- 两者可以同时存在：warm 里累积多次同类反馈后，由 `consolidate` 任务提升为 hot 的一条偏好。**这是 warm → hot 的晋升通道，原方案没有，建议补上。**

### 2.4 记忆类型体系（可扩展）

原方案要求"记忆类型可扩展、支持 agent 自我进化"，落地方式为 `memory_type_dict` 字典表（DDL 见 3.1.1）：

- **不用数据库 enum**。`memory_type` 存 `VARCHAR(32)`，由字典表校验。新增类型 = 插一行数据，无需 DDL 变更
- **权重配置化**。召回权重与裁剪优先级存字典表，可在线调整、可灰度，不需要改代码发版
- **agent 自主扩展的类型**标 `is_builtin=0`，可被治理任务审计和回收
- **衰减半衰期按类型配置**，`half_life_d` 为 NULL 表示不衰减（hot 层一律 NULL）

---

## 3. 数据模型（整合版）

### 3.0 表清单与整合说明

| 表 | 来源 | 处置 |
|---|---|---|
| `memory_type_dict` | 新增 | 类型字典，支撑类型可扩展 + 权重配置化 |
| `agent_memory` | 新增 | **记忆主表，核心** |
| `memory_task` | 现有 | 增补 `idempotency_key` + `produced_count`，**吸收 v2.0 方案中 `memory_extract_batch` 的职责** |
| `memory_watermark` | 现有 | 增补 `pending_signals` / `pending_tokens` / `last_extract_at`，**吸收 v2.0 方案中 `memory_extract_cursor` 的职责** |
| `memory_sync_checkpoint` | 现有 | **语义修正**：从"同步水位"降级为"对账进度"，不再承担正确性保证 |


### 3.1 完整建表语句

```sql
-- ============================================================
-- Agent 记忆系统 DDL v2.1
-- 存储栈：MySQL(真源) + Redis(hot缓存) + Qdrant(warm语义索引) + OSS(归档)
-- 约定：id 类字段统一 VARCHAR(64)；时间统一 DATETIME(秒级)；
--       字符集 utf8mb4 / utf8mb4_unicode_ci；不使用外键
-- 建表顺序：memory_type_dict -> agent_memory -> memory_task
--           -> memory_watermark -> memory_sync_checkpoint
-- ============================================================


-- ------------------------------------------------------------
-- 1. 记忆类型字典
-- 目的：类型可扩展（agent 自我进化新增类型无需 DDL）+ 权重可在线调整
-- 约束：agent_memory.memory_type 存 VARCHAR 而非 enum，由本表做应用层校验
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_type_dict (
  app_id       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '空串=全局内置类型',
  type_code    VARCHAR(32)  NOT NULL COMMENT '类型编码',
  memory_layer TINYINT      NOT NULL COMMENT '1=hot 2=warm',
  display_name VARCHAR(64)  NOT NULL COMMENT '展示名',
  weight       DECIMAL(4,3) NOT NULL DEFAULT 0.500
               COMMENT 'warm=召回类型权重(占0.1那一路)；hot=裁剪优先级',
  half_life_d  SMALLINT UNSIGNED NULL
               COMMENT '衰减半衰期(天)，NULL=不衰减；hot 层一律 NULL',
  is_builtin   TINYINT      NOT NULL DEFAULT 0 COMMENT '1=内置，不可删改',
  enabled      TINYINT      NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (app_id, type_code),
  INDEX idx_layer (memory_layer, enabled)
) COMMENT '记忆类型字典（可扩展 + 权重配置化）' COLLATE = utf8mb4_unicode_ci;

INSERT INTO memory_type_dict
  (app_id, type_code, memory_layer, display_name, weight, half_life_d, is_builtin) VALUES
  ('', 'hard_constraint',    1, '硬性规范',   1.000, NULL, 1),
  ('', 'system_rule',        1, '系统规则',   1.000, NULL, 1),
  ('', 'user_preference',    1, '用户偏好',   0.900, NULL, 1),
  ('', 'identity_fact',      1, '身份事实',   0.800, NULL, 1),
  ('', 'external_resource',  1, '外部资源',   0.700, NULL, 1),
  ('', 'user_correction',    2, '用户纠正',   1.000,   60, 1),
  ('', 'task_experience',    2, '任务经验',   0.800,   90, 1),
  ('', 'project_background', 2, '项目背景',   0.600,   30, 1),
  ('', 'interaction_habit',  2, '交互习惯',   0.500,   30, 1);


-- ------------------------------------------------------------
-- 2. 记忆主表（hot / warm 同表分层）
-- 定位：本表是唯一真源；Qdrant 为可重建的派生索引，Redis 为可丢弃的缓存
-- 分层：memory_layer=1 hot  结构化语义记忆，每轮全量注入，不进向量库，无时间衰减
--       memory_layer=2 warm 情景记忆，append-only，进 Qdrant 语义召回，参与衰减
-- 去重：hot  靠 uk_slot 确定性 upsert，不调 LLM 判重
--       warm 不判重（重复反馈是晋升 hot 的依据），幂等由 memory_task 保证
-- 同步：vector_synced_at IS NULL 即待同步队列，行级状态、天然幂等、崩溃自动续跑
-- 归档：warm 30天未命中 -> status=4；90天 -> 导出 OSS + Qdrant 物理删除
--       hot 永不因时间失效，只由"被推翻"(status=2)或人工撤销(status=3)触发
-- 注意：active_slot 必须由应用层严格维护——status 从 1 改为 2/3/4 时，
--       同一事务内置 active_slot=NULL，否则该槽位永久无法写入新记忆。
--       建议封装为单一 markInactive() 方法，禁止业务代码直接 UPDATE status。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_memory (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY
                   COMMENT '自增主键，同时作为 Qdrant point id（Qdrant 不接受 ULID）',
  app_id           VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '应用id',
  user_id          VARCHAR(64)  NOT NULL COMMENT '用户id',
  memory_id        CHAR(26)     NOT NULL COMMENT '记忆业务id（ULID，单调可比）',

  memory_layer     TINYINT      NOT NULL COMMENT '1=hot 2=warm',
  memory_type      VARCHAR(32)  NOT NULL COMMENT '取值见 memory_type_dict.type_code',
  subject          VARCHAR(64)  NOT NULL DEFAULT 'self' COMMENT '记忆主体：self/项目A/某人',
  slot_key         VARCHAR(64)  NOT NULL COMMENT 'hot=属性名(如 no_emoji)；warm=memory_id',
  active_slot      TINYINT      NULL
                   COMMENT 'active时=1，非active时=NULL；配合uk_slot实现同槽位仅一条生效且保留历史',

  summary          VARCHAR(512) NOT NULL COMMENT '注入 prompt 的一句话摘要',
  content          JSON         NOT NULL COMMENT '记忆片段原文',
  token_cost       SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '注入占用token，写入时算好',

  source_type      TINYINT      NOT NULL DEFAULT 1 COMMENT '1=用户明说 2=模型推断 3=人工录入',
  confidence       DECIMAL(3,2) NOT NULL DEFAULT 1.00
                   COMMENT '置信度；模型推断须<0.70 且不得进 hot 层',

  status           TINYINT      NOT NULL DEFAULT 1
                   COMMENT '1=active 2=superseded 3=revoked 4=archived',
  superseded_by    CHAR(26)     NULL COMMENT '被哪条记忆取代',

  session_id       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '来源会话id',
  source_msg_ids   JSON         NULL COMMENT '溯源消息id数组，合规级联删除依赖此字段',
  task_id          BIGINT       NULL COMMENT '产生本条记忆的 memory_task.id',

  valid_from       DATETIME     NULL COMMENT '生效起始，NULL=立即生效',
  valid_to         DATETIME     NULL COMMENT '生效截止，NULL=长期有效',

  hit_count        INT UNSIGNED NOT NULL DEFAULT 0
                   COMMENT 'hot=注入次数，warm=召回命中次数；语义不同不可混比',
  last_hit_at      DATETIME     NULL COMMENT '最后命中时间，衰减与软删依据（异步批量刷回）',
  vector_synced_at DATETIME     NULL COMMENT 'NULL=待同步Qdrant；非NULL=已同步',

  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  UNIQUE KEY uk_memory_id (memory_id),
  UNIQUE KEY uk_slot (app_id, user_id, subject, memory_type, slot_key, active_slot),
  INDEX idx_hot_load     (app_id, user_id, memory_layer, status, memory_type),
  INDEX idx_warm_recall  (app_id, user_id, memory_layer, status, created_at),
  INDEX idx_sync_pending (vector_synced_at, status),
  INDEX idx_decay_scan   (memory_layer, status, last_hit_at),
  INDEX idx_session      (session_id, created_at),
  INDEX idx_task         (task_id)
) COMMENT '记忆主表（hot/warm 同表分层，MySQL 为唯一真源）'
  COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC;


-- ------------------------------------------------------------
-- 3. 离线任务队列
-- 状态机：pending -> running -> done
--   └-> 失败：retry_count+1，按退避(1m/5m/30m)回 pending；超限 -> dead
--   └-> 卡死回收：running 且 updated_at 超过 10 分钟 -> 巡检任务重置为 pending
--                （worker 存活期间应定期心跳 touch 该行）
-- 语义：at-least-once；重复投递由 uk_idempotency 兜底，不会重复执行
-- 幂等键约定：
--   warm_extract      {session_id}:{from_seq}:{to_seq}
--   consolidate       {app_id}:{user_id}:{yyyy-mm-dd}
--   decay_archive     {app_id}:{user_id}:{yyyy-mm-dd}
--   sync_check        {app_id}:{scope}:{yyyy-mm-dd}
--   conflict_detect   {app_id}:{user_id}:{yyyy-mm-dd}
--   compliance_delete {app_id}:{user_id}:{request_id}
--   vector_rebuild    {app_id}:{model_version}
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_task (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_type       VARCHAR(32)  NOT NULL
                  COMMENT '任务类型：warm_extract/consolidate/decay_archive/sync_check/conflict_detect/compliance_delete/vector_rebuild',
  idempotency_key VARCHAR(160) NULL
                  COMMENT '幂等键，同键仅允许一条；NULL=不参与幂等（如全局巡检任务）',
  app_id          VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '应用id',
  user_id         VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '用户id（全局任务为空串）',
  session_id      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '会话id（全局任务为空串）',
  status          VARCHAR(16)  NOT NULL DEFAULT 'pending'
                  COMMENT '状态：pending/running/done/dead',
  retry_count     INT          NOT NULL DEFAULT 0 COMMENT '已重试次数',
  produced_count  INT          NOT NULL DEFAULT 0 COMMENT '产出记忆条数，done 时回填',
  next_run_at     BIGINT       NOT NULL DEFAULT 0 COMMENT '最早可领取时间（unix秒），退避重试用',
  payload         JSON         NULL
                  COMMENT '任务参数与执行结果。warm_extract 入参{from_seq,to_seq}；出参{model,prompt_tokens,completion_tokens,cost_ms,memory_ids[]}，批次审计靠此字段承载',
  last_error      VARCHAR(2000) NULL COMMENT '最近一次失败原因',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uk_idempotency (idempotency_key),
  INDEX idx_status_next  (status, next_run_at),
  INDEX idx_session_type (session_id, task_type, status),
  INDEX idx_user_type    (app_id, user_id, task_type, status),
  INDEX idx_stuck        (status, updated_at)
) COMMENT '记忆离线任务队列（含提炼批次幂等与审计）' COLLATE = utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. 会话消息消费水位
-- 记忆提取按会话增量消费 chat_message 表，崩溃后从水位续提
-- 水位 = 已消费到的最大 seq（会话内单调递增，含）
-- 双路触发（满足任一即投 warm_extract 任务）：
--   pending_signals >= 5
--   pending_tokens  >= 3000
--   pending_signals >= 1 AND 距 last_extract_at > 30min
--   会话结束事件
-- 为什么不只按轮次：50 条"嗯"和 3 条长文档按轮次衡量完全失真，
--   轮次不是好的计量单位，必须叠加 token 量
-- idx_pending 用途：事件驱动投任务若因进程崩溃丢失，
--   定时兜底扫描 pending_signals>0 OR pending_tokens>0 的会话补投
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_watermark (
  session_id      VARCHAR(64) PRIMARY KEY COMMENT '会话id',
  app_id          VARCHAR(64) NOT NULL DEFAULT '' COMMENT '应用id',
  user_id         VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户id',
  last_seq        BIGINT UNSIGNED NOT NULL DEFAULT 0
                  COMMENT '已消费到的最大 seq（chat_message.seq，含）',
  pending_signals SMALLINT UNSIGNED NOT NULL DEFAULT 0
                  COMMENT '轻量信号累计命中轮数，规则/正则判断，不调 LLM',
  pending_tokens  INT UNSIGNED NOT NULL DEFAULT 0
                  COMMENT '累计未提炼的 content_tokens，提炼完成后归零',
  last_extract_at DATETIME NULL COMMENT '上次成功提炼时间，超时兜底触发依据',
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  INDEX idx_user    (app_id, user_id),
  INDEX idx_pending (pending_signals, pending_tokens)
) COMMENT '记忆提取会话消费水位（含双路触发计数）' COLLATE = utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. 双数据源对账进度
-- 【语义修正】旧设计 last_entry_id = "已同步到向量库的最大记忆id"，
--   存在高水位漏洞：异步同步会乱序完成，id=100 失败重试中而 101~200 已成功，
--   水位推到 200 后，100 被永久跳过；且 per-user 粒度会让一条失败卡住整个用户。
-- 新语义：last_entry_id = 上次对账扫描到的位置（长任务断点续跑用）。
--   同步正确性完全由 agent_memory.vector_synced_at IS NULL 保证，与本表无关。
-- 对账双向：
--   正向 missing —— MySQL 有(warm,active) / Qdrant 无 -> 补写
--   反向 ghost   —— Qdrant 有 / MySQL 无或 status!=1 -> 清理
--     （缺反向对账会让已撤销记忆变成幽灵数据，白占 top-k 名额）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_sync_checkpoint (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  app_id            VARCHAR(64) NOT NULL COMMENT '应用id',
  user_id           VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户id（全局对账为空串）',
  scope             VARCHAR(32) NOT NULL COMMENT '同步范围：warm',
  last_entry_id     VARCHAR(64) NOT NULL DEFAULT ''
                    COMMENT '上次对账扫描到的记忆id（ULID，单调可比），仅作断点续跑，不代表同步水位',
  last_reconcile_at DATETIME    NULL COMMENT '上次对账完成时间',
  scanned_rows      BIGINT      NOT NULL DEFAULT 0 COMMENT '上次对账扫描行数',
  missing_found     INT         NOT NULL DEFAULT 0 COMMENT 'MySQL有/Qdrant无，已补写条数',
  ghost_found       INT         NOT NULL DEFAULT 0 COMMENT 'Qdrant有/MySQL无或已失效，已清理条数',
  updated_at        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uk_user_app_scope (user_id, app_id, scope)
) COMMENT '记忆双数据源对账进度（不承担同步正确性保证）' COLLATE = utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. 会话消息表（依赖表，记忆提炼的数据源）
chat_message表的说明 init/chat_message.sql
-- 【seq 的作用，不可删】
--   1) 提炼位点载体：WHERE seq > last_seq 是 Worker 的增量扫描条件。
--      不能用 created_at（DATETIME 秒级，同秒必然撞；流式输出下 assistant
--      与 tool 消息几乎同时落库），也不能用自增 id（批量插入顺序不等于业务
--      顺序，分库分表后跨片无序）。排序不稳定会让 LLM 看到颠倒的因果，
--      提炼出的记忆是错的，且不报错、只表现为质量下降。
--   2) 提炼区间定义：memory_task.idempotency_key 的 from_seq/to_seq。
--   3) 客户端增量拉取：断线重连的稳定分页游标（created_at 分页会重复/遗漏，
--      OFFSET 分页在新消息插入时会错位）。
--   4) 并发正确性防线：uk_session_seq 保证同会话不会两条消息拿到同一序号。
--   生成方式：Redis INCR msg:seq:{session_id}（推荐），或 session 表 next_seq
--      字段原子自增。对话产品通常会话级串行，无需分布式发号器。
--
-- 【两套 token 字段的语义区别，易踩坑】
--   content_tokens   = 本条消息自身内容的 token，全 role 有值，写入时本地
--                      tokenizer 计算。用途：提炼触发计量 / 提炼窗口按 token
--                      装填 / 内容量归因。可跨行累加。
--   prompt_tokens    = 仅 assistant，该次 LLM 调用的输入总 token（含 system
--   completion_tokens  prompt + hot/warm 记忆注入 + 历史消息 + 本轮输入），
--                      取自 API response.usage。用途：计费对账 / 真实花费。
--   算钱看 prompt+completion 累加；算内容量看 content_tokens；两者不可替代。
--   prompt_tokens 每轮都含全部历史，跨行相加=真实账单（正确），但远大于
--   内容实际总量，拿它当"用户说了多少话"会高估数倍。
--   不可用 content_bytes 估算 token：UTF-8 中文约 3 字节/字、1~2 token/字，
--   英文约 4 字节/token，固定换算系数的偏差可达 2~3 倍。
--   成本归因须按 role 分组统计 content_tokens——tool 消息通常占大头。
-- ------------------------------------------------------------

```

### 3.3 建表注意事项

**`ROW_FORMAT = DYNAMIC` 不能省**：`uk_slot` 六列合计约 1156 字节，超过 InnoDB COMPACT 格式的 767 字节索引上限。DYNAMIC 下上限为 3072 字节，MySQL 5.7.9+ 默认即是，显式写上更保险。若仍报 `Specified key was too long`，把 `subject` 与 `slot_key` 缩到 `VARCHAR(32)`（合计降至约 644 字节）。

**`active_slot` 的维护是硬约束**：任何把 `status` 从 1 改为 2/3/4 的操作，必须在同一事务里把 `active_slot` 置 NULL。漏掉这一步，该槽位就永久无法写入新记忆（唯一约束持续冲突）。必须封装为单一 `markInactive()` 方法，禁止业务代码直接 UPDATE status。

**hot 加载的 filesort 可接受**：`ORDER BY` 的权重来自字典表而非本表列，会走 filesort。但 hot 单用户上限 80 条，且结果缓存进 Redis，排序成本可忽略，不需要为此在 `agent_memory` 冗余存 weight。

**`agent_memory.id` 作为 Qdrant point id 的前提是单库自增**。若后续分库分表，自增 id 会重复，需换成 snowflake 类全局整数（Qdrant 同样接受无符号整数）并做一次 point id 迁移。**若近期有分表计划，建议现在就用 `BIGINT UNSIGNED` + 应用层发号，省掉将来那次迁移。**

### 3.4 Qdrant Collection 设计

#### 3.4.1 创建

```json
PUT /collections/agent_memory_warm
{
  "vectors": { "size": 1024, "distance": "Cosine" },
  "on_disk_payload": true,
  "optimizers_config": { "indexing_threshold": 20000 }
}
```

- `size` 按实际 embedding 模型输出维度填，**不要硬编码猜测值**
- `distance` 必须与 embedding 模型训练时的度量一致
- `on_disk_payload: true`——payload 落盘控制内存占用（正文本来就不进 payload，这里是防御性配置）

#### 3.4.2 Payload 索引

```json
PUT /collections/agent_memory_warm/index
{ "field_name": "user_id",
  "field_schema": { "type": "keyword", "is_tenant": true } }

PUT /collections/agent_memory_warm/index
{ "field_name": "app_id", "field_schema": { "type": "keyword", "is_tenant": true } }

PUT /collections/agent_memory_warm/index
{ "field_name": "memory_type", "field_schema": "keyword" }

PUT /collections/agent_memory_warm/index
{ "field_name": "status", "field_schema": "keyword" }

PUT /collections/agent_memory_warm/index
{ "field_name": "created_at", "field_schema": "datetime" }
```

**`is_tenant: true` 是关键**。Qdrant 官方明确建议多租户场景用单 collection + payload 分区，而不是每租户一个 collection（每个 collection 有独立资源开销，Qdrant Cloud 默认每集群上限 1000 个 collection）。该标记会让 Qdrant 针对字段优化索引布局，租户过滤查询性能显著提升。未建索引的字段做过滤会退化成全量扫描。

#### 3.4.3 Point 结构

```json
{
  "id": 10086,
  "vector": [0.0123, -0.0456, "..."],
  "payload": {
    "memory_id":   "01J8ZK3M9P2Q4R5S6T7V8W9X0Y",
    "app_id":      "app_demo",
    "user_id":     "u_10086",
    "memory_type": "task_experience",
    "status":      "active",
    "created_at":  "2026-09-24T16:20:00Z"
  }
}
```

**point id 用 `agent_memory.id`（BIGINT），不用 ULID。** Qdrant 的 point id 只接受无符号整数或 UUID，26 位 Crockford base32 的 ULID 不合法。整数 id 比 UUID 更省空间，且现有 ULID 业务 id 体系完全不用改动——ULID 放在 payload 里用于回表。

**payload 只放指针和过滤字段，不放正文。** 召回后按 `memory_id` 回 MySQL 取 `summary` 和 `content`。三个理由：

1. payload 参与过滤和加载，长文本会显著推高内存成本
2. 正文的软删/撤销/审计都在 MySQL 做，payload 里存一份等于制造第二个真源
3. 回表本来就要做（需过滤 superseded、关联溯源），顺路取正文没有额外成本

### 3.5 Redis 缓存结构

| Key | 类型 | 内容 | TTL | 失效策略 |
|---|---|---|---|---|
| `mem:hot:{app_id}:{user_id}` | String(JSON) | 该用户全部 active hot 记忆（按裁剪优先级排好序） | 24h | 写入/更新/撤销时 **DEL**（不是 SET） |
| `mem:hit:{app_id}:{user_id}` | Hash | `memory_id` → 命中计数与时间 | 7d | 定时任务批量刷回 MySQL 后 DEL |
| `mem:extract:lock:{session_id}` | String | 提炼 Worker 互斥锁 | 300s | 自动过期 |
| `msg:seq:{session_id}` | String(计数) | 会话内 seq 发号器 | 会话生命周期 + 7d | — |

**写时 DEL 而不是写时 SET**：并发写场景下 SET 容易把旧值写回缓存造成脏数据，DEL 让下一次读触发重建，简单且正确。

**Redis 只做缓存，绝不做真源**。丢了从 MySQL 重建即可。`msg:seq` 是唯一例外——它是发号器，Redis 不可用时需降级为 `SELECT MAX(seq)+1 ... FOR UPDATE`，由 `uk_session_seq` 兜底防重。

---

## 4. 写入机制

### 4.1 提炼触发：两级漏斗

原方案"每轮结束后触发判断"和"每 10 轮触发一次提取"两句话是矛盾的，且纯轮次计量对长短消息严重失真。新方案明确为两级：

**第一级 · 轻量信号判断（每轮，成本极低，不调 LLM）**

每轮对话结束后，用规则 + 正则判断本轮是否含记忆信号：

- 偏好表达：`我喜欢` / `我习惯` / `不要` / `别用` / `以后都` / `记住`
- 纠正表达：`不对` / `应该是` / `我说的是` / `你搞错了` / `重新`
- 事实陈述：`我是` / `我在做` / `我们项目` / `我的`
- 资源指向：路径、URL、文件名的模式匹配

命中则更新水位表：

```sql
UPDATE memory_watermark
   SET last_seq = :new_seq,
       pending_signals = pending_signals + :signal_hit,
       pending_tokens  = pending_tokens  + :round_tokens
 WHERE session_id = :sid;
```

**第二级 · LLM 提炼（达阈值才触发）**

触发条件（满足任一）：
- `pending_signals >= 5`
- `pending_tokens >= 3000`
- `pending_signals >= 1` 且距 `last_extract_at` 超过 30 分钟
- 会话结束事件

触发后投任务（幂等键防重复投递）：

```sql
INSERT INTO memory_task
  (task_type, idempotency_key, app_id, user_id, session_id, status, next_run_at, payload)
VALUES
  ('warm_extract', CONCAT(:sid, ':', :from_seq, ':', :to_seq),
   :app, :uid, :sid, 'pending', 0, JSON_OBJECT('from_seq', :from_seq, 'to_seq', :to_seq));
-- 唯一约束冲突 => 该区间已投过任务，直接忽略
```

**兜底扫描**：若投任务前进程崩溃，定时任务扫 `idx_pending` 找出 `pending_signals > 0 OR pending_tokens > 0` 且超时未处理的会话补投。

**为什么这样设计**：每轮都调 LLM，成本和延迟都不可接受；固定每 10 轮触发，则大量无信息量的闲聊轮次被白白提炼，而关键信息又可能延迟生效。两级漏斗把 LLM 调用压缩到真正有信号的批次上，token 量这一路则保证长文档场景不会被轮次掩盖。

### 4.2 提炼 Worker 执行流程

```
① 领取任务
   UPDATE memory_task SET status='running'
    WHERE id = (SELECT id FROM memory_task
                 WHERE status='pending' AND next_run_at <= UNIX_TIMESTAMP()
                 ORDER BY next_run_at LIMIT 1)
   -- 实际用乐观锁或 SELECT ... FOR UPDATE SKIP LOCKED

② 抢会话锁  SET mem:extract:lock:{session_id} NX EX 300

③ 按位点读消息（读只读副本，走 idx_extract_scan）
   SELECT message_id, seq, role, content, content_tokens
     FROM chat_message
    WHERE user_id=? AND session_id=? AND seq > :last_seq
    ORDER BY seq
    -- 按 token 预算装填窗口，不按条数；累计到预算上限即止

④ 调 LLM 提炼，产出候选记忆片段
   准入校验：类型在字典表内 / 非敏感信息 / confidence 达标
   模型推断(source_type=2)的记录 confidence 必须 < 0.70 且不得进 hot 层

⑤ 按 2.3 判定规则分层写入 agent_memory
   hot  -> uk_slot upsert（见 4.3）
   warm -> append（见 4.4）

⑥ 推进水位 + 完成任务（同一事务）
   UPDATE memory_watermark
      SET last_seq=:to_seq, pending_signals=0, pending_tokens=0,
          last_extract_at=NOW()
    WHERE session_id=:sid;
   UPDATE memory_task
      SET status='done', produced_count=:n,
          payload=JSON_MERGE_PATCH(payload, :result_json)
    WHERE id=:task_id;

⑦ 失败处理
   status='pending', retry_count+1, next_run_at=UNIX_TIMESTAMP()+退避
   超过重试上限 -> status='dead' + 告警
   水位不前进 => 下次重试覆盖同一区间，uk_idempotency 保证不重复产出
```

**幂等的双重保障**：`memory_task.uk_idempotency` 阻止同区间重复投递；即使锁失效导致两个 Worker 同时执行，第二个在步骤⑥更新水位时会发现 `last_seq` 已前进，可判定为重复执行并丢弃结果。

### 4.3 提炼的准入门槛

保留原方案的严格要求，明确为 LLM 提炼 prompt 的硬约束：

**允许写入**：用户明确表达的偏好、用户对输出的纠正、项目规则、稳定的身份/资源信息、已完成任务的可复用结论。

**禁止写入**：
- 临时闲聊、情绪表达
- 模型自己的推测（`source_type=2` 的记录 `confidence` 必须 < 0.7，且不进 hot 层）
- 瞬时上下文（"刚才那个文件"这类无主体的指代）
- 敏感信息（密码、密钥、token、身份证号、银行卡号、完整手机号）——**必须在提炼 prompt 和入库校验两处都拦截**

### 4.4 Hot 层写入：确定性 upsert

```sql
-- 步骤 1：把旧的 active 记录置为 superseded（同时释放槽位）
UPDATE agent_memory
   SET status = 2, active_slot = NULL, superseded_by = :new_memory_id
 WHERE app_id=:app AND user_id=:uid AND subject=:subj
   AND memory_type=:type AND slot_key=:slot AND active_slot = 1;

-- 步骤 2：插入新的 active 记录
INSERT INTO agent_memory
  (app_id, user_id, memory_id, memory_layer, memory_type, subject, slot_key,
   active_slot, summary, content, token_cost, source_type, confidence,
   status, session_id, source_msg_ids, task_id)
VALUES
  (:app, :uid, :new_memory_id, 1, :type, :subj, :slot,
   1, :summary, :content, :token_cost, :src_type, :conf,
   1, :sid, :msg_ids, :task_id);

-- 两步必须在同一事务内
-- 步骤 1 的 active_slot=NULL 是关键：MySQL 唯一索引允许多行 NULL 共存，
-- 于是 superseded 历史可无限保留，同时 active 槽位始终唯一
```

写入后：`DEL mem:hot:{app_id}:{user_id}`

**hot 层不做语义判重、不同步调 LLM 判矛盾**。`uk_slot` 已保证同属性只有一条 active，这是确定性的、零成本的、不会漏的。

`slot_key` 的生成规则需明确（否则同一属性会因命名不同产生多条）：
- 由提炼 LLM 输出，但必须从**预定义的 slot 词表**里选（词表存字典表或配置）
- 词表外的新 slot 标 `source_type=2` 且低置信度，累计出现 3 次以上才转正

### 4.5 Warm 层写入：append-only

```sql
INSERT INTO agent_memory
  (app_id, user_id, memory_id, memory_layer, memory_type, subject, slot_key,
   active_slot, summary, content, token_cost, source_type, confidence,
   status, session_id, source_msg_ids, task_id, vector_synced_at)
VALUES
  (:app, :uid, :mid, 2, :type, :subj, :mid,   -- slot_key 填 memory_id 自身
   1, :summary, :content, :token_cost, :src_type, :conf,
   1, :sid, :msg_ids, :task_id, NULL);        -- NULL = 待同步 Qdrant
```

warm 层**不做语义判重**。理由：情景记忆是一次性事件，"用户上周抱怨方案啰嗦"和"用户这周又抱怨方案啰嗦"是两条独立的有效记忆，不是重复——它们的累积恰恰是晋升为 hot 偏好的依据（见 2.3）。幂等由 `memory_task.uk_idempotency` 保证（同区间不重复提炼），不需要在记忆层面判重。

**这是相对原方案的一个重要简化**：原方案"写入时先按主题召回，用大模型判断是否重复/矛盾"，会让每次写入都串行依赖一次向量检索 + 一次 LLM 调用，写入链路又慢又不稳定，且判重结果不可复现。

### 4.6 矛盾检测：降级为异步兜底

LLM 语义矛盾检测**保留，但移出写入主链路**，落到 `conflict_detect` 任务：

- **触发**：每日定时，按用户投递任务（幂等键 `{app_id}:{user_id}:{yyyy-mm-dd}`）
- **范围**：扫描单个用户全部 active hot 记忆（体量小，可全量两两比对）
- **判断**：是否存在语义冲突（如"喜欢简洁" vs "希望详细解释"）
- **处理**：命中冲突 → 保留 `created_at` 更新的一条，旧的走 `markInactive()` 置 superseded，写审计日志
- **不阻塞**：检测结果不影响写入，写入永远是快的

跨层冲突（hot vs warm）不靠这个任务解决，靠 5.5 的 Prompt 规则。

### 4.7 向量库同步

```
同步 Worker（独立进程，可水平扩展）
  循环：
    SELECT id, memory_id, app_id, user_id, memory_type, summary, status, created_at
      FROM agent_memory
     WHERE vector_synced_at IS NULL AND memory_layer = 2
     ORDER BY id LIMIT 500
    ↓
    批量 embedding（一次 API 调用打多条，禁止逐条调用）
    ↓
    Qdrant 批量 upsert（point id = agent_memory.id）
    ↓
    UPDATE agent_memory SET vector_synced_at = NOW() WHERE id IN (...)
```

要点：
- **批量**：embedding 和 Qdrant upsert 都必须批量。逐条调用会让同步吞吐低一个数量级
- **`vector_synced_at IS NULL` 就是队列**，不需要 MQ，不需要全局游标。天然幂等，Worker 崩溃重启自动续跑
- **status 变更也要触发同步**：`markInactive()` 里应把 `vector_synced_at` 重置为 NULL，让 Worker 把新的 status 推到 Qdrant payload
- **Qdrant 写失败**：不更新 `vector_synced_at`，下一轮自动重试；连续失败 N 次告警
- **hot 层记忆不进向量库**（`memory_layer=2` 条件），这是分层设计的直接体现

---

## 5. 检索机制

### 5.1 双路加载

```
① Hot 路（必然生效，不打分）
   GET mem:hot:{app_id}:{user_id}
   miss → SELECT memory_id, memory_type, summary, token_cost
            FROM agent_memory
           WHERE app_id=? AND user_id=? AND memory_layer=1 AND status=1
             AND (valid_to IS NULL OR valid_to > NOW())
           ORDER BY <字典表 weight> DESC, updated_at DESC
   → SETEX mem:hot:{app_id}:{user_id} 86400 <json>
   → 全量注入 system prompt

② Warm 路（按需召回，参与打分）
   embed(当前用户输入)
   → Qdrant search top-N (N=20~30)
       filter: app_id=? AND user_id=? AND status=active
   → 应用层重排（见 5.2）
   → 截 top-k (k=5~8)
   → 按 memory_id 回 MySQL 取 summary/content
       同时过滤 status != 1 的记录（Qdrant payload 可能滞后）
   → 注入上下文
```

**Qdrant 的 status 过滤是"尽力而为"**：payload 更新有异步延迟，所以回表后必须在 MySQL 侧再过滤一次 `status=1`。这是双介质架构必须接受的细节。

### 5.2 Warm 层重排

保留原方案的三路加权框架，补齐可实现的定义：

```
final_score = 0.7 × sim_norm + 0.2 × decay + 0.1 × type_weight
```

**语义相似度 `sim_norm`**：Qdrant 返回的 score。Cosine 距离下取值 [-1,1]，归一化到 [0,1]：`sim_norm = (score + 1) / 2`。

**时间衰减 `decay`**：原方案只说"新记忆权重更高"，没给公式，无法实现。明确为半衰期指数衰减：

```
age_days = (now - COALESCE(last_hit_at, created_at)) / 86400
decay    = 0.5 ^ (age_days / half_life)
```

`half_life` 从 `memory_type_dict.half_life_d` 读取，默认 30 天，按类型分别配置（`user_correction` 60 天，`task_experience` 90 天）。用 `COALESCE(last_hit_at, created_at)` 而非只用 `created_at`——被反复命中的记忆应保持热度。

**类型权重 `type_weight`**：从字典表读取，warm 层初始值见 3.1.1 种子数据（`user_correction` 1.0 / `task_experience` 0.8 / `project_background` 0.6 / `interaction_habit` 0.5）。原方案的 `user_preference` 0.8 和 `resource_path` 0.5 已上移到 hot 层，不再参与召回打分。

**为什么必须在应用层重排**：Qdrant 原生排序只按向量距离，不做 decay 加权。所以要先召回 top-N（N 明显大于 k，建议 N=20~30），在应用层算完 `final_score` 再截 top-k。如果只召回 k 条再重排，等于没重排——真正该上榜的记忆根本没进候选集。

### 5.3 命中记录：异步刷回

原方案"检索时更新片段的最后访问时间"是在检索链路里做写操作，高并发下形成热点行更新，拖慢每次查询。改为：

```
检索命中 → HINCRBY mem:hit:{app_id}:{user_id} <memory_id> 1
         → 时间戳写入同一 Hash 的伴生字段
定时任务（每 5 分钟）→ 批量 UPDATE agent_memory
                        SET hit_count = hit_count + ?, last_hit_at = ?
                      WHERE memory_id = ?
         → DEL mem:hit:{app_id}:{user_id}
```

代价是 `last_hit_at` 有最多 5 分钟延迟——对衰减计算完全可接受。

### 5.4 Token 预算与裁剪

原方案的 `hot 最多 xxx token` 是占位符，且"hot 截断后面的"存在严重隐患。新方案：

**预算分配（建议初始值，需按实际模型上下文压测调整）**：

| 层 | 预算 | 说明 |
|---|---|---|
| hot | ≤ 2500 token | 强约束层，目标是不触发裁剪 |
| warm | ≤ 1500 token | 按 final_score 从高到低累加 |
| 合计 | ≤ 4000 token | 占 32k 上下文的 12.5% |

**Warm 层裁剪**：按 `final_score` 降序累加 `token_cost`，超预算即停止。这是安全的——warm 本来就是概率性召回，少一条不影响正确性。

**Hot 层裁剪：运行期不静默截断。**

原方案"hot 截断后面的"意味着某条硬约束可能被悄悄丢掉，而系统毫无感知——这是正确性事故，不是体验降级。新方案：

1. **写入期控制**：hot 层设条数硬上限（建议 80 条）。达到上限时**拒绝直接写入**，转投 `consolidate` 任务
2. **压缩任务**：把多条同类记忆合并成一条更抽象的（如 5 条代码风格偏好 → 1 条"代码风格规范"），或把长期低优先级的降级到 warm
3. **运行期兜底**：万一仍超预算，按 `weight DESC, updated_at DESC` 保留高优先级部分，但**必须打 ERROR 级日志 + 告警指标**，让运维知道 hot 约束正在失效
4. **监控 `hot_token_usage`**：常态 > 80% 预算就该扩容或触发压缩，而不是等到溢出

### 5.5 冲突消解

原方案"以最新时间的为准"在跨层场景会出错：warm 里一条昨天的情景记忆，时间上比 hot 里三个月前的硬约束更新，按时间优先就会推翻硬约束。

新方案改为**两级优先**：

```
1. 层级优先：hot > warm
   hot 是强约束层，warm 是参考信息层，warm 永远不推翻 hot
2. 同层内时间优先：created_at 更新的胜出
```

Prompt 注入的规则文案相应调整为：

> 记忆冲突处理规则：
> - 核心约束（hot）与历史经验（warm）冲突时，一律以核心约束为准
> - 同一层级内出现冲突时，以时间更新的记忆为准
> - 若发现记忆与用户当前明确指令冲突，以用户当前指令为准

第三条是原方案没有的，但很必要——用户当下说的话永远优先于历史记忆。

---

## 6. 衰减、删除与归档

### 6.1 Hot 层：不做时间衰减

**这是相对原方案最重要的修正之一。**

原方案"30 天未访问软删除"对 hot / warm 一视同仁，但 hot 层存的是硬约束和用户偏好——"用户不要 emoji"这条可能连续 30 天没有被任何检索"命中"（因为 hot 是全量加载，不走召回，`hit_count` 根本不会增长），然后被静默软删。约束就这么丢了，而且没人知道。

| 层 | 时间衰减 | 失效方式 |
|---|---|---|
| hot | **无** | 只由"被推翻"（`superseded`）或人工撤销（`revoked`）触发 |
| warm | 有，参与召回打分 | 30 天未命中 → `archived`；90 天未命中 → 导出归档 |

hot 层需要的是**治理**而不是衰减：定期跑 `consolidate` 合并冗余、跑 `conflict_detect` 清理冲突。

同时，hot 记忆的 `hit_count` 应统计"被注入 prompt 的次数"，而不是"被召回的次数"——否则这个字段对 hot 永远是 0，无法用于治理决策。这也是 DDL 注释里标注"hot 与 warm 的 hit_count 语义不同、不可混比"的原因。

### 6.2 Warm 层：衰减与软删

保留原方案策略，明确执行细节，落到 `decay_archive` 任务：

```sql
-- ① 软删候选（分批，避免长时间持锁）
UPDATE agent_memory
   SET status = 4, active_slot = NULL, vector_synced_at = NULL
 WHERE memory_layer = 2 AND status = 1
   AND COALESCE(last_hit_at, created_at) < DATE_SUB(NOW(), INTERVAL 30 DAY)
 LIMIT 5000;
-- vector_synced_at 置 NULL => 自动进入同步队列，把新 status 推到 Qdrant
-- 检索时过滤 status!=1，但数据仍在，可恢复

-- ② 归档（status=4 且再满 60 天，即累计 90 天未命中）
--    导出 JSON 到对象存储：oss://memory-archive/{app_id}/{user_id}/{yyyy}/{mm}.jsonl
--    Qdrant delete by filter: {"points": [<id>, ...]}  或按 memory_id 过滤
--    MySQL 保持 status=4，content 可迁冷表或保留
```

**分批 + LIMIT**：全表扫描式的批量 UPDATE 会长时间持锁，必须分批。

**软删可恢复**：用户重新提到相关话题时，如果归档记忆被命中（通过管理后台或冷检索），可恢复为 active（同时 `active_slot=1`）。这是软删相对物理删的核心价值。

### 6.3 归档后能否召回：必须显式决策

原方案"90 天未访问 → 从向量库物理删除"有一个没被讨论的后果：**用户问及 3 个月前的事，系统永久召回不到。**

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A. 彻底归档**（原方案） | 主 collection 物理删除，只留对象存储 JSON | 主索引规模可控，检索性能稳定，成本最低 | 90 天以上记忆无法语义召回，只能通过管理后台按用户翻查 |
| **B. 冷 collection** | 迁入 `agent_memory_warm_cold`（独立 collection，`on_disk` 索引，不参与常规检索） | 需要时可显式查冷层（主层无结果时兜底查一次） | 多维护一个 collection，冷查询延迟高，成本上升 |

**建议**：先上 A（简单、够用），把"冷层召回"作为 P2 观察项。上线后监控指标 `memory.warm.recall_empty_but_historical`（主层召回为空但用户明显在指代历史的比率）。若持续 > 5%，再上 B。

不要一开始就做 B——冷 collection 的维护成本是真实的，而收益未经数据验证。

### 6.4 合规删除（原方案完全缺失，P0）

《个人信息保护法》要求个人信息可删除，且派生数据要一并清理。记忆系统是典型的"派生数据放大器"——一条消息可能派生出多条记忆、多个向量、多份归档。落到 `compliance_delete` 任务：

```
收到删除请求（用户自助 / 客服工单 / 监管要求）
  ↓
① 确定范围
   - 删单条记忆：按 memory_id
   - 删某主题：按 subject
   - 删某来源消息的派生记忆：按 source_msg_ids 反查
   - 删用户全部：按 app_id + user_id
  ↓
② MySQL 侧
   - 硬删（监管要求）或 status=3 + 内容脱敏（业务需留审计痕迹时）
   - 两种情况都必须 active_slot=NULL
   - 写 deletion_audit_log（谁、何时、删了什么范围、依据什么）
  ↓
③ Qdrant 侧（原生支持按 filter 删除，一个请求搞定，无需先 scroll 出 id）
   POST /collections/agent_memory_warm/points/delete
   { "filter": { "must": [
       { "key": "app_id",  "match": { "value": "app_demo" } },
       { "key": "user_id", "match": { "value": "u_10086" } }
   ]}}
  ↓
④ Redis 侧
   DEL mem:hot:{app_id}:{user_id}   mem:hit:{app_id}:{user_id}
  ↓
⑤ 对象存储侧
   - 删除归档文件（或按保留策略标记待删）
   - 删除审阅快照
  ↓
⑥ 原始消息
   chat_message 按独立策略处理（通常也需删除或匿名化）
  ↓
⑦ 校验（异步）
   Qdrant 按 app_id+user_id 查询应返回 0 条；MySQL 应无 active 记录
   不一致则告警 + 重试
```

**关键点**：`source_msg_ids` 字段是这套流程能跑通的前提——没有它，无法从"某条消息"反查到"由它派生的所有记忆"。这个字段在原方案的 JSON 结构里没有，必须补上。

---

## 7. 一致性与对账

### 7.1 定位

**MySQL 是唯一真源，Redis 和 Qdrant 都是可丢弃的派生层。**

这个定位定下来，一致性问题大幅简化：不需要分布式事务、两阶段提交或 Saga。派生层落后甚至完全丢失，都能从真源重建。

### 7.2 两个不同的"位点"

原方案只有一个 checkpoint 概念，混用了两件不同的事。新方案明确拆开：

| 位点 | 载体 | 语义 | 丢失后果 |
|---|---|---|---|
| **提炼位点** | `memory_watermark.last_seq` | 某会话提炼到哪条消息了 | 重复提炼（记忆重复）或漏提炼（记忆缺失） |
| **同步状态** | `agent_memory.vector_synced_at` | 哪些记忆已写入 Qdrant | 向量缺失，召回不到 |

同步状态**用行级字段而不是全局 checkpoint**——`WHERE vector_synced_at IS NULL` 本身就是待办队列，天然幂等，Worker 崩溃重启自动续跑。

**这正是 `memory_sync_checkpoint.last_entry_id` 原设计的问题所在**：它是一个"最大已同步 id"的高水位标记，隐含假设"同步按 id 顺序完成"。但异步同步必然乱序——id=100 失败进入重试，id=101~200 已成功，水位推到 200 之后，**100 就永久漏掉了**。而且它是 per-user 粒度，一条失败记忆会卡住整个用户的游标（或者被跳过造成丢失），两种结果都不可接受。

因此 `memory_sync_checkpoint` 保留但语义降级为**对账任务的断点续跑记录**，不再作为同步正确性的依据。

### 7.3 对账任务（`sync_check`）

原方案只有单向补全（以文件为准补缺失向量）。新方案必须**双向**：

```
每日低峰期执行

① 正向：补写缺失向量
   SELECT * FROM agent_memory
    WHERE vector_synced_at IS NULL AND memory_layer = 2 AND status IN (1,2,3)
      AND created_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE)  -- 排除处理中的
   → 重新走 4.7 同步流程
   → 数量超阈值（如 > 10000）告警：说明同步 Worker 挂了

② 反向：清理幽灵向量
   scroll Qdrant 全量 point（按 app_id + user_id 分批）
   → 批量查 MySQL：SELECT memory_id, status FROM agent_memory WHERE id IN (...)
   → Qdrant 有但 MySQL 无        -> 删除（记忆已被合规删除）
   → Qdrant 有但 MySQL status!=1 -> 更新 payload.status 或删除
   → 数量超阈值告警

③ 抽检：召回质量
   随机抽 100 条 active warm 记忆
   → 用其 summary 作为 query 检索
   → 命中自己 = 正常；未命中 = embedding 或索引有问题

④ 进度写回 memory_sync_checkpoint
   last_entry_id / scanned_rows / missing_found / ghost_found / last_reconcile_at
```

**反向对账是原方案缺失的**。没有它，被撤销的记忆会在 Qdrant 里变成幽灵数据——MySQL 查不到，但召回时会返回一个不存在的 `memory_id`，回表取详情为空，白白占用一个 top-k 名额。

### 7.4 并发控制

**移除原方案的分布式文件锁。**

原方案需要锁，是因为多实例并发写同一个 JSON 文件会撕裂。新方案下：

| 场景 | 原方案 | 新方案 |
|---|---|---|
| 多实例写同一用户记忆 | 分布式文件锁 | MySQL 事务 + `uk_slot` 唯一约束 |
| 同一会话并发提炼 | 分布式文件锁 | `mem:extract:lock:{session_id}`（Redis，300s 过期）+ `uk_idempotency` 兜底 |
| 提炼任务重复投递 | — | `memory_task.uk_idempotency` 直接拒绝 |
| 缓存并发 | — | 写时 DEL，读时重建 |

**锁 + 唯一约束双保险**：即使 Redis 锁失效（Redis 抖动、锁过期但任务仍在跑），`uk_idempotency` 会让第二次插入失败，不会产生重复记忆。这比单纯依赖分布式锁可靠得多。

**`memory_task` 卡死回收**：Worker 崩溃会让任务永久停在 `running`。巡检任务定期执行：

```sql
UPDATE memory_task SET status='pending', next_run_at=UNIX_TIMESTAMP()
 WHERE status='running' AND updated_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE);
```

Worker 存活期间应定期心跳 touch 该行（`UPDATE ... SET updated_at=NOW()`），避免长任务被误回收。`idx_stuck (status, updated_at)` 就是为这个查询建的。

---

## 8. 多租户与数据隔离（新增）

原方案完全没提用户隔离。多用户在线服务下，越权读到别人的记忆是安全事故级问题。

**隔离维度**：采用现有库已确立的 `app_id` + `user_id` 两级隔离。`app_id` 是隔离边界（对应"应用/租户"），`user_id` 是数据归属者。不额外引入 `tenant_id`，避免概念重复。

若未来出现"企业下挂多员工账号、企业管理员可查下属数据"的 B 端形态，`app_id` 可直接承载企业维度，或在其上再加一层 `org_id`——现有索引以 `app_id` 打头，扩展时改动可控。

**MySQL 侧**：
- 所有查询强制带 `app_id` + `user_id`，代码层用 ORM scope 或统一 DAO 封装，**禁止裸写 SQL**
- 所有索引以 `app_id` / `user_id` 为前缀（见 DDL 中 `idx_hot_load`、`idx_warm_recall`）
- DAO 层自动注入隔离条件，业务代码无法绕过

**Qdrant 侧**：
- `app_id` 和 `user_id` 都建 payload index 且 `is_tenant: true`
- **每次检索强制带 filter**，无 filter 的检索请求在客户端封装层直接拒绝
- 单 collection 多租户，不做 per-user collection（Qdrant Cloud 默认每集群上限 1000 collection）

**Redis 侧**：
- 所有 key 带 `{app_id}:{user_id}` 命名空间
- 缓存值反序列化后二次校验 `user_id`（防 key 拼接错误）

**测试要求**：CI 里必须有用例验证"用户 A 无法通过任何接口读到用户 B 的记忆"，包括直接构造 `memory_id` 访问的场景。

---

## 9. 容灾与降级（新增）

原方案没有任何故障场景考虑。记忆系统是**增强组件而非核心链路**——它挂了，对话应该继续，只是变得"没记性"。

| 故障 | 影响 | 降级策略 | 用户感知 |
|---|---|---|---|
| **Qdrant 不可用** | warm 无法召回 | 跳过 warm 路，只用 hot 层；打降级标记 + 告警；对话正常继续 | 轻微：不记得历史事件，但仍遵守偏好和约束 |
| **Redis 不可用** | hot 缓存 + seq 发号失效 | hot 直接查 MySQL（`idx_hot_load`，单点查很快）；seq 降级为 `SELECT MAX(seq)+1 FOR UPDATE`，靠 `uk_session_seq` 兜底防重 | 无 / 轻微延迟增加 |
| **MySQL 主库不可用** | 读写全断 | 读切只读副本（hot 加载、warm 回表可继续）；写入进 Redis List 或 MQ 缓冲，主库恢复后重放；提炼 Worker 暂停（位点不前进，天然安全） | 轻微：新记忆延迟生效 |
| **MySQL 完全不可用** | 记忆全断 | hot 靠 Redis 缓存在 TTL 内提供只读降级；warm 完全不可用；对话主链路必须能跑 | 明显：助手失忆，但可用 |
| **LLM 提炼服务不可用** | 无法产生新记忆 | 任务停在 pending 或走退避重试；水位不前进；恢复后自动追赶（`uk_idempotency` 保证不重复）；堆积超阈值告警 | 无：短期无感知 |
| **Embedding 服务不可用** | 无法同步向量 | `vector_synced_at` 保持 NULL，队列堆积；hot 层完全不受影响（不走向量）；恢复后自动追赶 | 轻微：新情景记忆暂不可召回 |

**三条硬性设计原则**：

1. **记忆加载失败不得阻塞对话**。所有记忆读取都要有超时（建议 hot 200ms / warm 500ms）和 try-catch 兜底，失败即降级
2. **降级必须可观测**。每次降级打点，运维要能立刻知道"现在有多少请求在没有记忆的情况下跑"
3. **异步链路失败必须能自愈**。位点 + 幂等键 + 对账三件套保证任何异步任务挂掉重启后都能正确续跑，不需要人工干预

---

## 10. 可观测性（新增）

记忆系统的问题（漏召回、脏记忆、约束丢失）不会报错，只会表现为"助手变笨了"。没有指标就完全无法排查。

### 10.1 核心指标

**写入链路**

| 指标 | 类型 | 告警阈值 |
|---|---|---|
| `memory.extract.triggered` | Counter（按触发原因分标签：signal/token/timeout/session_end） | — |
| `memory.extract.success_rate` | Gauge | < 95% 告警 |
| `memory.extract.latency_p99` | Histogram | > 30s 告警 |
| `memory.extract.lag_seq` | Gauge（当前 seq − last_seq 的最大值） | > 10000 告警 |
| `memory.extract.llm_cost` | Counter（token 消耗，取自 `memory_task.payload`） | 日环比 +50% 告警 |
| `memory.task.dead_count` | Gauge（`status='dead'` 数量） | > 0 告警 |
| `memory.task.stuck_reclaimed` | Counter（卡死回收次数） | 突增说明 Worker 不稳定 |
| `memory.write.rejected_sensitive` | Counter（敏感信息拦截数） | 突增告警 |

**同步链路**

| 指标 | 类型 | 告警阈值 |
|---|---|---|
| `memory.vector.pending` | Gauge（`vector_synced_at IS NULL` 且 layer=2 的数量） | > 5000 告警 |
| `memory.vector.sync_lag_seconds` | Gauge（最早未同步记录的年龄） | > 300s 告警 |
| `memory.vector.sync_error_rate` | Gauge | > 1% 告警 |
| `memory.reconcile.missing_found` | Gauge（对账发现缺失） | > 0 告警 |
| `memory.reconcile.ghost_found` | Gauge（对账发现幽灵） | > 0 告警 |

**检索链路**

| 指标 | 类型 | 告警阈值 |
|---|---|---|
| `memory.hot.token_usage_ratio` | Gauge | > 80% 预警，> 100% **ERROR**（约束正在被截断） |
| `memory.hot.count` | Gauge（单用户 hot 条数） | > 80 触发 consolidate |
| `memory.hot.cache_hit_rate` | Gauge | < 80% 需分析 |
| `memory.warm.recall_hit_rate` | Gauge（top-k 非空比率） | < 30% 需分析 |
| `memory.warm.recall_empty_but_historical` | Gauge（见 6.3） | > 5% 考虑上冷 collection |
| `memory.search.latency_p99` | Histogram | > 800ms 告警 |
| `memory.degrade.count` | Counter（按降级类型分标签） | 突增告警 |

**治理链路**

| 指标 | 类型 | 告警阈值 |
|---|---|---|
| `memory.archived.count` | Counter（软删数量） | 日增量突增需排查 |
| `memory.conflict.detected` | Counter（矛盾检测命中数） | 突增说明提炼质量下降 |
| `memory.consolidate.merged` | Counter（压缩合并条数） | — |
| `memory.archived.bytes` | Counter（归档体积） | — |
| `memory.compliance_delete.lag` | Gauge（删除请求到全链路清理完成） | > 24h 告警（合规风险） |

### 10.2 结构化日志

每条记忆的关键操作都要可追溯，日志字段至少包含：

```json
{
  "ts": "2026-09-24T16:39:00Z",
  "level": "INFO",
  "event": "memory.write",
  "trace_id": "...",
  "app_id": "app_demo",
  "user_id": "u_10086",
  "memory_id": "01J8ZK3M9P2Q4R5S6T7V8W9X0Y",
  "memory_layer": 1,
  "memory_type": "user_preference",
  "slot_key": "no_emoji",
  "action": "upsert_supersede",
  "superseded_id": "01J8ZK3M8N1P2Q4R5S6T7V8W9X",
  "task_id": 10086,
  "source_type": 1,
  "confidence": 0.95
}
```

**日志脱敏**：`summary` 和 `content` 默认**不进日志**（可能含用户隐私）。排错需要时用 `memory_id` 去 MySQL 查。这是硬要求，不是可选项。

### 10.3 排查工具

必须提供内部工具支持以下查询，否则线上问题无法定位：

- 按 `app_id + user_id` 列出全部 active 记忆（看这个用户的记忆全貌）
- 按 `memory_id` 反查 `source_msg_ids` 对应的原始消息（回答"这条记忆从哪来的"）
- 按 `task_id` 查看提炼批次的完整上下文（输入消息区间、LLM 输出、产出记忆）
- 模拟检索：给定 query，输出 Qdrant top-N 原始 score + 应用层重排后的 final_score 明细（回答"为什么召回了这条 / 为什么没召回那条"）
- 单用户记忆重建：清空该用户向量 → 从 MySQL 全量重灌（处理索引损坏、embedding 换模型）

---

## 11. 改造清单与实施顺序

### P0（不做不能上线）

| 项 | 内容                                                                                      | 预估 |
|---|-----------------------------------------------------------------------------------------|---|
| P0-1 | 建 `memory_type_dict`（含种子数据）+ `agent_memory`                                             | 1d |
| P0-2 | 存量三表 ALTER（`memory_task` / `memory_watermark` / `memory_sync_checkpoint`）               | 0.5d |
| P0-3 | `chat_message` 补 `content_tokens`，写入链路接入 tokenizer（待定，先不做，后续再看怎么加载此表）                   | 1d |
| P0-4 | JSON 文件 → MySQL 数据迁移脚本（含校验：迁移前后条数、抽样内容比对）                                               | 2d |
| P0-5 | hot 加载改造：读文件 → 查 SQL（`idx_hot_load`）                                                    | 1d |
| P0-6 | 分层归属调整：把 `user_preference` / `resource_path` 类记忆从 warm 迁到 hot                           | 1d |
| P0-7 | hot 写入改为 `uk_slot` upsert + `active_slot` 维护（封装 `markInactive()`），移除同步 LLM 判重           | 1.5d |
| P0-8 | Qdrant payload 瘦身：移除正文，point id 改用 `agent_memory.id`；建 5 个 payload index（含 `is_tenant`） | 1d |
| P0-9 | 多租户隔离：DAO 层强制 `app_id + user_id`，Qdrant 检索强制 filter，补越权测试用例                             | 1.5d |
| P0-10 | 合规删除级联流程（含 `source_msg_ids` 字段落地 + `compliance_delete` 任务类型）                            | 2d |
| P0-11 | hot 层取消时间衰减；warm 保留                                                                     | 0.5d |
| P0-12 | 跨层冲突规则改为 hot > warm                                                                     | 0.5d |
| P0-13 | hot 层 token 溢出改为告警而非静默截断                                                                | 0.5d |

**小计约 14.5 人日**

### P1（上线后 2 周内）

| 项 | 内容 | 预估 |
|---|---|---|
| P1-1 | Redis hot 缓存 + 写时 DEL | 1d |
| P1-2 | 提炼两级漏斗（轻量信号判断 + 双路阈值触发 + 兜底扫描） | 2d |
| P1-3 | 提炼 Worker 接入 `memory_task`（含卡死回收巡检、心跳） | 1.5d |
| P1-4 | 向量同步 Worker（`vector_synced_at IS NULL` 驱动，批量 embedding + 批量 upsert） | 2d |
| P1-5 | 双向对账任务 + 抽检任务（`sync_check`） | 2d |
| P1-6 | 移除分布式文件锁，改为 Redis 锁 + `uk_idempotency` 双保险 | 0.5d |
| P1-7 | `last_hit_at` 异步刷回（Redis Hash 聚合） | 1d |
| P1-8 | 应用层重排（召回 top-N → 三路加权 → 截 top-k），decay 公式落地 | 1.5d |
| P1-9 | `conflict_detect` 任务（矛盾检测降为每日异步） | 1d |
| P1-10 | `decay_archive` 任务（软删 + 归档，分批 LIMIT） | 1.5d |
| P1-11 | 容灾降级：全链路超时 + try-catch + 降级打点 | 2d |
| P1-12 | 可观测性：核心指标埋点 + 告警规则 + 结构化日志 | 2d |
| P1-13 | 超长消息内容外置对象存储（`payload_ref`） | 1.5d |

**小计约 19.5 人日**

### P2（按需）

| 项 | 内容 |
|---|---|
| P2-1 | `memory_type_dict` 支持 agent 自主新增类型（`is_builtin=0` + 治理审计） |
| P2-2 | `consolidate` 任务：hot 层压缩（同类合并、抽象提升） |
| P2-3 | warm → hot 晋升通道（同类反馈累积 N 次自动提升为偏好） |
| P2-4 | 人工审阅视图：异步导出 markdown/JSON 快照到对象存储 |
| P2-5 | 管理后台：记忆的查看 / 编辑 / 撤销（写 MySQL，不直接改文件） |
| P2-6 | 冷 collection（视 `recall_empty_but_historical` 指标决定） |
| P2-7 | 排查工具五件套（见 10.3） |
| P2-8 | `chat_message` 按月 RANGE 分区；hot 记忆拆独立小表 |
| P2-9 | `agent_memory.id` 改为应用层全局发号（若有分库分表计划，建议提前到 P0） |

---

## 附录 A：人工审阅视图

原方案把"支持人工查看、编辑、审计、回溯"作为选择 JSON 文件的理由之一。这个诉求合理，但在新架构下要用正确的方式满足：

**MySQL 是真源，文件是导出视图。**

```
每日定时任务
  → 按用户渲染 hot 记忆为 markdown + JSON 双格式
  → 写入对象存储：oss://memory-review/{app_id}/{user_id}/{yyyy-mm-dd}.md
  → 保留最近 30 天，供人工查看与 diff
```

**关键约束：这份文件是只读的，不参与在线读写链路。**

人工编辑必须走管理接口写 MySQL，接口负责：
1. 校验 `memory_type` 在 `memory_type_dict` 内且 `enabled=1`
2. 走 `uk_slot` upsert 逻辑（含 `active_slot` 维护）
3. `source_type=3`（人工录入），`confidence=1.00`
4. 失效 Redis 缓存
5. 若为 warm 层，`vector_synced_at` 置 NULL 入同步队列
6. 写审计日志

**为什么不能直接改文件**：新架构下文件不在读写链路上，改了不会生效。如果保留"改文件能生效"的假象，运维会陷入"我明明改了为什么没用"的排查黑洞——这比没有文件更糟。

导出视图依然完整满足原方案诉求：可看（markdown 渲染）、可 diff（每日快照对比）、可审计（对象存储版本控制）、可回溯（MySQL 里保留全部 superseded 历史记录）。

---

## 附录 B：待决策项

以下需要业务方或技术负责人拍板，文档中已给建议值但不宜由设计方单方面决定：

| # | 决策项 | 建议 | 影响 |
|---|---|---|---|
| B1 | hot / warm 的 token 预算具体值 | hot 2500 / warm 1500 | 需按实际模型上下文窗口和 prompt 其余部分压测确定 |
| B2 | hot 层条数硬上限 | 80 条 | 决定 consolidate 任务的触发频率 |
| B3 | 归档后是否保留冷召回（6.3 方案 A / B） | 先 A，按指标决定是否上 B | 成本 vs 长期记忆完整性 |
| B4 | 衰减半衰期 | 默认 30 天，`user_correction` 60 天，`task_experience` 90 天 | 影响老记忆的召回概率，已配置在字典表可在线调 |
| B5 | 30/90 天软删与归档阈值是否调整 | 保留原方案值 | hot 层已豁免，warm 层可按实际数据分布调整 |
| B6 | embedding 模型与向量维度 | 按现有选型填 Qdrant `size` | 决定后不宜频繁更换（换则需走附录 C 的 alias 重建） |
| B7 | 合规删除是硬删还是软删 + 脱敏 | 按法务意见 | 硬删无法审计，软删需确保内容已脱敏 |
| B8 | 提炼用哪个 LLM | 建议中等规模模型，不用最大模型 | 成本与质量的平衡，需实测提炼准确率 |
| B9 | 轻量信号判断是否需要小模型 | 建议先纯规则，不够再加 | 规则能覆盖大部分场景，加模型增加延迟和成本 |
| B10 | Qdrant 部署形态（自建单机 / 集群 / 云托管） | 按用户规模定 | 集群模式下 snapshot 需每节点单独做，备份策略不同 |
| B11 | 双路触发阈值 | `pending_signals >= 5` OR `pending_tokens >= 3000` | 前者决定记忆生效延迟，后者决定提炼成本 |
| B12 | 是否近期分库分表 | 若"是"，`agent_memory.id` 应现在就改为应用层全局发号 | 避免将来一次 Qdrant point id 全量迁移 |
| B13 | `app_id` 是否承载企业租户维度 | 纯 2C 可固定单值；有 B 端规划则需明确企业级权限模型 | 影响 DAO 隔离层的实现复杂度 |

---

## 附录 C：Qdrant 换 embedding 模型的重建流程

embedding 模型或向量维度变更时，用 collection alias 做零停机切换（Qdrant 官方推荐做法，alias 变更是原子的，并发请求不受影响）。落到 `vector_rebuild` 任务：

```
① 建 agent_memory_warm_v2（新维度 / 新 metric）
② 建同名 payload index（含 is_tenant）
③ 应用层开启双写：新记忆同时写 v1 和 v2
④ 后台任务：从 MySQL 全量读 active warm 记忆
   → 用新模型批量 embedding
   → 批量灌入 v2（point id 仍用 agent_memory.id，保持不变）
   （这一步是"从 MySQL 读"而不是"从 v1 scroll"——
     有真源时重建是一次简单的 SELECT 全表扫描；
     没有真源时是一次痛苦的全量 API 导出，且要处理分页与并发写入）
⑤ 追平校验：v2 的 point 数 == MySQL 中 layer=2 且 status=1 的记忆数
⑥ 原子切换 alias：
   POST /collections/aliases
   { "actions": [
       { "delete_alias": { "alias_name": "warm_prod" } },
       { "create_alias": { "collection_name": "agent_memory_warm_v2",
                           "alias_name": "warm_prod" } }
   ]}
⑦ 关闭双写，观察 1~3 天
⑧ 删除 v1
```

**注意**：Qdrant 的 collection snapshot **不包含 alias**，alias 需要单独迁移或重建。灾备演练时容易漏掉这一步。另外分布式部署下 snapshot 必须**每个节点单独创建**，单个 snapshot 只含该节点的数据——所以不要把 Qdrant snapshot 当主备份手段，MySQL 的全量备份 + binlog 才是恢复基线。

应用代码全程只访问 alias `warm_prod`，不直接引用 collection 名。

---

## 附录 D：与原方案的术语对照

| 原方案术语 | 新方案术语 | 说明 |
|---|---|---|
| Hot 核心记忆 | Hot 层 · 结构化语义记忆（`memory_layer=1`） | 职责扩大，纳入用户偏好与资源指针 |
| Warm 主题记忆 | Warm 层 · 情景记忆（`memory_layer=2`） | 职责收窄，移出偏好类 |
| JSON 文件（真相源） | MySQL `agent_memory`（真源） | 介质变更 |
| 向量数据库（检索加速层） | Qdrant（warm 层语义索引） | 定位不变，payload 瘦身，point id 用自增整数 |
| 记忆主题 | `memory_type` + `subject` | 拆成两个正交维度：类型和主体 |
| 记忆 id（ULID） | `memory_id`（ULID，业务id）+ `id`（自增，Qdrant point id） | 双 id：ULID 对外与回表，自增整数供 Qdrant |
| 同步 checkpoint | `vector_synced_at` 行级字段 | 从全局高水位改为行级状态，修掉漏同步漏洞 |
| `memory_sync_checkpoint.last_entry_id` | 同名，语义改为"对账断点" | 不再承担同步正确性保证 |
| （无对应） | `memory_watermark.last_seq` | 现有表已有，提炼位点 |
| （无对应） | `memory_watermark.pending_signals / pending_tokens` | 新增：双路触发计数 |
| （无对应） | `memory_task.idempotency_key` | 新增：任务幂等，替代独立的批次表 |
| （无对应） | `slot_key` + `active_slot` + `uk_slot` | 新增：hot 层确定性去重且保留历史 |
| （无对应） | `source_msg_ids` | 新增：溯源与合规删除的基础 |
| （无对应） | `chat_message.content_tokens` | 新增：token 计量，区别于 LLM 调用的 usage |
| 文件归档压缩 | 对象存储归档 | 保留，触发条件不变 |
| 文件 MD5 监听 | 已移除 | 原方案已自行标注忽略 |
| 分布式文件锁 | 已移除 | 由事务 + 唯一约束 + Redis 锁替代 |

---

## 修订记录

| 版本 | 日期 | 修订内容 |
|---|---|---|
| v1.0 | — | 原方案：JSON 文件 + 向量库双数据源，hot/warm 两层 |
| v2.0 | 2026-09-24 | 真源迁移至 MySQL；重划 hot/warm 职责；补齐多租户隔离、容灾降级、合规删除、可观测性；明确提炼位点、双向对账、衰减公式、token 裁剪策略；共 26 项差异 |
| v2.1 | 2026-09-24 | 与现有库整合：`memory_extract_cursor` 并入 `memory_watermark`、`memory_extract_batch` 并入 `memory_task`；修正 `memory_sync_checkpoint` 高水位漏同步漏洞；Qdrant point id 改用自增整数（ULID 不被接受）；`uk_slot` 引入 `active_slot` 解决历史保留冲突；字段规范对齐现有库（VARCHAR(64) id / DATETIME 秒级 / utf8mb4_unicode_ci / app_id 隔离）；补 `chat_message.content_tokens` 与两套 token 字段语义区分；补 `seq` 字段职责说明；差异项增至 27 条 |

---

内容由 AI 生成
