# AI 数据分析平台

AI 驱动数据应用快速交付平台，支持自然语言生成代码、数据分析、可视化报告，面向数据分析师。

## 技术架构

```
┌──────────────────────────────────────────────────────┐
│                     Frontend (Vue 3)                  │
│         Ant Design Vue + Monaco Editor + ECharts      │
│                    Port: 5173 (dev)                   │
└──────────────────────┬───────────────────────────────┘
                       │ HTTP/SSE
┌──────────────────────▼───────────────────────────────┐
│                  API Gateway (FastAPI)                │
│         动态路由 · JWT 认证 · 限流 · IP 黑名单        │
│                    Port: 8456                         │
└───┬──────────┬──────────┬──────────┬─────────────────┘
    │ gRPC     │ HTTP     │ HTTP     │ HTTP
┌───▼────┐ ┌──▼──────┐ ┌─▼──────┐ ┌─▼──────────────┐
│ User   │ │ App     │ │ AI     │ │ Infrastructure │
│ Service│ │ Service │ │ Service│ │                │
│ gRPC   │ │ FastAPI │ │FastAPI │ │ MySQL · Redis  │
│ :50051 │ │ :8004   │ │ :8002  │ │ Nacos · Prometheus│
└────────┘ └─────────┘ └────────┘ └────────────────┘
                                  
```

| 层级 | 技术选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Ant Design Vue 4 |
| 编辑器 | Monaco Editor (`@guolao/vue-monaco-editor`) |
| 后端框架 | FastAPI (Python 3.12) |
| RPC | gRPC (protobuf) |
| 服务发现 | Nacos |
| 数据库 | MySQL 8.0 (SQLAlchemy + aiomysql 异步) |
| 缓存 | Redis 7 |
| 监控 | Prometheus + OpenTelemetry |
| 容器化 | Docker Compose / Kubernetes |

## 微服务说明

### 1. API Gateway (`backend/api-gateway/`) · Port 8456

统一入口网关，负责请求路由、认证鉴权、限流、IP 黑名单。

- **动态路由** — 基于 `routes.yaml` 配置自动转发 HTTP/gRPC 请求到下游微服务
- **JWT 认证** — 登录签发 Token，后续请求 Bearer 校验
- **显式路由** — 网关特有逻辑（SSE 流式转发、文件上传/下载、Redis 黑名单管理）不走动态路由

### 2. User Service (`backend/services/user-service/`) · gRPC :50051

用户中心微服务，管理账号体系。

- 注册 / 登录 / 登出
- 用户信息查询与修改
- 密码 MD5+盐加密存储
- gRPC 协议，由网关透传调用

### 3. App Service (`backend/services/app-service/`) · Port 8004

项目管理微服务，管理代码项目全生命周期。

- 项目 CRUD（创建、删除、修改、分页查询）
- 代码文件树管理（新建、重命名、删除、上传）
- 代码文件读写（Monaco Editor 在线编辑）
- 脚本在线运行（SSE 流式输出）
- 代码打包下载
- 数据库创建删除

### 4. AI Service (`backend/services/ai-service/`) · Port 8002

核心 AI 能力微服务，承载 Agent 运行时、会话管理、监控。

- 流式代码生成（SSE）
- Session 会话管理
- Token 消耗统计
- Prometheus 指标暴露 (`/metrics`)
- 监控面板（会话追踪、告警、Token 用量）
- 会话历史（列表、消息回溯、活跃检查）

## 主要功能模块

### 项目管理
- 创建/删除/编辑应用项目
- 在线代码编辑器（语法高亮、文件树、多 Tab）
- Python 脚本在线运行，实时查看输出
- 代码文件上传、下载、重命名
- 项目数据库表结构浏览

### AI 对话式开发
- 自然语言描述需求，Agent 自动生成代码
- 多轮对话，支持流式输出
- 会话历史查看与继续
- 会话停止/中断控制
- 用户级频率限制

### 数据可视化 TODO
- ECharts 图表渲染
- 数据表浏览与查询
- 统计面板（Token 消耗、请求量、费用）

### 监控管理 (Admin)
- 会话监控面板（运行中/已完成/失败）
- 告警规则与告警记录
- Token 用量查询
- 历史数据清理

### 用户与权限
- 用户注册 / 登录（JWT）
- 管理员角色（`admin`）
- 普通用户（`user`）项目隔离
- IP 黑名单管理

## Agent 核心技术架构
基于事件触发的AgentRuntime
```
                        ┌─────────────────┐
                        │   MessageBus    │  ← 事件总线
                        └────────┬────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │           AgentRuntime               │
              │  ┌─────────────────────────────────┐ │
              │  │         Dispatch Loop           │ │
              │  └───────────┬─────────────────────┘ │
              │              │                        │
              │  ┌───────────▼──────────┐             │
              │  │     SessionPool      │ ← 会话池    │
              │  │  (max 1000, TTL 1h)  │             │
              │  └───────────┬──────────┘             │
              │              │                        │
              │  ┌───────────▼──────────┐             │
              │  │   Turn Loop (≤50)    │ ← 多轮对话  │
              │  │  ┌────────────────┐  │             │
              │  │  │ Context Builder│  │ ← 上下文    │
              │  │  │ + Compaction   │  │   组装+压缩  │
              │  │  └───────┬────────┘  │             │
              │  │          ▼           │             │
              │  │  ┌────────────────┐  │             │
              │  │  │   LLM Client   │  │ ← 模型    │
              │  │  │  + Recovery    │  │   容错重试   │
              │  │  └───────┬────────┘  │             │
              │  │          ▼           │             │
              │  │  ┌────────────────┐  │             │
              │  │  │ Tool Executor  │  │ ← 工具调用  │
              │  │  │ (14+ tools)    │  │             │
              │  │  └───────┬────────┘  │             │
              │  │          ▼           │             │
              │  │  (loop if tools)     │             │
              │  └──────────────────────┘             │
              └───────────────────────────────────────┘
```

