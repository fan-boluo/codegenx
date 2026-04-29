"""User service entrypoint for gRPC server startup."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_server_module() -> object:
    root = Path(__file__).resolve().parent
    server_path = root / "grpc_service" / "server.py"
    spec = importlib.util.spec_from_file_location("user_service_grpc_server", server_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load user-service gRPC server from {server_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["user_service_grpc_server"] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    server_module = _load_server_module()
    server_module.serve()