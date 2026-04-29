"""Dynamic proto loader for gRPC stubs."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from grpc_tools import protoc


def compile_proto(proto_path: Path) -> None:
    """Compile a .proto file into Python gRPC modules."""
    output_dir = proto_path.parent
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "protoc",
        f"-I{output_dir}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        str(proto_path),
    ]
    result = protoc.main(args)
    if result != 0:
        raise RuntimeError(f"Failed to compile proto: {proto_path}")


def import_module_from_file(module_name: str, file_path: Path):
    """Import a Python module directly from file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {module_name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def ensure_grpc_service_namespace(proto_dir: Path) -> None:
    """Ensure generated imports like `from grpc_service import xxx_pb2` can resolve."""
    package_name = "grpc_service"
    proto_dir_str = str(proto_dir)

    existing_pkg = sys.modules.get(package_name)
    if existing_pkg is None:
        pkg = types.ModuleType(package_name)
        pkg.__path__ = [proto_dir_str]
        sys.modules[package_name] = pkg
        return

    pkg_paths = getattr(existing_pkg, "__path__", None)
    if pkg_paths is None:
        existing_pkg.__path__ = [proto_dir_str]
    elif proto_dir_str not in pkg_paths:
        pkg_paths.append(proto_dir_str)


def load_proto_modules(service_name:str,proto_name: str) -> tuple[object, object]:
    """Load compiled gRPC modules from a proto file, compiling if needed.
        service_name:微服务的名称，对应目录名
        proto_name：proto文件名
    """
    proto_path = Path(__file__).resolve().parent.parent.parent / "services" / service_name / f"grpc_service/{proto_name}.proto"
    if not proto_path.exists():
        raise FileNotFoundError(f"Proto file not found: {proto_path}")

    pb2_path = proto_path.parent / f"{proto_name}_pb2.py"
    pb2_grpc_path = proto_path.parent / f"{proto_name}_pb2_grpc.py"
    if not pb2_path.exists() or not pb2_grpc_path.exists():
        compile_proto(proto_path)

    ensure_grpc_service_namespace(proto_path.parent)

    pb2 = import_module_from_file(f"grpc_service.{proto_name}_pb2", pb2_path)
    pb2_grpc = import_module_from_file(f"grpc_service.{proto_name}_pb2_grpc", pb2_grpc_path)
    return pb2, pb2_grpc