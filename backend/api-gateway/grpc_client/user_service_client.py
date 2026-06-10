"""gRPC client wrapper for user-service in the API gateway."""

from __future__ import annotations

from grpc_client.base import GrpcServiceClientBase
from shared.config.config import get_settings
from shared.utils.proto_loader import load_proto_modules

settings = get_settings()
user_pb2, user_pb2_grpc = load_proto_modules(settings.user_service_name,settings.user_service_proto_name)


class UserServiceGrpcClient(GrpcServiceClientBase):
    """Wrapper for user-service gRPC calls."""

    def __init__(self) -> None:
        super().__init__(
            service_name=settings.user_service_name,
            fallback_target=f"{settings.user_service_host}:{settings.user_service_port}",
        )

    async def register(self, user_account: str, user_password: str, check_password: str, user_name: str | None = None) -> int:
        request = user_pb2.RegisterRequest(
            user_account=user_account,
            user_password=user_password,
            check_password=check_password,
        )
        if user_name:
            request.user_name = user_name
        response = await self.invoke(
            operation="Register",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.Register(request, timeout=self.timeout_seconds),
        )
        return response.user_id

    async def login(self, user_account: str, user_password: str) -> dict[str, object]:
        response = await self.invoke(
            operation="Login",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.Login(user_pb2.LoginRequest(
                user_account=user_account,
                user_password=user_password,
            ), timeout=self.timeout_seconds),
        )
        user = response.user
        return {
            "id": user.id,
            "userAccount": user.user_account,
            "userName": user.user_name,
            "userAvatar": user.user_avatar,
            "userProfile": user.user_profile,
            "userRole": user.user_role,
        }

    async def get_user(self, user_id: int) -> dict[str, object]:
        response = await self.invoke(
            operation="GetUser",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.GetUser(user_pb2.GetUserRequest(user_id=user_id), timeout=self.timeout_seconds),
        )
        user = response.user
        return {
            "id": user.id,
            "userAccount": user.user_account,
            "userName": user.user_name,
            "userAvatar": user.user_avatar,
            "userProfile": user.user_profile,
            "userRole": user.user_role,
        }

    async def add_user(self, user_account: str, user_password: str, user_name: str | None, user_avatar: str | None, user_profile: str | None, user_role: str | None) -> int:
        request = user_pb2.AddUserRequest(
            user_account=user_account,
            user_password=user_password,
        )
        if user_name is not None:
            request.user_name = user_name
        if user_avatar is not None:
            request.user_avatar = user_avatar
        if user_profile is not None:
            request.user_profile = user_profile
        if user_role is not None:
            request.user_role = user_role
        response = await self.invoke(
            operation="AddUser",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.AddUser(request, timeout=self.timeout_seconds),
        )
        return response.user_id

    async def update_user(self, user_id: int, user_name: str | None, user_avatar: str | None, user_profile: str | None, user_role: str | None) -> bool:
        request = user_pb2.UpdateUserRequest(user_id=user_id)
        if user_name is not None:
            request.user_name = user_name
        if user_avatar is not None:
            request.user_avatar = user_avatar
        if user_profile is not None:
            request.user_profile = user_profile
        if user_role is not None:
            request.user_role = user_role
        response = await self.invoke(
            operation="UpdateUser",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.UpdateUser(request, timeout=self.timeout_seconds),
        )
        return response.success

    async def delete_user(self, user_id: int) -> bool:
        response = await self.invoke(
            operation="DeleteUser",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.DeleteUser(user_pb2.DeleteUserRequest(user_id=user_id), timeout=self.timeout_seconds),
        )
        return response.success

    async def list_users_page(self, page_num: int, page_size: int, user_id: int | None = None, user_account: str | None = None, user_name: str | None = None, user_profile: str | None = None, user_role: str | None = None, sort_field: str | None = None, sort_order: str | None = None) -> dict[str, object]:
        request = user_pb2.ListUsersPageRequest(
            page_num=page_num,
            page_size=page_size,
        )
        if user_id is not None:
            request.user_id = user_id
        if user_account is not None:
            request.user_account = user_account
        if user_name is not None:
            request.user_name = user_name
        if user_profile is not None:
            request.user_profile = user_profile
        if user_role is not None:
            request.user_role = user_role
        if sort_field is not None:
            request.sort_field = sort_field
        if sort_order is not None:
            request.sort_order = sort_order
        response = await self.invoke(
            operation="ListUsersPage",
            stub_factory=user_pb2_grpc.UserServiceStub,
            callback=lambda stub: stub.ListUsersPage(request, timeout=self.timeout_seconds),
        )
        page_data = response.page_data
        records = [
            {
                "id": u.id,
                "userAccount": u.user_account,
                "userName": u.user_name,
                "userAvatar": u.user_avatar,
                "userProfile": u.user_profile,
                "userRole": u.user_role,
            }
            for u in page_data.records
        ]
        return {
            "records": records,
            "pageNumber": page_data.page_number,
            "pageSize": page_data.page_size,
            "totalPage": page_data.total_page,
            "totalRow": page_data.total_row,
            "optimizeCountQuery": page_data.optimize_count_query,
        }