"""User proxy utilities for HTTP-to-gRPC translation."""

from __future__ import annotations

from grpc_client.user_service_client import UserServiceGrpcClient


class UserProxy:
    """Proxy class that maps gateway requests to gRPC calls."""

    def __init__(self) -> None:
        self.client = UserServiceGrpcClient()

    async def register(self, user_account: str, user_password: str, check_password: str, user_name: str | None = None) -> int:
        return await self.client.register(user_account, user_password, check_password, user_name)

    async def login(self, user_account: str, user_password: str) -> dict[str, object]:
        return await self.client.login(user_account, user_password)

    async def get_user(self, user_id: int) -> dict[str, object]:
        return await self.client.get_user(user_id)

    async def add_user(self, user_account: str, user_password: str, user_name: str | None, user_avatar: str | None, user_profile: str | None, user_role: str | None) -> int:
        return await self.client.add_user(user_account, user_password, user_name, user_avatar, user_profile, user_role)

    async def update_user(self, user_id: int, user_name: str | None, user_avatar: str | None, user_profile: str | None, user_role: str | None) -> bool:
        return await self.client.update_user(user_id, user_name, user_avatar, user_profile, user_role)

    async def delete_user(self, user_id: int) -> bool:
        return await self.client.delete_user(user_id)

    async def list_users_page(self, page_num: int, page_size: int, user_id: int | None, user_account: str | None, user_name: str | None, user_profile: str | None, user_role: str | None, sort_field: str | None, sort_order: str | None) -> dict[str, object]:
        return await self.client.list_users_page(page_num, page_size, user_id, user_account, user_name, user_profile, user_role, sort_field, sort_order)