### 核心组件

| 组件 | 说明                                                                  |
|---|---------------------------------------------------------------------|
| **AgentRuntime** | 中央调度器，管理消息总线、会话池、工具注册、钩子系统                                          |
| **SessionPool** | 内存会话池，最多 1000 个并发会话，空闲 1 小时自动回收                                     |
| **Turn Loop** | 多轮对话循环，限制每轮最大迭代次数，每次迭代组装上下文 → LLM 推理 → 工具调用                         |
| **Context Builder** | 动态组装系统提示词 + 历史消息 + 工具结果，超长自动压缩                                      |
| **LLM Client** | 基于 OpenAI 兼容协议，支持 DashScope / DeepSeek / Zhipu / vLLM / 自定义         |
| **Tool System** | 20+ 工具：代码检查、CSV/MySQL 数据操作、文件读写、搜索（find/grep）、任务面板、子 Agent、Skill 加载 |
| **Hook System** | 生命周期钩子：会话启停、轮次启停、LLM 前后、工具前后、错误处理                                   |
| **Memory** | 三级记忆：Hot（全局 MEMORY.md）、Warm（关键词召回主题文件）、Session（当前会话）                |
| **Skill Runtime** | 可插拔技能系统：数据清洗、特征工程、建模、绘图、Python 分析、SQL 分析、报告生成                       |
| **Task Board** | 会话内持久化任务面板，Agent 可自行规划/追踪子任务                                        |
| **Monitor Pipeline** | 全链路追踪：会话录制 → Span 采集 → 指标计算 → 告警评估 → Prometheus 暴露                  |

### LLM 配置

`backend/config.json` 中配置模型与 Provider：

```json
{
  "default_model": "dashscope-qwen/qwen3.7-plus",
  "default_provider": "dashscope",
  "temperature": 0.1,
  "max_tool_iterations": 40,
  "max_steps": 10,
  "providers": {
    "dashscope": { "base_url": "...", "api_key": "..." },
    "deepseek":  { "base_url": "...", "api_key": "..." },
    "openai":    { "base_url": "...", "api_key": "..." },
    "zhipu":     { "base_url": "...", "api_key": "..." },
    "custom":    { "base_url": "...", "api_key": "..." },
    "vllm":      { "base_url": "...", "api_key": "..." }
  }
}
```

## 项目结构

```
CodeGenX/
├── frontend/                     # Vue 3 前端
│   └── src/
│       ├── api/                  # API 接口层
│       ├── components/           # 通用组件
│       ├── composables/          # 组合式函数
│       ├── config/               # 环境配置
│       ├── layouts/              # 布局组件
│       ├── pages/                # 页面
│       │   ├── admin/            # 管理后台
│       │   ├── app/              # 项目工作台
│       │   └── user/             # 登录/注册
│       ├── router/               # 路由配置
│       ├── stores/               # Pinia 状态
│       └── utils/                # 工具函数
├── backend/
│   ├── api-gateway/              # API 网关
│   │   ├── api/                  # 显式路由
│   │   ├── core/                 # 动态路由、代理
│   │   ├── middleware/           # 中间件
│   │   ├── grpc_client/          # gRPC 客户端
│   │   └── proxy/                # 服务代理
│   ├── services/
│   │   ├── user-service/         # 用户服务 (gRPC)
│   │   ├── app-service/          # 应用服务
│   │   └── ai-service/           # AI 服务
│   │       ├── bot/
│   │       │   ├── agent/        # Agent 运行时
│   │       │   ├── tools/        # 工具集 (14+)
│   │       │   ├── memory/       # 多级记忆
│   │       │   ├── llm/          # LLM 客户端
│   │       │   ├── compact/      # 上下文压缩
│   │       │   ├── skill/        # 技能系统 (8)
│   │       │   ├── task/         # 任务面板
│   │       │   ├── hook/         # 生命周期钩子
│   │       │   └── session/      # 会话管理
│   │       └── monitor/          # 监控采集
│   ├── shared/                   # 共享库
│   │   ├── config/               # 配置
│   │   ├── schema/               # Pydantic 模型
│   │   ├── models/               # SQLAlchemy 模型
│   │   └── utils/                # 工具函数
│   ├── infra/                    # 基础设施
│   │   ├── mysql/                # MySQL 会话
│   │   ├── redis/                # Redis 客户端
│   │   ├── nacos/                # Nacos 注册中心
│   │   ├── qdrant/               # 向量数据库
│   │   ├── nginx/                # Nginx 配置
│   │   ├── k8s/                  # Kubernetes 部署
│   │   └── monitoring/           # Prometheus 配置
│   ├── config.json               # Agent LLM 配置
│   ├── routes.yaml               # 动态路由规则
│   └── .env                      # 环境变量
├── docker-compose.yml            # Docker 编排
├── startup.bat                   # Windows 启动脚本
└── shutdown.bat                  # Windows 关闭脚本
```

## 快速启动

<!-- TODO: 请在此处补充你的启动说明 -->


### 环境要求

- Python 3.12+
- Node.js 20+
- MySQL 8.0
- Redis 7
- Nacos 2.0+

### 1. 后端启动

```bash
# 安装依赖
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 配置环境变量 (.env)

# 启动全部服务（Windows）
startup.bat

# 或按需启动
startup.bat user ai app gateway
```

### 2. 前端启动

```bash
cd frontend
pnpm install
pnpm dev
```

### 3. 停止服务

```bash
shutdown.bat
```

### docker方式 TODO

## 其它详细设计说明
session管理： SessionState管理.md
Memory管理：
Compact: