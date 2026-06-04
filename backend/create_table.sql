-- 创建库
create database if not exists codegenx;

-- 切换库
use codegenx;

-- 以下是建表语句

-- 用户表
create table if not exists user
(
    id           bigint auto_increment comment 'id' primary key,
    userAccount  varchar(256)                           not null comment '账号',
    userPassword varchar(512)                           not null comment '密码',
    userName     varchar(256)                           null comment '用户昵称',
    userAvatar   varchar(1024)                          null comment '用户头像',
    userProfile  varchar(512)                           null comment '用户简介',
    userRole     varchar(256) default 'user'            not null comment '用户角色：user/admin',
    editTime     datetime     default CURRENT_TIMESTAMP not null comment '编辑时间',
    createTime   datetime     default CURRENT_TIMESTAMP not null comment '创建时间',
    updateTime   datetime     default CURRENT_TIMESTAMP not null on update CURRENT_TIMESTAMP comment '更新时间',
    isDelete     tinyint      default 0                 not null comment '是否删除',
    UNIQUE KEY uk_userAccount (userAccount),
    INDEX idx_userName (userName)
) comment '用户' collate = utf8mb4_unicode_ci;

-- 应用表
create table if not exists app
(
    id           bigint auto_increment comment 'id' primary key,
    appName      varchar(128)                           not null comment '应用名称',
    cover        varchar(1024)                          null comment '应用封面',
    initPrompt   varchar(4096)                          not null comment '应用初始化的 prompt',
    codeGenType  varchar(64)                            not null comment '代码生成类型（枚举）',
    deployKey    varchar(128)                           null comment '部署标识',
    deployedTime datetime                           null comment '部署时间',
    priority     int          default 0                 not null comment '优先级',
    userId       bigint                                 not null comment '创建用户id',
    editTime     datetime                               null comment '编辑时间',
    createTime   datetime     default CURRENT_TIMESTAMP not null comment '创建时间',
    updateTime   datetime     default CURRENT_TIMESTAMP not null on update CURRENT_TIMESTAMP comment '更新时间',
    isDelete     tinyint      default 0                 not null comment '是否删除',
    UNIQUE KEY uk_deployKey (deployKey), -- 确保部署标识唯一
    INDEX idx_appName (appName),         -- 提升基于应用名称的查询性能
    INDEX idx_userId (userId)            -- 提升基于用户 ID 的查询性能
) comment '应用' collate = utf8mb4_unicode_ci;


CREATE TABLE `spans` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `app_id` varchar(10) NOT NULL DEFAULT '',
  `user_id` varchar(10) DEFAULT '',
  `trace_id` VARCHAR(32) NOT NULL,
  `span_id` VARCHAR(32) NOT NULL,
  `parent_span_id` VARCHAR(32) DEFAULT NULL,
  `session_id` VARCHAR(32) NOT NULL,
  `request_id` VARCHAR(32) DEFAULT '',
  `step_counter` int DEFAULT '0',
  `operation_type` varchar(20) NOT NULL,
  `start_time` datetime(3) NOT NULL,
  `end_time` datetime(3) DEFAULT NULL,
  `duration_ms` int DEFAULT NULL,
  `status` varchar(10) DEFAULT 'running',
  `attributes` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_app_start_time` (`app_id`,`start_time`),
  KEY `idx_trace_id` (`trace_id`),
  KEY `idx_session_turn` (`session_id`,`step_counter`),
  KEY `idx_start_time` (`start_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci

CREATE TABLE session_metrics (
    session_id VARCHAR(32) PRIMARY KEY,
    trace_id VARCHAR(32) NOT NULL,
    request_id VARCHAR(32) NOT NULL DEFAULT '',
    app_id VARCHAR(10) NOT NULL DEFAULT '',
    user_id VARCHAR(10),
    model VARCHAR(32) NOT NULL DEFAULT 'unknown',
    span_id VARCHAR(32) NOT NULL DEFAULT '',

    status VARCHAR(16) DEFAULT 'running',
    end_reason VARCHAR(32),
    turn_number INT DEFAULT 0,
    token_count INT DEFAULT 0,
    token_usage REAL DEFAULT 0.0,
    is_compress BOOL DEFAULT FALSE,

    total_prompt_tokens BIGINT DEFAULT 0,
    total_completion_tokens BIGINT DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,
    llm_recovery_count INT DEFAULT 0,
    last_recovery_kind VARCHAR(32) DEFAULT '',

    total_tool_calls INT DEFAULT 0,
    total_tool_call_errors INT DEFAULT 0,
    total_memory_hits INT DEFAULT 0,
    memory_is_error BOOL DEFAULT FALSE,

    started_at DATETIME(3) NOT NULL,
    ended_at DATETIME(3),
    duration_ms INT DEFAULT 0,
    updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),

    INDEX idx_session_metrics_trace (trace_id),
    INDEX idx_session_metrics_app_status (app_id, status),
    INDEX idx_session_metrics_updated (updated_at)
);

CREATE TABLE turn_metrics (
    turn_id VARCHAR(32) PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    trace_id VARCHAR(32) NOT NULL,
    request_id VARCHAR(32) NOT NULL DEFAULT '',
    app_id VARCHAR(10) NOT NULL DEFAULT '',
    user_id VARCHAR(10) NOT NULL DEFAULT '',
    model VARCHAR(32) NOT NULL DEFAULT 'unknown',
    span_id VARCHAR(32) NOT NULL DEFAULT '',

    status VARCHAR(16) DEFAULT 'running',
    end_reason VARCHAR(32),
    turn_number INT DEFAULT 0,
    token_count INT DEFAULT 0,
    token_usage REAL DEFAULT 0.0,
    is_compress BOOL DEFAULT FALSE,

    total_prompt_tokens BIGINT DEFAULT 0,
    total_completion_tokens BIGINT DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,
    llm_recovery_count INT DEFAULT 0,
    last_recovery_kind VARCHAR(32) DEFAULT '',

    total_tool_calls INT DEFAULT 0,
    total_tool_call_errors INT DEFAULT 0,
    total_memory_hits INT DEFAULT 0,
    memory_is_error BOOL DEFAULT FALSE,

    started_at DATETIME(3) NOT NULL,
    ended_at DATETIME(3),
    duration_ms INT DEFAULT 0,
    updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),

    INDEX idx_turn_metrics_trace (trace_id),
    INDEX idx_session_turn (session_id, turn_number),
    INDEX idx_request_turn (session_id, request_id, turn_number),
    INDEX idx_turn_metrics_status (status, updated_at)
);

CREATE TABLE monitor_alerts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_name VARCHAR(64) NOT NULL,
    level VARCHAR(16) NOT NULL,
    trace_id CHAR(32) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) DEFAULT '',
    status VARCHAR(16) DEFAULT 'open',
    message VARCHAR(255) NOT NULL,
    observed_value VARCHAR(128) DEFAULT '',
    threshold_value VARCHAR(128) DEFAULT '',
    triggered_at DATETIME(3) NOT NULL,
    resolved_at DATETIME(3),
    payload JSON,
    INDEX idx_alert_rule_status (rule_name, status),
    INDEX idx_alert_session (session_id),
    INDEX idx_alert_triggered_at (triggered_at)
);