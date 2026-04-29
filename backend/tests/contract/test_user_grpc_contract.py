from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = BACKEND_ROOT / "api-gateway"
for candidate in (str(GATEWAY_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from shared.utils.proto_loader import load_proto_modules


class UserGrpcContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.user_pb2, cls.user_pb2_grpc = load_proto_modules("user-service", "user_service")

    def test_user_service_stub_exports_expected_rpcs(self) -> None:
        stub_methods = {method.name for method in self.user_pb2.DESCRIPTOR.services_by_name["UserService"].methods}
        self.assertEqual(
            stub_methods,
            {"Register", "Login", "GetUser", "AddUser", "UpdateUser", "DeleteUser", "ListUsersPage"},
        )
        self.assertTrue(hasattr(self.user_pb2_grpc, "UserServiceStub"))

    def test_register_request_fields_match_contract(self) -> None:
        request = self.user_pb2.RegisterRequest(user_account="demo", user_password="secret", check_password="secret")
        self.assertEqual(request.user_account, "demo")
        fields = {field.name: field.number for field in self.user_pb2.RegisterRequest.DESCRIPTOR.fields}
        self.assertEqual(fields, {"user_account": 1, "user_password": 2, "check_password": 3, "user_name": 4})

    def test_page_data_uses_snake_case_field_names(self) -> None:
        fields = {field.name for field in self.user_pb2.PageData.DESCRIPTOR.fields}
        self.assertIn("page_number", fields)
        self.assertIn("optimize_count_query", fields)
        self.assertNotIn("pageNumber", fields)


if __name__ == "__main__":
    unittest.main()