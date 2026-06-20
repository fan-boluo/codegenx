# CodeGenX Docker 部署指南

## 架构概览

```
                    ┌─────────────────────────────┐
                    │     api-gateway :8456        │
                    │       (FastAPI)              │
                    └──┬────────┬────────┬────────┘
                       │        │        │
              ┌────────▼──┐ ┌──▼──────┐ ┌▼──────────┐
              │user-service│ │app-svc  │ │ ai-service │
              │  :50051    │ │ :8004   │ │  :8002     │
              │  (gRPC)    │ │(FastAPI)│ │ (FastAPI)  │
              └──────┬─────┘ └──┬──────┘ └──┬────────┘
                     │          │           │
              ┌──────▼──────────▼───────────▼────────┐
              │     MySQL :3306   Redis :6379        │
              │     Nacos  :8848                     │
              └──────────────────────────────────────┘
```

## 服务端口

| 服务 | 端口 | 协议 | 说明 |
|------|------|------|------|
| api-gateway | 8456 | HTTP | 统一入口网关 |
| user-service | 50051 | gRPC | 用户中心 |
| app-service | 8004 | HTTP | 项目管理 |
| ai-service | 8002 | HTTP | AI Agent 运行时 |
| MySQL | 3306 | TCP | 数据库 |
| Redis | 6379 | TCP | 缓存 / 黑名单 |
| Nacos | 8848 | HTTP | 服务注册与发现 |

## 前置准备

### 1. 环境要求

- Docker 20.10+
- Docker Compose 2.0+
- 可用内存 >= 8GB (推荐 16GB)

### 2. 配置文件

部署前需要准备以下配置文件：

```
CodeGenX/
├── backend/
│   ├── .env          # 环境变量（从 .env.example 复制并修改）
│   ├── config.json   # Agent LLM 配置（从 config.json.example 复制并修改）
│   └── routes.yaml   # 路由规则（已提供默认配置）
├── docker-compose.yml
└── DEPLOY.md
```

### 3. 修改环境变量

```bash
# 复制配置文件
cp backend/.env.example backend/.env
cp backend/config.json.example backend/config.json
```

编辑 `backend/.env`，修改以下关键配置：

```ini
# 数据库（MySQL 容器内可通过服务名 mysql 访问）
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_DB=codegenx
MYSQL_USER=root
MYSQL_PASSWORD=boluo123          # 对应 docker-compose 中的 MYSQL_ROOT_PASSWORD

# Redis（容器内）
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=1
REDIS_PASSWORD=boluo123          # 对应 docker-compose 中的 REDIS_PASSWORD

# Nacos（容器内）
NACOS_SERVER_ADDR=nacos:8848
NACOS_NAMESPACE=public
NACOS_USER=nacos
NACOS_PASSWORD=nacos

# 各服务地址（容器内服务名）
USER_SERVICE_HOST=user-service
USER_SERVICE_PORT=50051
APP_SERVICE_HOST=app-service
APP_SERVICE_HTTP_PORT=8004
AI_SERVICE_HOST=ai-service
AI_SERVICE_HTTP_PORT=8002
```

> 注意：Docker 环境内各服务通过**容器名**互相访问，host 必须改为容器名（如 `mysql`、`redis`、`nacos`）。

### 4. 配置 LLM 模型

编辑 `backend/config.json`，设置你的 LLM Provider 和 API Key：

```json
{
  "agents": [
    {
      "id": "01",
      "name": "main",
      "defaults": true,
      "model": "deepseek-chat",
      "provider": "deepseek",
      "temperature": 0.1,
      "max_tool_iterations": 40,
      "max_steps": 10,
      "maxSessions": 1000,
      "sessionIdleTimeoutSeconds": 1800
    }
  ],
  "providers": {
    "deepseek": {
      "apiKey": "sk-your-deepseek-api-key",
      "apiBase": "https://api.deepseek.com/v1"
    },
    "dashscope": {
      "apiKey": "sk-your-dashscope-key",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }
  }
}
```

支持的 Provider：`dashscope`、`deepseek`、`openai`、`zhipu`、`vllm`、`custom`

## 快速部署

### 方式一：一键启动全部服务

