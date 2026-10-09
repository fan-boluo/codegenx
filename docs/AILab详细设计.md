系统详细设计文档
整体架构：网关 + gRPC 微服务 + Nacos 服务发现，整体分四层
前端：Vue 3 + Monaco Editor 编辑器
API 网关 (FastAPI)：统一入口，负责 JWT 认证、限流、IP 黑名单、动态路由转发
微服务层：User Service (gRPC)、App Service (FastAPI)、AI Service (FastAPI) — 各服务通过 Nacos 注册发现
基础设施：MySQL 8.0、Redis 7、Prometheus、Docker Compose / K8s

核心是 AI Service 中的 AgentRuntime
基于事件驱动的循环结构，核心组件包括 MessageBus 进行消息收发，SessionPool 管理并发会话（上限 1000），Context Builder 组装上下文
流程：LLM 推理 → Tool Executor 执行工具调用，形成完整的 Agent 生命周期

# JWT 的认证过程

用户登录后，网关转发到 user-service 进行账户密码校验返回给网关
网关把用户信息包装成 JWTUser，创建 jwt_token (RSA256 算法)，设置过期时间 1h，然后将 token 给前端
前端得到 token 后在 LocalStorage 存储
后续请求头携带 token，到网关进行验证
网关解析验证 token、过期时间，然后执行相应的业务逻辑
代码路径：backend/api-gateway/middleware/jwt_auth.py
过期规则：硬性超过 24H 自动过期，无刷新机制
前端防护：前端设置 30 分钟无操作退出登录（useIdleTimeout.ts / GlobalHeader.vue），减少过期重登问题
**[TODO] JWT 退出漏洞修复**
用户主动退出后，JWT token 仍然有效，存在泄露风险，需设计解决方案
方案：
完整一次登录→使用→刷新→登出全链路
》登录
账号密码校验通过：
生成 15min JWT access_token；
生成随机 refresh_token，存入 Redis，7 天过期；
两个 token 一起返回给前端保存。

》正常业务请求
请求头携带 Authorization: Bearer access_token，网关 / 服务直接解析 JWT 鉴权，不查 Redis，速度快。

》token 快过期，自动静默刷新
前端后台调用刷新接口，带上 refresh_token；
后端查 Redis 有记录 → 下发新 access_token，覆盖本地旧 token。

》用户主动退出
调用 logout，后端删除 Redis 中该设备的 refresh_token；
本地清空两个 token。
token加入ｒｅｄｉｓ黑名单，ｔｔｌ＝剩余过期时间，再拦截处增加token有效期的拦截

**密钥改造：**
当前代码：`JWT_ALGORITHM = "HS256"`，对称加密，密钥泄露后攻击者可伪造任意用户 token。
改为非对称的RS２５６，配置密钥地址为ｓｅｃｒｔｅ文件中，生成ｊｗｔ的ｐａｙｌｏａｄ加入ｊｗｔＩＤ，和签发时间双重保障

```
1. 改用 RS256（非对称加密）：
   - 私钥签发（auth service 持有），公钥验证（Gateway 持有）
   - 私钥永远不出 auth service，Gateway 只拿公钥
   - 密钥轮换：私钥每 90 天轮换，公钥通过 Nacos 配置下发

2. 密钥管理：
   - 生产环境密钥从 Vault/KMS 读取，不写在配置文件/环境变量
   - K8s Secret 加密存储，Pod 启动时注入内存

3. Token 安全加固：
   - payload 中加入 jti（JWT ID，全局唯一），用于精准撤销
   - 加入 iat（签发时间），服务端拒绝超长存活的 token
   - 加入 aud（audience），限定 token 目标服务

4. 传输安全：全链路 HTTPS，禁止 token 出现在 URL Query 参数中
```

**前端的改造：**
silent refresh 静默刷新逻辑；
要处理过期预判、异步刷新、刷新并发锁、401 跳转、网络失败重试等边界；

