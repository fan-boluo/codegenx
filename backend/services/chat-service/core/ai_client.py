from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_service_root = Path(__file__).resolve().parents[3] / "services" / "app-service" / "core"
_module_path = _service_root / "ai_client.py"
if str(_module_path.parent) not in sys.path:
    sys.path.insert(0, str(_module_path.parent))

spec = importlib.util.spec_from_file_location("chat_service_ai_client_module", _module_path)
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load ai client module from {_module_path}")
module = importlib.util.module_from_spec(spec)
sys.modules["chat_service_ai_client_module"] = module
spec.loader.exec_module(module)

AiServiceClient = getattr(module, "AiServiceClient")

__all__ = ["AiServiceClient"]