```bash
# 在项目根目录（docker-compose.yml 所在目录）
docker compose up -d --build
```

### 方式二：分步启动

```bash
# 1. 先启动基础设施
docker compose up -d mysql redis nacos

# 2. 等待基础设施就绪后，启动微服务
docker compose up -d user-service app-service ai-service

# 3. 最后启动网关
docker compose up -d api-gateway
```

### 验证部署

```bash
# 检查所有容器状态
docker compose ps

# 检查日志
docker compose logs -f api-gateway

# 健康检查
curl http://localhost:8456/health
# 预期返回: {"code": 0, "message": "ok"}

# 测试 AI 服务（需要先注册用户获取 token）
curl http://localhost:8456/api/ai/sessions/1
```

### 查看各服务日志

```bash
docker compose logs -f --tail=50 api-gateway
docker compose logs -f --tail=50 ai-service
docker compose logs -f --tail=50 app-service
docker compose logs -f --tail=50 user-service
```

## 环境变量参考

`backend/.env` 中所有可配置的环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MYSQL_HOST` | mysql | MySQL 地址 |
| `MYSQL_PORT` | 3306 | MySQL 端口 |
| `MYSQL_DB` | codegenx | 数据库名 |
| `MYSQL_USER` | root | 数据库用户 |
| `MYSQL_PASSWORD` | - | 数据库密码 |
| `REDIS_HOST` | redis | Redis 地址 |
| `REDIS_PORT` | 6379 | Redis 端口 |
| `REDIS_DB` | 1 | Redis 数据库编号 |
| `REDIS_PASSWORD` | - | Redis 密码 |
| `NACOS_SERVER_ADDR` | nacos:8848 | Nacos 地址 |
| `NACOS_NAMESPACE` | public | Nacos 命名空间 |
| `USER_SERVICE_HOST` | user-service | 用户服务地址 |
| `USER_SERVICE_PORT` | 50051 | 用户服务 gRPC 端口 |
| `APP_SERVICE_HOST` | app-service | 应用服务地址 |
| `APP_SERVICE_HTTP_PORT` | 8004 | 应用服务 HTTP 端口 |
| `AI_SERVICE_HOST` | ai-service | AI 服务地址 |
| `AI_SERVICE_HTTP_PORT` | 8002 | AI 服务 HTTP 端口 |
| `JWT_SECRET` | - | JWT 签名密钥 |
| `JWT_ALGORITHM` | HS256 | JWT 算法 |
| `JWT_EXPIRATION_HOURS` | 24 | JWT 过期时间 |

## 常用操作

### 停止服务

```bash
docker compose down
```

### 停止并清除数据（数据卷）

```bash
docker compose down -v
```

### 重建单个服务

```bash
docker compose up -d --build ai-service
```

### 进入容器

```bash
docker compose exec ai-service bash
docker compose exec mysql mysql -uroot -p
```

## 各服务 requirements.txt 说明

| 服务 | 依赖 | 说明 |
|------|------|------|
| **api-gateway** | fastapi, uvicorn, httpx, pydantic, redis, grpcio, PyJWT, PyYAML, python-dotenv, starlette | 网关路由 + JWT + Redis 黑名单 |
| **user-service** | grpcio, protobuf, sqlalchemy, aiomysql, greenlet | gRPC 服务 + MySQL ORM |
| **app-service** | fastapi, uvicorn, sqlalchemy, aiomysql, greenlet, pymysql, starlette | 项目管理 API |
| **ai-service** | fastapi, uvicorn, pydantic, pydantic-settings, httpx, aiofiles, sqlalchemy, aiomysql, greenlet, openai, prometheus-client, loguru, PyYAML, numpy, starlette | Agent 运行时 + LLM + 监控 |

所有服务的 requirements.txt 均基于**实际代码导入**精确整理，不包含未使用的依赖。

## 可选：Prometheus 监控

docker-compose.yml 默认未包含 Prometheus。如需启用监控，添加以下配置：

```yaml
  prometheus:
    image: prom/prometheus:latest
    container_name: codegenx-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./backend/infra/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    networks:
      - codegenx-net
```

AI 服务的 metrics 端点：`http://ai-service:8002/metrics`
