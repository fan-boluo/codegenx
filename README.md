# CodeGenX

CodeGenX is a Python-based microservice platform for AI-driven code generation, visual editing, cloud deployment, and enterprise management.

# 混合协议
- 用户域 gateway -> user-service 使用 gRPC + Nacos
- 生成域 gateway -> chat-service -> app-service -> ai-service 使用 HTTP/SSE + Nacos
- 前端出口继续保持 HTTP/SSE，不引入前端协议变化
- 
# 微服务调用：

网关接口层  
    ↓
Proxy（代理层,gateway）  
    ↓
ServiceGrpcClient（gRPC 调用层,gateway）
    ↓
Nacos 服务发现  （infra）        

Nacos 服务注册 （service）
   ⬆
ServiceServicer(grpc服务层,service)
   ⬆
grpc_pb2 / grpc_pb2_grpc (service)
   ⬆
proto (service)
   ⬆
实际业务功能

部署：
使用静态资源访问+nginx部署的方式
前端点击预览，可以调用后端接口访问静态资源
前端点击部署：nginx部署



可视化修改

使用前端页面内嵌ifram来实现，当选中子网站元素时，向父网站汇报



## 目录介绍

api-gateway:网关，要实现限流，JWT认证，黑名单拦截等功能
services:python 微服务,ai-service、chat-service、app-service、user-service，分别处理 AI 生成服务、聊天服务、应用/工作区服务、用户服务
shared：共享模块

## 技术栈

要完成的python服务的技术栈，计划如下：

- 语言：Python + FastAPI

- 限流：Redis + Lua 脚本实现 **令牌桶 / 滑动窗口限流**

- 鉴权：JWT 解析、用户权限校验、接口访问控制

- 服务发现与调用：Nacos + grpc

- 高可用：k8s 多实例部署 + 健康检查 + 自动熔断

- 大模型架构：langchain / openAI

# memory
qdrant向量数据库存储
三个 Collection：
short_term_memories    → 高写入、短 TTL、每天滚动的短期向量库
long_term_memories     → 低写入、持久保留、高权重的长期向量库
memory_consolidation_logs                  元数据
## 一 memory功能清单
1. 记忆写入
（1）短期记忆追加：LLM 在对话中自动判断并写入当日短期记忆
记忆去重没有重复的再写入，判重只查短期，阈值设很高0.95只查几乎相同的内容，为的是快速写入
如果设置的比较低，比如0.7 0.8那么说明是有一些不一样的信息的，就得去提炼，这样处理就跟长期一样了

（2）长期记忆提炼：从短期记忆中提取、合并、升级为长期记忆
候选短期记忆 (N条)
    │
    ▼
对每条短期记忆：qdrant.search(长期向量, top_k=3, threshold=0.75)
    │
    ▼
有匹配的 → 进入 LLM 待判断池
    │  无匹配的 → 判断 importance > 0.8 则 new，否则 ignore，得到策略
    ▼
LLM 批量判断，得到策略（一次调用，将待判断池的发送给大模型）
    │
    ▼
执行策略：new / update / duplicate / ignore
new :新建记忆
update：更新content、合并 source_id、递增 version
duplicate:仅记录
ignore:跳过

2. 记忆检索（SessionStart / 上下文组装）
多集合联合召回：长期库 + 短期库并行检索，每个执行混合检索
访问计数更新：每次检索命中后更新 access_count

**混合检索**
**1)搜索**：关键字 / 向量相似度搜索：语义匹配，返回 top_k
过滤查询：按 user_id, app_id, 时间范围过滤，只查最近7天
topK:10, threshold_score:0.6
得到keyword和vector的两个结果
**合并**：分数先各自做正则化，然后根据id加权合并结果，keyword*0.3 + vector*0.7
rerank:MMR重排,考虑相关性和多样性的重新排序，然后取topK=10,没有改变原始分数

**2)检索去重**：长期和短期命中结果去重，优先保留长期
embedding余弦相似度去重，vector在检索的时候已经得到了
遍历短期记忆，计算与长期的相似度，有匹配到高相似度则认为重复，否则才会加入结果中

**3)rerank 加权**：
分数只是语义上的相关性，但是它是有时效性的，因此长短期的重要性是不同的
短期时效衰减：短期记忆按创建时间降权（openclaw）
    半衰期（half_life_days）表示记忆的相关性分数在一定天数后减半。 当前设置为 3 天，即记忆的分数每过 3天会减半。
    然后距离当前时间越远，权重越低，按照指数衰减公式，计算时间衰减因子。
长期记忆权重0.7 > 短期记忆 0.3

**4)topK**

5) 更新命中的access_count
暂时没有用到，后续会使用这个再优化importance
importance:可以让大模型在得到这个结果后，根据这个值考虑问题时有所侧重

3. 记忆生命周期-定时任务
（1）短期过期清理：超期的短期记忆自动删除（基于 expires_at）
保留10天

（2）长期记忆更新：
触发条件（满足任一） 
• 会话结束
• 每日凌晨 3:00 定时任务    
• 短期记忆数量 > 100 条

（3）重要性衰减：长期未访问的长期记忆逐步降低 importance

4. 持久化-定时任务
备份恢复：Qdrant snapshot 定期备份

5. 可观测性
retriver之后
检索命中率：请求命中数/记忆总量

定时任务
记忆增量统计：每日新增短期记忆数量

提炼日志：每次短期 → 长期提炼的操作记录

## 二、Qdrant Collection 设计
Collection 1：short_term_memories
字段	类型	说明
id	uuid (point id)	stm-{date}-{seq}
vector.content	float[1024]	记忆正文向量
payload.user_id	keyword	用户标识
payload.app_id	keyword	项目/工作区标识
payload.content	text	记忆原文
payload.memory_type	keyword	preference / decision / fact / todo
payload.source_date	keyword	2026-04-30 格式
payload.importance	float	0-1，LLM 打分
payload.access_count	integer	检索命中次数
payload.expires_at	integer	Unix 时间戳，默认 +7天
payload.created_at	integer	创建时间
索引字段：user_id, app_id, source_date, memory_type, importance, expires_at

Collection 2：long_term_memories
字段	类型	说明
id	uuid (point id)	ltm-{seq}
vector.content	float[1024]	记忆正文向量
payload.user_id	keyword	用户标识
payload.app_id	keyword	项目/工作区标识
payload.content	text	提炼后的记忆内容
payload.memory_type	keyword	preference / decision / principle / fact
payload.source_short_term_ids	keyword[]	来源短期记忆 ID 数组
payload.importance	float	0-1，默认 0.8
payload.access_count	integer	累计命中次数
payload.version	integer	更新次数
payload.created_at	integer	首次创建时间
payload.updated_at	integer	最后更新时间
索引字段：user_id, app_id, memory_type, importance

Collection 3： memory_consolidation_logs
提炼日志
字段	说明
id	操作 ID
action	new / update / merge / delete
source_ids	来源短期记忆 ID
target_id	目标长期记忆 ID
result_content	操作后内容
performed_at	操作时间

四、检索策略速查
text
SessionStart / 上下文组装时调用：

1. 长期记忆检索
   - 过滤：user_id + project
   - 排序：向量距离 ASC
   - 阈值：score < 0.3（相似度 > 0.7）
   - 数量：top_k = 20

2. 短期记忆检索
   - 过滤：user_id + project + source_date >= now-3天 + expires_at > now
   - 排序：向量距离 ASC
   - 阈值：score < 0.35
   - 数量：top_k = 10

3. 合并去重
   - 长期优先
   - 短期命中如果与长期语义相似（LLM 判断），丢弃

4. 注入上下文
   - 按 type 分组格式化
   - 注入 system prompt


# 三、监控管线
MonitorPipeline（门面）
监控门面层（MonitorPipeline）
Trace管理（TraceManager）
指标采集器（MetricsCollector）
数据持久化（MonitorStore）

OpenTelemetry SDK
TracerProvider + MeterProvider + OTLP Exporter

TelemetrySDK
初始化与关闭

TraceManager
分布式追踪
创建/管理 Root Span 和 Child Span
Span 属性、事件、异常

MetricsCollector
三类指标：
计数 Counter：轮次计数、Token 使用、工具调用、错误、会话
分布 Histogram：LLM 延迟、首 Token 延迟、工具延迟、记忆检索延迟
瞬时 Gauge：上下文 Token 数、配额剩余、内存命中

MonitorStore
数据持久化：MySQL 存储 Session/Turn/Spans/Alerts

## 功能描述
功能	数据来源	存储	展示方式	用途
Span追踪	LLM/Tool/Memory埋点	MySQL spans 表	SQL查询 / /session/{id}/spans	会话调用链调试
指标采集	同上	MySQL session_metrics + turn_metrics	/metrics (Prometheus) + SQL	性能分析、成本统计
健康检查	主动探测	不持久化	/health 端点 + 日志	故障提前发现
告警	指标阈值判断	alerts.log 文件 + 内存	/alerts 端点 + 日志	异常通知
HTTP端点	以上所有	无存储	REST API	调试、外部集成


功能一：Span 追踪收集
设计目标
记录一次会话中每个操作（LLM调用、工具调用、记忆检索、上下文组装）的开始时间、结束时间、耗时、状态和附加属性，
用于追踪单次会话的完整执行链路。

