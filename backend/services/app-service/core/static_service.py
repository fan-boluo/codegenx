from __future__ import annotations

from pathlib import Path
import sys

LOCAL_SERVICES_ROOT = Path(__file__).resolve().parents[1] / "services"
if str(LOCAL_SERVICES_ROOT) not in sys.path:
	sys.path.insert(0, str(LOCAL_SERVICES_ROOT))

from static_service import StaticResourceService

__all__ = ["StaticResourceService"]