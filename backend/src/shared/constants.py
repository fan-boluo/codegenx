from pathlib import Path

# ----------------------页面展示------------------------------
DEFAULT_PAGE_NUM = 1
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


# Safety settings TODO 前端添加
MAX_PROMPT_LENGTH = 10000
MAX_CODE_LENGTH = 50000

# ---------------------------------------------------------
# 用户数据位置，日志位置
# ---------------------------------------------------------
# 日志
# 获得当前项目的绝对路径（src/shared/constants.py → 上跳 3 级为仓库根）
ROOT_DIR = Path(__file__).resolve().parents[3]
LOG_DIR = ROOT_DIR / "logs"  # 存放项目日志目录的绝对路径
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"  # 存储日志的文件

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

DATA_ROOT_DIR = Path(__file__).resolve().parents[3] / ".data"

APPS_DIR = DATA_ROOT_DIR / "apps"
APPS_CODE_DIR = APPS_DIR / "code"
APPS_DEPLOY_DIR = APPS_DIR / "deploy"

def get_runtime_app_dir(app_id: str | int) -> Path:
    return DATA_ROOT_DIR / str(app_id)

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

# ------------------------监控---------------------------
# 是否开启
TELEMETRY_OPEN = False

# ----------------------python虚拟环境--------------------
PYTHON_ENV = {
    "model":r"D:\CondaEnvs\model\python.exe"
}