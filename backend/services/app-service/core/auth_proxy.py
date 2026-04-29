from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
_api_gateway_root = _repo_root / "api-gateway"
if str(_api_gateway_root) not in sys.path:
    sys.path.insert(0, str(_api_gateway_root))
_auth_module_path = _repo_root / "api-gateway" / "middleware" / "auth.py"

spec = importlib.util.spec_from_file_location("app_service_auth_module", _auth_module_path)
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load auth module from {_auth_module_path}")
module = importlib.util.module_from_spec(spec)
sys.modules["app_service_auth_module"] = module
spec.loader.exec_module(module)

require_login = getattr(module, "require_login")
require_role = getattr(module, "require_role")
JWTUser = getattr(module, "JWTUser")

__all__ = ["JWTUser", "require_login", "require_role"]
