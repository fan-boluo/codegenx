# Session 生命周期管理

## 概述

CodeGenX 的会话（session）由三层状态共同决定：**前端 localStorage**、**后端内存池（session_pool）**、**磁盘持久化（JSONL 文件）**。每层各有独立过期策略。

## 过期时间

| 层级 | 失效条件               | 时间        |
|---|--------------------|-----------|
| JWT token | 登录凭证过期             | **24 小时** |
| 前端空闲登出 | 用户 30 分钟无操作        | **30 分钟** |
| 后端 session_pool | 内存池闲置驱逐            | **30 分钟** |
| 磁盘 session 数据 | 文件5天自动清理 | **5天**    |

## Session ID 生命周期

### 何时新建

| 条件 | 说明 |
|---|---|
| 用户退出→重新登录 | `doLogout` 清除所有 `codegenx:app-chat-session:*` localStorage 键，下次打开为新 session |
| 用户点击「新会话」按钮 | `createNewSession` 清除 localStorage、生成新 ID、清空聊天消息 |
| 打开页面时 session 已过期 | `checkSessionAlive` 返回 false → 清掉旧 localStorage → 生成新 ID |
| 首次打开项目 | localStorage 中无 session 记录 → `ensureChatSessionId` 生成新 ID |
| 空闲登出 | `performIdleLogout` 清除 token 和 session 键，下次登录等同新用户 |
| 发消息时 session 被驱逐 | 后端 `get_or_create` 发现 pool 中无此 session → 自动重建（无差别的重新开始），前端不感知 |

### 何时复用

| 条件 | 说明 |
|---|---|
| 页面打开，session 还在 pool 中 | `checkSessionAlive` 返回 true → 加载历史消息，继续聊天 |
| 正常聊天过程中 | 后端 `get_or_create` 命中 → touch 更新 `last_activity_at` → 重置 30 分钟倒计时 |

## 状态恢复流程

```
打开项目页面
  │
  ├─ localStorage 中有 sessionId？
  │   ├─ 是 → GET /api/.../alive 检查存活
  │   │   ├─ alive=true → 加载历史消息 → 接着聊
  │   │   └─ alive=false → 清理 localStorage → 新 sessionId → 空白页
  │   └─ 否 → 新 sessionId → 空白页
```

## 空闲自动登出

`frontend/src/composables/useIdleTimeout.ts` 在 `GlobalHeader.vue` 中启用：

- 监听全局 `mousemove` / `keydown` / `click` / `scroll` / `touchstart`
- 30 分钟无操作 → 清除 token + 所有 session localStorage → 跳转登录页
- 多次操作会不断重置倒计时

## 关键 API

| 端点 | 说明 |
|---|---|
| `GET /api/chat/sessions/{appId}/{sessionId}/alive` | 检查 session 在内存池是否存活 |
| `GET /api/chat/sessions/{appId}/{sessionId}/messages` | 从磁盘加载 session 历史消息 |
| `GET /api/chat/sessions/{appId}` | 列出 app 下最近 sessions |

## 相关文件

| 文件 | 职责 |
|---|---|
| `frontend/src/pages/app/AppChatPage.vue` | 会话恢复逻辑、消息加载、新建会话 |
| `frontend/src/components/GlobalHeader.vue` | 退出登录清理、空闲超时检测 |
| `frontend/src/composables/useIdleTimeout.ts` | 全局空闲检测 |
| `backend/services/ai-service/bot/agent/session_pool.py` | 内存池管理、LRU 驱逐、空闲清理 |
| `backend/services/ai-service/bot/session/manager.py` | 磁盘 session 数据读写 |
| `backend/services/ai-service/app.py` | alive 端点 |
| `backend/api-gateway/api/chat.py` | 透传 alive + 其他会话 API |
