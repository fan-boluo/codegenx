-- ============================================================
-- 记忆系统元数据表
-- 依据：docs/记忆系统详细设计方案.md 第 3.3 节
-- 说明：记忆内容本体存于 json 真相源与 Qdrant，此处只存
--       离线任务队列 / 会话消费水位 / 双数据源同步检查点。
-- 使用：与 init/schema.sql 同库（codegenx），部署方手动执行。
-- ============================================================

use codegenx;

-- ------------------------------------------------------------
-- 离线任务队列
-- 状态机：pending -> running -> done
--                    └-> 失败：retry_count+1，按退避(1m/5m/30m)回 pending；超限 -> dead
-- 语义：at-least-once，重复执行由写入侧判重兜底
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_task (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_type   VARCHAR(32)  NOT NULL COMMENT '任务类型：warm_extract/consolidate/decay_archive/sync_check',
    app_id      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '应用id',
    user_id     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '用户id',
    session_id  VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '会话id（全局任务为空串）',
    status      VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT '状态：pending/running/done/dead',
    retry_count INT          NOT NULL DEFAULT 0 COMMENT '已重试次数',
    next_run_at BIGINT       NOT NULL DEFAULT 0 COMMENT '最早可领取时间（unix秒），退避重试用',
    payload     JSON         NULL COMMENT '任务附加参数（如触发的轮次数）',
    last_error  VARCHAR(2000) NULL COMMENT '最近一次失败原因',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_status_next (status, next_run_at),
    INDEX idx_session_type (session_id, task_type, status)
) COMMENT '记忆离线任务队列' COLLATE = utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 会话消息消费水位
-- 记忆提取按会话增量消费 jsonl 对话文件，崩溃后从水位续提。
-- 对话文件无全局 rowid，水位用 (file_name, line_no) 定位。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_watermark (
    session_id    VARCHAR(64) PRIMARY KEY COMMENT '会话id',
    app_id        VARCHAR(64) NOT NULL DEFAULT '' COMMENT '应用id',
    user_id       VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户id',
    last_file_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '已消费到的对话文件名（chat_history_*.jsonl）',
    last_line_no  INT         NOT NULL DEFAULT 0 COMMENT '已消费到的行号（含）',
    updated_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) COMMENT '记忆提取会话消费水位' COLLATE = utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- 双数据源同步检查点
-- json 为真相源，Qdrant 为检索层；每次双写完成后推进检查点，
-- 定时对账任务从检查点起扫描 json 侧增量，补写/修正 Qdrant。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_sync_checkpoint (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    app_id        VARCHAR(64) NOT NULL COMMENT '应用id',
    scope         VARCHAR(32) NOT NULL COMMENT '同步范围：warm',
    last_entry_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '已同步到向量库的最大记忆id（ULID，单调可比）',
    updated_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_app_scope (app_id, scope)
) COMMENT '记忆双数据源同步检查点' COLLATE = utf8mb4_unicode_ci;
