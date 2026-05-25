CREATE TABLE spans (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    app_id VARCHAR(64) NOT NULL DEFAULT 'main',
    user_id VARCHAR(64) DEFAULT '',
    trace_id CHAR(32) NOT NULL,
    span_id CHAR(16) NOT NULL,
    parent_span_id CHAR(16),
    session_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) DEFAULT '',
    turn_number INT DEFAULT 0,
    operation_name VARCHAR(128) NOT NULL,
    start_time DATETIME(3) NOT NULL,
    end_time DATETIME(3),
    duration_ms INT,
    status VARCHAR(10) DEFAULT 'running',
    attributes JSON,
    INDEX idx_app_start_time (app_id, start_time),
    INDEX idx_trace_id (trace_id),
    INDEX idx_session_turn (session_id, turn_number),
    INDEX idx_start_time (start_time)
);

CREATE TABLE session_metrics (
    session_id VARCHAR(64) PRIMARY KEY,
    trace_id CHAR(32) NOT NULL,
    request_id VARCHAR(64) NOT NULL DEFAULT '',
    app_id VARCHAR(64) NOT NULL DEFAULT 'main',
    user_id VARCHAR(64),
    model VARCHAR(32) NOT NULL DEFAULT 'unknown',
    span_id VARCHAR(64) NOT NULL DEFAULT '',

    status VARCHAR(16) DEFAULT 'running',
    end_reason VARCHAR(32),
    turn_number INT DEFAULT 0,
    token_count INT DEFAULT 0,
    token_usage REAL DEFAULT 0.0,
    is_compress BOOL DEFAULT FALSE,

    total_prompt_tokens BIGINT DEFAULT 0,
    total_completion_tokens BIGINT DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,
    max_duration_ms INT DEFAULT 0,
    min_duration_ms INT DEFAULT 999999,
    recovery_count INT DEFAULT 0,
    last_recovery_kind VARCHAR(32) DEFAULT '',

    total_tool_calls INT DEFAULT 0,
    total_tool_call_errors INT DEFAULT 0,
    total_memory_hits INT DEFAULT 0,

    started_at DATETIME(3) NOT NULL,
    ended_at DATETIME(3),
    duration_ms INT DEFAULT 0,
    updated_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),

    INDEX idx_session_metrics_trace (trace_id),
    INDEX idx_session_metrics_app_status (app_id, status),
    INDEX idx_session_metrics_updated (updated_at)
);

CREATE TABLE turn_metrics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id CHAR(32) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    request_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) NOT NULL,
    app_id VARCHAR(64) NOT NULL DEFAULT 'main',
    user_id VARCHAR(64),
    model VARCHAR(32) NOT NULL DEFAULT 'unknown',
    span_id VARCHAR(64) NOT NULL DEFAULT '',

    turn_number INT DEFAULT 0,
    status VARCHAR(16) DEFAULT 'running',
    end_reason VARCHAR(32) DEFAULT '',
    token_count INT DEFAULT 0,
    token_usage REAL DEFAULT 0.0,
    is_compress BOOL DEFAULT FALSE,

    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,
    llm_latency_ms INT,
    first_token_ms INT,
    max_duration_ms INT,
    min_duration_ms INT,
    recovery_count INT DEFAULT 0,
    last_recovery_kind VARCHAR(32) DEFAULT '',

    tool_calls_count INT DEFAULT 0,
    total_tool_call_errors INT DEFAULT 0,
    memory_hits INT DEFAULT 0,

    started_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    ended_at DATETIME(3),
    duration_ms INT DEFAULT 0,
    created_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),

    INDEX idx_turn_metrics_trace (trace_id),
    INDEX idx_session_turn (session_id, turn_number),
    INDEX idx_request_turn (session_id, request_id, turn_number),
    INDEX idx_turn_metrics_status (status, created_at)
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