import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

CONTEXT_LIMIT = 8000
TOKEN_THRESHOLD = 50000
KEEP_RECENT_TOOL_RESULTS = 3

