from enum import StrEnum, Enum
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


class CodeGenType(Enum):
    HTML = "html"
    MULTI_FILE = "multi_file"
    VUE_PROJECT = "vue_project"

class ChatHistoryMessageType:
    USER = "user"
    AI = "ai"

# 调用大模型
API_KEY_PREFIX = "sk-"
API_KEY_STATUS_ACTIVE = "active"
API_KEY_STATUS_REVOKED = "revoked"
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

DEFAULT_AI_MODEL = "qwen-plus"
DEFAULT_AI_PROVIDER = "dashscope"

# Code generation types
CODE_GEN_TYPE_HTML = 0
CODE_GEN_TYPE_MULTI_FILE = 1
CODE_GEN_TYPE_VUE_PROJECT = 2

# Chat memory settings
CHAT_MEMORY_MAX_TURNS = 50
CHAT_MEMORY_TTL_HOURS = 24

# Safety settings
MAX_PROMPT_LENGTH = 10000
MAX_CODE_LENGTH = 50000

# Streaming settings
STREAM_CHUNK_SIZE = 1024
STREAM_TIMEOUT_SECONDS = 300

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

BOT_ROOT_DIR = Path.home() / ".bot"
BOT_WORKSPACE_DIR = BOT_ROOT_DIR / "workspace"
BOT_APPS_ROOT = BOT_ROOT_DIR / "apps"
BOT_APP_RUNTIME_ROOT = BOT_WORKSPACE_DIR
BOT_APP_CODE_ROOT = BOT_APPS_ROOT / "code"
BOT_APP_DEPLOY_ROOT = BOT_APPS_ROOT / "deploy"
BOT_DEFAULT_SESSION_DIR = BOT_WORKSPACE_DIR / "session"
BOT_DEFAULT_MEMORY_DIR = BOT_WORKSPACE_DIR / "memory"
CHAT_HISTORY_ROOT = BOT_APP_RUNTIME_ROOT
CHAT_HISTORY_FILE_NAME = "chat_history.jsonl"
CHAT_HISTORY_ARCHIVE_PREFIX = "chat_"
CHAT_HISTORY_MAX_BYTES = 10 * 1024 * 1024
CHAT_HISTORY_CACHE_TURNS = 20

GOOD_APP_PRIORITY = 99
DEFAULT_APP_PRIORITY = 0

CODE_OUTPUT_ROOT_DIR = BOT_APP_CODE_ROOT
CODE_DEPLOY_ROOT_DIR = BOT_APP_DEPLOY_ROOT
CODE_DEPLOY_HOST = "http://localhost"


def get_bot_runtime_app_dir(app_id: str | int) -> Path:
    return BOT_APP_RUNTIME_ROOT / str(app_id)


def get_bot_context_dir(app_id: str | int) -> Path:
    return get_bot_runtime_app_dir(app_id) / "context"


def get_bot_memory_dir(app_id: str | int) -> Path:
    return get_bot_runtime_app_dir(app_id) / "memory"


def get_bot_session_dir(app_id: str | int) -> Path:
    return get_bot_runtime_app_dir(app_id) / "session"


def get_bot_code_dir(app_id: str | int) -> Path:
    return BOT_APP_CODE_ROOT / str(app_id)


def get_bot_deploy_dir(deploy_key: str) -> Path:
    return BOT_APP_DEPLOY_ROOT / deploy_key