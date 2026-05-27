# CodeGenX

AI 驱动的代码生成平台，支持自然语言描述 → 代码生成 → 可视化编辑 → 云端部署的全流程。

## 技术栈

- **语言**: Python 3.12 + FastAPI
- **前端**: Vue 3 + TypeScript
- **大模型**: 多 Provider 适配（OpenAI / DeepSeek / DashScope / ZhiPu）
- **服务发现**: Nacos
- **通信协议**: HTTP/SSE + gRPC
- **存储**: MySQL → Qdrant → Redis → COS
- **监控**: Prometheus + 自定义指标管线
- **部署**: Docker → K8s

## 微服务架构

```
前端 (Vue 3)
    │ HTTP/SSE
    ▼
API Gateway  ──JWT 鉴权、IP 黑名单、限流──
    │
    ├──▶ user-service  (gRPC)     用户注册/登录/查询
    ├──▶ app-service   (HTTP)     应用管理、代码保存/下载、聊天历史
    └──▶ ai-service    (HTTP/SSE) Agent 代码生成、模型调用、监控
```

| 服务 | 端口 | 协议 | 职责 |
|------|------|------|------|
| api-gateway | 8000 | HTTP | 统一入口、鉴权、限流、黑名单、路由转发 |
| user-service | 8001 | gRPC | 用户注册/登录/信息查询 |
| ai-service | 8002 | HTTP/SSE | Agent 代码生成、LLM 调用编排、上下文/记忆/压缩 |
| app-service | 8004 | HTTP | 应用 CRUD、代码持久化、聊天历史、静态资源 |

## 关键概念

- **Session**: 一次完整的用户对话会话
- **Turn**: 一次用户提问 + Agent 回答
- **Step**: 一个 Turn 内的单次 LLM 调用 + 工具执行

## 快速开始

### 环境准备

```bash
# 安装依赖
pip install -r backend/requirements.txt

# 配置环境变量
cp backend/.env.example backend/.env
# 编辑 .env 填入实际的 API Key、数据库连接等
```

### 启动服务

```bash
# 启动 ai-service
cd backend/services/ai-service
python app.py

# 启动 app-service
cd backend/services/app-service
python app.py

# 启动 user-service
cd backend/services/user-service
python main.py

# 启动 api-gateway
cd backend/api-gateway
python run.py
```

## 目录结构

```
CodeGenX/
├── frontend/                  Vue 3 前端
├── backend/
│   ├── api-gateway/           网关服务
│   │   ├── api/               API 路由
│   │   ├── proxy/             HTTP 代理
│   │   ├── middleware/        中间件（JWT、限流、黑名单）
│   │   └── services/          网关级服务（服务发现）
│   ├── services/
│   │   ├── ai-service/        AI 代码生成（核心 Agent）
│   │   ├── app-service/       应用管理层
│   │   └── user-service/      用户服务（gRPC）
│   ├── infra/                 基础设施
│   │   ├── k8s/               K8s 部署文件
│   │   ├── mysql/             MySQL 客户端
│   │   ├── redis/             Redis 客户端
│   │   ├── nacos/             Nacos 客户端
│   │   └── monitoring/        Prometheus 配置 & 告警规则
│   └── shared/                共享模块（Schema、Config、异常）
└── design.md                  设计文档
```

## 监控

- **指标暴露**: `GET /metrics` 输出 Prometheus 格式指标
- **内部查询**: `GET /internal/monitor/metrics`（兼容路径）
- **告警规则**: `infra/monitoring/prometheus_alerts.yml`
- **管理接口**: `GET /internal/monitor/overview` /sessions /cleanup 等

详见 [设计文档](design.md)。