# 限流模块

实现了固定窗口和滑动窗口两种限流（api-gateway/services/rate_limit_service.py）
生产实际使用固定窗口 check_user_rate_limit，用户级限流，每秒 limit=5
Redis 实现：INCR + EXPIRE，两行代码原子操作，零额外开销
选用固定窗口的原因:
AI 调用本身有秒级延迟，请求不是瞬时密集到达，不存在窗口边界的 “双倍流量” 问题

**固定窗口原理**
将时间划分为固定大小的窗口（如 1 秒），每个窗口内维护一个计数器。当请求到达时，计数器 + 1，如果超过限制则拒绝
时间轴示例：0s、1s、2s、3s
窗口 1 计数器：5、5、3、5

**双倍流量 / 窗口临界流量问题**
举例限流配置：100 次 / 60 秒
时间轴：0s ~ 60s ~ 120s
窗口 1：0~60s；窗口 2：60~120s
场景：用户在 60s 窗口边界时快速发送 100 个请求
结果：窗口 1 59s 发送 100 个 + 窗口 2 61s 发送 100 个 = 200 个 → 实际瞬时流量达到 200 次 / 60 秒

**滑动窗口原理**
将时间窗口细分为多个小窗口（如 1 秒分为 10 个 0.1 秒窗口），每个请求记录时间戳，计算当前窗口内所有请求的加权总和
请求进来逻辑：先得到窗口边界 [now-window_size, now]，然后计算窗口内请求数，判断是否超限，未超限则添加，否则拒绝

# 黑名单 IP 拦截

在 fastapi 添加拦截中间件，数据持久存储在 redis 中，增删改查逻辑直接在网关实现
基于 redis 的 Set 集合操作：
是否存在：sismember(BLACKLIST_IP_KEY, ip)
添加：sadd(BLACKLIST_IP_KEY, ip)
移除：srem(BLACKLIST_IP_KEY, ip)
查询所有：smembers(BLACKLIST_IP_KEY)
获取总量：scard(BLACKLIST_IP_KEY)
删除数据：redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
获取总量：redis.call('ZCARD', key)
增加：redis.call('ZADD', key, now, member)
给key设置过期时间：redis.call('EXPIRE', key, tonumber(ARGV[5]))

# 动态路由

TODO 待补充动态路由实现、区分路由类型等细节

# Runtime 架构

整体基于事件驱动循环结构，核心三大组件：MessageBus 消息管线、session_pool 会话管理、runtime 循环
ai-service 服务启动流程：
runtime 启动创建 loop 循环等待消息进入
启动 session_pool：创建定时清理 session 的后台任务
加载全部工具集

## messageBus - 消息管线

内置两个队列：消息输入队列、消息传出队列
**消息接收流程**
请求进入后，messageBus 执行两步操作：
根据 request_id 创建独立队列，该队列用于后续消息传出推送
将原始请求放入消息输入队列
runtime 的 loop 循环持续从输入队列获取请求消息
session_pool 取出一个 session_state 会话实例
将请求放入 session_state 内部等待队列，异步创建协程任务处理该请求
请求处理：从 session_state 取出请求执行业务逻辑，生成响应消息发送回消息管线
**消息传出流程**
回复客户端时：
根据 request_id 匹配对应消息队列
将响应消息追加至队列
消息收发接口从队列取出消息，封装为 AgentEvent 返回前端

# session_pool 会话池

基础配置
最大并发会话：1000 个
session 最大空闲时间：1h
清理任务循环：5 分钟执行一次，回收闲置 / 关闭 session
底层存储：OrderedDict[session_id, SessionState]
生命周期：创建、回收、更新活跃时间ｔｏｕｃｈ

## session 创建规则

采用 LRU （Least Recently Used）淘汰策略：
最早使用的
dict 中存在则直接获取，touch 更新会话最后活跃时间
不存在则新建 session 并加入池
若池内会话已达 1000 上限，淘汰第一个 session（有问题待改进），新建会话入池

**潜在问题 待完善 TODO**
LRU 策略若被淘汰的会话正在执行业务，session 被强制关闭后用户侧感知异常如何处理？- 用户会得到requeststoped事件

1. 淘汰时先检查 session 是否处于 RUNNING 状态：遍历 OrderedDict 前 20% 的元素，
   跳过 RUNNING 状态的 session，找到第一个 IDLE/COMPLETED/FAILED 状态的 session 淘汰。

2. 如果前 20% 都在 RUNNING：说明系统容量已经不足，此时应触发告警而非继续淘汰 ——
   通过 Prometheus 指标 session_pool_eviction_blocked 上报，通知运维扩容。

3. 增加软性淘汰优先队列：sessions 按 (closed, idle_duration, worker_running) 三级排序，
   优先淘汰 closed > 长时间 idle > 短时间 idle 但无活跃 worker 的 session。

## session 回收逻辑

关闭 session 入口：_close_session_unsafe
》获取当前会话所有正在执行的协程任务 work_task，执行 cancel 取消
》获取全部 activate_task 激活任务，执行 cancel 取消
全部为异步协程任务
**待完善 TODO：**
》资源回收

- **数据库连接**：如果 session 在 `await` 数据库查询时被 cancel，连接可能处于"借出未还"状态。完善方案：在 `_close_session_unsafe` 中增加 `await session.context_manager.close_db()` 显式归还连接

- **文件句柄**：如果 tool 正在读写文件，cancel 后不会被自动关闭。完善方案：ToolExecutor 中的 open 统一使用 `async with`，cancel 时自动 close

- **LLM 连接**：每次创建新 AsyncLLMClient 实例，cancel 后 httpx 连接靠 GC 回收。完善方案：改为连接池单例，cancel 时显式 `await client.aclose()`
  》RuntimeSessionState 的销毁

- 1. 增加显式清理方法 RuntimeSessionState.dispose()：
     - session_manager.flush() → 将未落盘的日志刷盘
     - task_manager.close() → 关闭任务图文件句柄
     - context_manager.clear() → 清空 chat_messages 释放大列表内存
     - 设置这些属性为 None，打破循环引用
2. 在 _close_session_unsafe 的 finally 块中调用 session.dispose()


# 上下文
上下文工程设计.md

# Hook 钩子系统
Hook设计.md

# tool

面向 csv/table 原始数据探索，提供基础表信息统计：表大小、列类型、采样数据
百万级大表自适应采样
执行数据统计前对底层数据表自动采样，采样规则按数据量分级：
< 10 万行：全量计算
10 万～50 万：10% 等距采样  pandas读取的时候有skip的参数
50 万～200 万：5% 采样
200 万：1% 采样
表大小估算逻辑
数据库表直接查询元数据获取行数；csv 文件根据文件大小估算总行数，动态调整采样比例
超时熔断
单列统计计算超时 30 秒自动中断，返回已计算结果 + 超时提示

# 可观测性和监控

代码路径：/ai-service/monitor/monitor_pipeline.py
**链路 Span 追踪**
层级：Session → Turn → LLM Call → Tool Call 均生成独立 Span，记录父子关系、起止时间、执行状态；Span 数据持久化 MySQL，支持全链路回溯
onSessionStart: 父span
OnLLmStart / OnToolCall / OnTurnStart: 子span

**Prometheus 指标采集**
暴露核心指标：Session 启停、Turn 结束、LLM token 消耗、工具调用次数、LLM 异常计数；
通过 /metrics 端点供 Prometheus 定时抓取

**告警评估**
基于 AlertStreakTracker 实现异常告警评估，
追踪 LLM 调用异常、上下文超限、工具执行错误连续发生次数，支持阈值自动触发告警，告警记录同步存入 mysql
