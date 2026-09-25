"""user/app 前缀 ID（user_xxxx / app_xxxx）与字符串化改造的固化测试。

运行：cd backend && python -m pytest tests/test_prefixed_id.py -v
（或直接 python tests/test_prefixed_id.py）
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import String

from shared.utils.id_utils import (
    APP_ID_PREFIX,
    USER_ID_PREFIX,
    generate_app_id,
    generate_prefixed_id,
    generate_user_id,
)

_ID_PATTERN = re.compile(r"^[a-z]+_[a-z0-9]{4}$")


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        raise SystemExit(1)


def test_id_format_and_uniqueness():
    uid = generate_user_id()
    aid = generate_app_id()
    check("user id 前缀", uid.startswith(USER_ID_PREFIX), uid)
    check("app id 前缀", aid.startswith(APP_ID_PREFIX), aid)
    check("user id 格式 user_xxxx", _ID_PATTERN.match(uid) is not None, uid)
    check("app id 格式 app_xxxx", _ID_PATTERN.match(aid) is not None, aid)
    check("随机段长度为4", len(uid) == len(USER_ID_PREFIX) + 4, uid)
    # 36^4≈168万组合，2000 次采样按生日问题本就允许少量碰撞，只验证随机性充分
    ids = {generate_prefixed_id("t_") for _ in range(2000)}
    check("2000 次采样随机性充分（去重率>99%）", len(ids) > 1980, f"distinct={len(ids)}")


def test_orm_columns_are_string():
    from codegenx.app_service.orm.app import App
    from codegenx.app_service.orm.app_member import AppMember
    from codegenx.user_service.models.user import User

    check("User.id 为 String 主键", isinstance(User.__table__.c.id.type, String))
    check("App.id 为 String 主键", isinstance(App.__table__.c.id.type, String))
    check("App.owner 为 String", isinstance(App.__table__.c.owner.type, String))
    check("AppMember.appId 为 String", isinstance(AppMember.__table__.c.appId.type, String))
    check("AppMember.userId 为 String", isinstance(AppMember.__table__.c.userId.type, String))
    check("User 模型已无 userAvatar 列", "userAvatar" not in User.__table__.c)


def test_schemas_accept_string_ids():
    from codegenx.app_service.schema.app import AppMemberVO, AppVO
    from codegenx.user_service.schema.user import UserRegisterRequest

    vo = AppVO.model_validate({"id": "app_ab12", "appName": "项目甲", "owner": "user_xy9z"})
    check("AppVO 接受字符串 id/owner", vo.id == "app_ab12" and vo.owner == "user_xy9z")
    mvo = AppMemberVO.model_validate({"appId": "app_ab12", "userId": "user_xy9z"})
    check("AppMemberVO 接受字符串 id", mvo.app_id == "app_ab12" and mvo.user_id == "user_xy9z")
    import json

    payload = json.loads(AppVO(id="app_ab12").model_dump_json(by_alias=True))
    check("AppVO.id 序列化为字符串", payload["id"] == "app_ab12", str(payload))

    # 回归：注册 userName 必填
    try:
        UserRegisterRequest.model_validate({"userAccount": "a", "userPassword": "x", "checkPassword": "x"})
        check("注册缺 userName 被拒绝", False)
    except Exception:
        check("注册缺 userName 被拒绝", True)


def test_generate_unique_user_id_with_fake_db():
    from codegenx.user_service.user_service import UserService

    class FakeDB:
        async def scalar(self, *_a, **_k):
            return None  # 模拟无冲突

    svc = UserService.__new__(UserService)
    svc.db = FakeDB()
    new_id = asyncio.run(svc._generate_unique_user_id())
    check("服务层生成 user_xxxx", new_id.startswith("user_") and len(new_id) == 9, new_id)


def test_generate_unique_user_id_retry_on_conflict():
    from codegenx.user_service.user_service import UserService

    class ConflictedDB:
        def __init__(self):
            self.calls = 0

        async def scalar(self, *_a, **_k):
            self.calls += 1
            return "user_taken" if self.calls < 3 else None  # 前两次冲突

    db = ConflictedDB()
    svc = UserService.__new__(UserService)
    svc.db = db
    new_id = asyncio.run(svc._generate_unique_user_id())
    check("主键冲突时重试成功", db.calls == 3 and new_id != "user_taken", f"calls={db.calls}")


if __name__ == "__main__":
    test_id_format_and_uniqueness()
    test_orm_columns_are_string()
    test_schemas_accept_string_ids()
    test_generate_unique_user_id_with_fake_db()
    test_generate_unique_user_id_retry_on_conflict()
    print("\n全部通过")