功能二：指标采集
设计目标
采集会话级和 Turn 级的量化指标：

LLM： prompt_tokens, completion_tokens, first_token_ms, total_ms，recovery指标
tool: tool_name, latency_ms, status,调用次数
memory: hits, latency_ms
context: token_count,token使用量




功能四：告警
告警规则
规则	阈值	级别
LLM 调用平均延迟 > 10s	最近 5 次均值	WARN
LLM 调用单次超时 > 60s	单次	ERROR
Token 消耗超过配额 90%	累计值	WARN
Tool 调用连续失败 3 次	连续计数	ERROR
会话超过 50 轮未结束	单次	WARN
-------------不做-------------
磁盘使用率 > 90%	检查时	ERROR
Qdrant 健康检查失败	单次	CRITICAL

功能五：HTTP 端点综合
设计目标
将所有监控数据对外暴露，供调试和外部系统消费

## 结构设计
otlp不做双写了，它没办法导入MySQL中，现在我需要自己写collector：
1 TraceManager完全去掉，自己管理生命周期；
SpanContext：自定义的，Span封装一层，这个也可以去掉了
TelemetrySDK没有endpoint服务可以用，不做双写的话有没有必要保留，没有就去掉，仅留下有用的

2 spanCollector：
runtime过程中收集span的信息，缓存并在session结束后通过MonitorStore，flush到数据库的spans表
需要写一个sqlalchemy的类对应这个表SpanRecorder

on_session_start:
根Span新建,SpanRecorder初始化,追加到spanCollector中，

on_llm on_tool 等触发时点：
子Span新建，SpanRecorder初始化，对应的操作名-OperationName，对应的指标TurnContextMetrics，（...），TurnMemoryMetrics四类，
见telemetry_schema.py
追加/更新到spanCollector中

3 MetricCollector:
这个还要保留，作为session级别的指标收集器
runtime过程中收集metric的信息，放到MetricCollector里面缓存着
session结束后通过MonitorStore，flush到数据库->session_metrics表

4 turn_metrics表的指标怎么来
在turn结束后，应该有多个spanRecord在SpanCollector缓存着，它们的attributes属性就是要组成的turn_metric表
error_count可以使用is_error的个数汇总,tool_calls_satus也需要汇总

5 去掉request_metrics表

6 monoitor.sql的四张表都建sqlalchemy的类，MonitorStore的时候可以更方便操作

7 重新整理这个monitor模块，去掉多余的，不再使用的类直接去掉



# 四、runtime
## 初始化
随服务启动完成初始化：
1. 加载配置
2. 初始化存储层
 MySQL 连接池（全局复用）
 Qdrant 客户端
3. 初始化全局服务（单例，整个进程一份）
MonitorPipeline
MemoryManager
ToolRegistry
SkillLoader
4. 启动定时任务？？？
SpanCollector 定时 flush
MetricsBuffer 定时 flush
HealthChecker 定时检查

## 消息收发
采用双循环、双层 Queue 架构方式
Runtime启动后进入 dispatch_loop 循环，等待消息接收，外层循环使用的是 message_bus 来做消息管线。
内层循环是每个 Session 一个循环，用 session_state.queue 做消息对等。

具体方式：
外层循环：
用户消息发送后进入 message_bus，然后 dispatch_loop 消费消息，找到请求 request 所在的 session_state，没有就新建。
session循环，每个session一个循环；
session_state 新建：
    将 request 放到 session_state.queue 中等待被消费
    创建一个 session_worker 循环
session_worker 循环：
    取出 queue 中的 request 执行

消息返回：
message_bus消息总线采用请求订阅的方式，每当有请求进来后

## 执行
session开始后，执行on_session_start:
### on_session_start


### on_turn_start


### pre_llm_call

### post_llm_call

### on_turn_end

### on_session_end

### on_error

## 异常处理

### session异常
session关闭的情况...
超过30分钟空闲：
Runtime 停止；
Turn 执行异常



# 2 项目完成情况

| 模块           | 功能      | 备注  |
| ------------ | ------- | --- |
| ai-gateway   | JWT鉴权   | 已完成 |
|              | 限流      |     |
|              | 黑名单拦截   |     |
|              | 其它      |     |
| user-service | 注册      | 已完成 |
|              | 登录      | 已完成 |
|              | 查询      | 已完成 |
|              | 其它      |     |
| app-service  | 应用创建    |     |
|              | 代码生成    |     |
|              | 代码保存    |     |
|              | 代码下载    |     |
|              | 代码部署    |     |
|              | 调用日志记录  |     |
| chat-service | 聊天编排    |     |
|              | 聊天历史服务  |     |
| api-service  | 大模型调用   |     |
|              | 大模型调用监控 |     |