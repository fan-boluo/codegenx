from enum import StrEnum
from pathlib import Path


# 字符串枚举类
class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"

# 定义不同部门，方便后续的权限区分
class Department(StrEnum):
    DEP1 = "dep1"
    DEP2 = "dep2"
    DEP3 = "dep3"


USER_LOGIN_STATE = "user_login"
SESSION_COOKIE_NAME = "SESSION"
SESSION_KEY_PREFIX = "session:"
SESSION_EXPIRE_SECONDS = 30 * 24 * 60 * 60

PASSWORD_SALT = "yupi"
DEFAULT_USER_NAME = "无名"
DEFAULT_USER_PASSWORD = "12345678"


DEFAULT_PAGE_NUM = 1
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


class ChatHistoryMessageType:
    USER = "user"
    AI = "ai"

# 调用大模型
REQUEST_STATUS_SUCCESS = "success"
REQUEST_STATUS_FAILED = "failed"
CHAT_OBJECT = "chat.completion"
DEFAULT_LOG_LIMIT = 100

MODEL_TYPE_CHAT = "chat"

# 模型状态
MODEL_STATUS_ACTIVE = "active"
MODEL_STATUS_INACTIVE = "inactive"
MODEL_STATUS_DEPRECATED = "deprecated"
PROVIDER_STATUS_ACTIVE = "active"
PROVIDER_STATUS_INACTIVE = "inactive"
PROVIDER_STATUS_MAINTENANCE = "maintenance"



# 模型健康状况
HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_UNHEALTHY = "unhealthy"
HEALTH_STATUS_DEGRADED = "degraded"
HEALTH_STATUS_UNKNOWN = "unknown"

# 路由策略
ROUTING_STRATEGY_AUTO = "auto"
ROUTING_STRATEGY_COST_FIRST = "cost_first"
ROUTING_STRATEGY_LATENCY_FIRST = "latency_first"
ROUTING_STRATEGY_ROUND_ROBIN = "round_robin"
ROUTING_STRATEGY_FIXED = "fixed"
MAX_FALLBACK_RETRIES = 3

# 健康检查
HEALTH_CHECK_INTERVAL_SECONDS = 300  # 5分钟一次，避免请求频率频繁
HEALTH_CHECK_TIMEOUT_MS = 10000
HEALTH_CHECK_STATS_HOURS = 24
HEALTH_CHECK_MAX_HISTORY_SIZE = 100

SCORE_COST_WEIGHT = 0.3
SCORE_LATENCY_WEIGHT = 0.3
SCORE_SUCCESS_RATE_WEIGHT = 0.2
SCORE_PRIORITY_WEIGHT = 0.2


# Chat memory_bak settings
CHAT_MEMORY_MAX_TURNS = 50
CHAT_MEMORY_TTL_HOURS = 24

# Safety settings
MAX_PROMPT_LENGTH = 10000
MAX_CODE_LENGTH = 50000

# Streaming settings
STREAM_CHUNK_SIZE = 1024
STREAM_TIMEOUT_SECONDS = 300

"""
这是一个远程服务，不是本地部署的，不在用户根目录下，而是和应用代码放在一处的
-- frontend
-- backend
-- data
  app1
    -- code  源代码
    -- deploy 部署安装包
    -- memory
    -- context
    -- session
  
  app2

""" 

ROOT_DIR = Path(__file__).resolve().parents[2] / ".data"

APPS_DIR = ROOT_DIR / "apps"
APPS_CODE_DIR = APPS_DIR / "code"
APPS_DEPLOY_DIR = APPS_DIR / "deploy"

# WORKSPACE_DIR = ROOT_DIR / "workspace"

CHAT_HISTORY_FILE_NAME = "chat_history.jsonl"
CHAT_HISTORY_ARCHIVE_PREFIX = "chat_"
CHAT_HISTORY_MAX_BYTES = 10 * 1024 * 1024
CHAT_HISTORY_CACHE_TURNS = 20

GOOD_APP_PRIORITY = 99
DEFAULT_APP_PRIORITY = 0


def get_runtime_app_dir(app_id: str | int) -> Path:
    return ROOT_DIR / str(app_id)

def get_code_dir(app_id: str | int) -> Path:
    return get_runtime_app_dir(app_id) / "code"


def get_deploy_dir(app_id:str) -> Path:
    return get_runtime_app_dir(app_id) / "deploy"

def get_context_dir(app_id: str | int) -> Path:
    return get_runtime_app_dir(app_id) / "context"


def get_memory_dir(app_id: str | int) -> Path:
    return get_runtime_app_dir(app_id) / "memory"


def get_session_dir(app_id: str | int) -> Path:
    return get_runtime_app_dir(app_id) / "session"

def get_current_session_dir(app_id: str | int,session_id:str) -> Path:
    return get_session_dir(app_id) / session_id



# 压缩
PERSIST_THRESHOLD = 30000  # 压缩阈值
PREVIEW_CHARS = 2000  # 展示的长度

# 监控
# 是否开启
TELEMETRY_OPEN = False