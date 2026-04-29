"""gRPC servicer implementation for user-service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import importlib.util
import sys

from sqlalchemy.ext.asyncio import AsyncSession
from infra.mysql.session import session_maker
from shared.models.user import User
from shared.utils.proto_loader import load_proto_modules
from shared.config.config import get_settings

settings = get_settings()

user_pb2, user_pb2_grpc = load_proto_modules(settings.user_service_name,settings.user_service_proto_name)


def _import_user_service_class() -> type:
    repo_root = Path(__file__).resolve().parents[2]
    service_module_path = repo_root / "user-service" / "services" / "user_service.py"
    spec = importlib.util.spec_from_file_location("user_service_impl_module", service_module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load user service module from {service_module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["user_service_impl_module"] = module
    spec.loader.exec_module(module)
    return getattr(module, "UserService")

def _user_to_message(user) -> user_pb2.User:
    return user_pb2.User(
        id=user.id,
        user_account=user.user_account,
        user_name=user.user_name if hasattr(user, "user_name") else "",
        user_avatar=user.user_avatar if hasattr(user, "user_avatar") else "",
        user_profile=user.user_profile if hasattr(user, "user_profile") else "",
        user_role=user.user_role if hasattr(user, "user_role") else ""
    )

def _page_data_to_message(page_data) -> user_pb2.PageData:
    records = [_user_to_message(u) for u in page_data.records]
    return user_pb2.PageData(
        records=records,
        page_number=page_data.page_number,
        page_size=page_data.page_size,
        total_page=page_data.total_page,
        total_row=page_data.total_row,
        optimize_count_query=page_data.optimize_count_query,
    )


class UserServiceServicer(user_pb2_grpc.UserServiceServicer):
    """gRPC UserService servicer."""

    async def Register(self, request: user_pb2.RegisterRequest, context) -> user_pb2.RegisterResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            user_id = await service.register(
                request.user_account,
                request.user_password,
                request.check_password,
                request.user_name if request.HasField('user_name') else None,
            )
            return user_pb2.RegisterResponse(user_id=user_id)

    async def Login(self, request: user_pb2.LoginRequest, context) -> user_pb2.LoginResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            user = await service.login(request.user_account, request.user_password)
            return user_pb2.LoginResponse(user=_user_to_message(user))

    async def GetUser(self, request: user_pb2.GetUserRequest, context) -> user_pb2.UserResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            user = await service.get_by_id(request.user_id)
            if user is None:
                return user_pb2.UserResponse()
            return user_pb2.UserResponse(user=_user_to_message(user))

    async def AddUser(self, request: user_pb2.AddUserRequest, context) -> user_pb2.AddUserResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            user_id = await service.add_user(
                request.user_account,
                request.user_password,
                request.user_name if request.HasField("user_name") else None,
                request.user_avatar if request.HasField("user_avatar") else None,
                request.user_profile if request.HasField("user_profile") else None,
                request.user_role if request.HasField("user_role") else None,
            )
            return user_pb2.AddUserResponse(user_id=user_id)

    async def UpdateUser(self, request: user_pb2.UpdateUserRequest, context) -> user_pb2.UpdateUserResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            success = await service.update_user(
                request.user_id,
                request.user_name if request.HasField("user_name") else None,
                request.user_avatar if request.HasField("user_avatar") else None,
                request.user_profile if request.HasField("user_profile") else None,
                request.user_role if request.HasField("user_role") else None,
            )
            return user_pb2.UpdateUserResponse(success=success)

    async def DeleteUser(self, request: user_pb2.DeleteUserRequest, context) -> user_pb2.DeleteUserResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            success = await service.delete_user(request.user_id)
            return user_pb2.DeleteUserResponse(success=success)

    async def ListUsersPage(self, request: user_pb2.ListUsersPageRequest, context) -> user_pb2.ListUsersPageResponse:
        UserServiceClass = _import_user_service_class()
        async with session_maker() as session:
            service = UserServiceClass(session)
            page_data = await service.list_user_vo_page(
                request.page_num,
                request.page_size,
                request.user_id if request.HasField("user_id") else None,
                request.user_account if request.HasField("user_account") else None,
                request.user_name if request.HasField("user_name") else None,
                request.user_profile if request.HasField("user_profile") else None,
                request.user_role if request.HasField("user_role") else None,
                request.sort_field if request.HasField("sort_field") else None,
                request.sort_order if request.HasField("sort_order") else None,
            )
            return user_pb2.ListUsersPageResponse(page_data=_page_data_to_message(page_data))