"""用户-项目-数据库权限与存储隔离 回归测试（离线可跑，不依赖 MySQL/Redis）。

覆盖四条核心链路：
1. 权限阶梯：owner/member/admin 放行，非参与者拒绝（NO_AUTH_ERROR=40101，即业务层 403）；
2. 成员文件隔离：A/B 成员各自 .data/{userId}/{appId}/code 目录互不可见，路径穿越被拒；
3. dbName 后端覆盖：gateway chat 以项目记录覆盖前端传值（防伪造 dbName 查任意库）；
4. 成员增删：按账号邀请、重复/属主拒绝、移除为逻辑删除、重新加入恢复原记录。

运行：backend 目录下 `python -m tests.test_member_storage_isolation`
（或用主工作区 backend/.venv 的 python 运行）。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

import shared.constants as constants_mod
from codegenx.app_service.orm.app import App
from codegenx.app_service.orm.app_member import AppMember
from codegenx.app_service.schema.app import AppMemberAddRequest, AppMemberRemoveRequest, AppQueryRequest
from codegenx.app_service.services.access import (
    get_access_role,
    get_active_member,
    require_manager,
    require_participant_by_id,
)
from codegenx.app_service.services.app_service import AppService
from codegenx.gateway.middleware.jwt_auth import JWTUser
from codegenx.user_service.models.user import User
from shared.exceptions.business_exception import BusinessException
from shared.exceptions.error_code import ErrorCode

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def expect_error(name: str, fn, err) -> None:
    # err 可传 ErrorCode 枚举成员（value 为 (code, msg) 元组）或 int
    code = err.value[0] if hasattr(err, "value") else err
    try:
        fn()
    except BusinessException as exc:
        got = exc.code if hasattr(exc, "code") else None
        check(name, got == code, f"expect code={code} got={got} msg={getattr(exc, 'message', '')}")
        return
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"unexpected {type(exc).__name__}: {exc}")
        return
    check(name, False, f"expected BusinessException({code}) but no error raised")


# ────────────────────────── FakeSession：内存版 AsyncSession ──────────────────────────


class _Scalars:
    def __init__(self, items: list) -> None:
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def all(self) -> list:
        return list(self._items)


class FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars([r[0] for r in self._rows])

    def all(self) -> list:
        return list(self._rows)


class FakeSession:
    """仅实现被测代码用到的 AsyncSession 子集；where 条件用小型求值器解释。"""

    def __init__(self) -> None:
        self.table_stores: dict[str, list] = {"app": [], "user": [], "app_member": []}
        self._pending: list[AppMember] = []
        self._next_id = 1

    # -- 数据装载 --

    def add_user(self, uid: int, account: str, name: str, role: str = "user") -> User:
        u = User(id=uid, user_account=account, user_name=name, user_role=role)
        self.table_stores["user"].append(u)
        return u

    def add_app(self, app_id: int, owner: int, name: str, db_name: str | None) -> App:
        a = App(id=app_id, owner=owner, app_name=name, db_name=db_name)
        self.table_stores["app"].append(a)
        return a

    def add_member_row(self, app_id: int, user_id: int, is_delete: int = 0) -> AppMember:
        m = AppMember(id=self._next_id, app_id=app_id, user_id=user_id, is_delete=is_delete)
        self._next_id += 1
        m.create_time = datetime.now()
        m.update_time = datetime.now()
        self.table_stores["app_member"].append(m)
        return m

    # -- AsyncSession 子集 --

    async def get(self, model, pk):
        for obj in self.table_stores[model.__tablename__]:
            if getattr(obj, "id", None) == pk:
                return obj
        return None

    async def execute(self, stmt) -> FakeResult:
        return FakeResult(self.eval_select(stmt))

    async def scalar(self, stmt):
        from_obj = getattr(stmt, "_from_obj", ()) or ()
        if from_obj:
            inner = from_obj[0]
            inner = getattr(inner, "element", inner)
            return len(self.eval_select(inner))
        rows = self.eval_select(stmt)
        return rows[0][0] if rows else None

    def add(self, obj) -> None:
        self._pending.append(obj)

    async def commit(self) -> None:
        # 模拟 flush 时的列默认值（default= 在 flush 才生效）
        for obj in self._pending:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1
            if getattr(obj, "create_time", None) is None:
                obj.create_time = datetime.now()
            if getattr(obj, "update_time", None) is None:
                obj.update_time = datetime.now()
            if getattr(obj, "is_delete", None) is None:
                obj.is_delete = 0
            self.table_stores["app_member"].append(obj)
        self._pending.clear()

    # -- 小型 where 求值器 --

    _MODEL_BY_TABLE = {"app": App, "app_member": AppMember, "user": User}

    def _col_val(self, col, ctx: dict):
        # Column.key 是库表列名（如 appId），需经 mapper 还原为 ORM 属性名（app_id）
        model = self._MODEL_BY_TABLE[col.table.name]
        attr = model.__mapper__.get_property_by_column(col).key
        return getattr(ctx[col.table.name], attr)

    def _eval_cond(self, cond, ctx: dict) -> bool:
        if cond is None:
            return True
        if isinstance(cond, BooleanClauseList):
            name = getattr(cond.operator, "__name__", "")
            vals = [self._eval_cond(c, ctx) for c in cond.clauses]
            return any(vals) if name == "or_" else all(vals)
        if isinstance(cond, BinaryExpression):
            name = getattr(cond.operator, "__name__", "")
            left, right = cond.left, cond.right
            if name in ("in_", "in_op"):
                col_name = right.column_descriptions[0]["name"]
                sub_ids = [getattr(r[0], col_name) for r in self.eval_select(right)]
                return self._col_val(left, ctx) in sub_ids
            lval = self._col_val(left, ctx)
            rval = getattr(right, "value", None)
            return {"eq": lambda: lval == rval, "ne": lambda: lval != rval}.get(
                name, lambda: False
            )()
        return True

    def eval_select(self, stmt) -> list:
        entities = [d["entity"] for d in stmt.column_descriptions if d.get("entity") is not None]
        conds = []
        for fo in getattr(stmt, "_from_obj", ()) or ():
            on = getattr(fo, "onclause", None)
            if on is not None:
                conds.append(on)
        wc = getattr(stmt, "whereclause", None)
        if wc is not None:
            conds.append(wc)

        if len(entities) <= 1:
            table = entities[0].__tablename__ if entities else "app_member"
            candidates = [({table: o}, (o,)) for o in self.table_stores[table]]
        else:
            candidates = []
            combos = [({}, ())]
            for ent in entities:
                nxt = []
                for ctx, tup in combos:
                    for o in self.table_stores[ent.__tablename__]:
                        c2 = dict(ctx)
                        c2[ent.__tablename__] = o
                        nxt.append((c2, tup + (o,)))
                combos = nxt
            candidates = combos

        rows = [
            (ctx, tup) for ctx, tup in candidates if all(self._eval_cond(c, ctx) for c in conds)
        ]

        order_by = getattr(stmt, "_order_by_clauses", ()) or ()
        for oc in reversed(list(order_by)):
            col = getattr(oc, "element", oc)
            desc_order = "desc" in str(getattr(oc, "modifier", ""))
            rows.sort(key=lambda r: self._col_val(col, r[0]) or datetime.min, reverse=desc_order)
        return [tup for _, tup in rows]


# ────────────────────────── 测试数据 ──────────────────────────


def build_session() -> FakeSession:
    db = FakeSession()
    db.add_user(1, "alice", "Alice", "user")  # app 100 属主
    db.add_user(2, "bob", "Bob", "user")  # app 100 / 101 成员
    db.add_user(3, "carol", "Carol", "user")  # app 100 成员（隔离对照）
    db.add_user(4, "dave", "Dave", "user")  # app 101 属主
    db.add_user(9, "root", "Root", "admin")  # 平台管理员
    db.add_app(100, 1, "项目甲", "proj_db")
    db.add_app(101, 4, "项目乙", None)
    db.add_member_row(100, 2)
    db.add_member_row(100, 3)
    db.add_member_row(101, 2)
    return db


def jwt(uid: int, account: str, role: str = "user") -> JWTUser:
    return JWTUser(user_id=uid, user_account=account, user_role=role)


OWNER = lambda: jwt(1, "alice")  # noqa: E731
MEMBER = lambda: jwt(2, "bob")  # noqa: E731
MEMBER2 = lambda: jwt(3, "carol")  # noqa: E731
OUTSIDER = lambda: jwt(5, "eve")  # noqa: E731
ADMIN = lambda: jwt(9, "root", "admin")  # noqa: E731


# ────────────────────────── 1. 权限阶梯 ──────────────────────────


def test_permission_ladder() -> None:
    print("[1] 权限阶梯（非参与者拒绝）")
    db = build_session()
    app = db.table_stores["app"][0]  # app 100

    import asyncio

    check("owner 角色识别", asyncio.run(get_access_role(db, app, OWNER())) == "owner")
    check("member 角色识别", asyncio.run(get_access_role(db, app, MEMBER())) == "member")
    check("admin 角色识别", asyncio.run(get_access_role(db, app, ADMIN())) == "admin")
    check("非参与者返回 None", asyncio.run(get_access_role(db, app, OUTSIDER())) is None)
    check("软删成员不算 member", asyncio.run(get_access_role(db, _soft_delete_member(db, 100, 3), MEMBER2())) is None)

    expect_error(
        "非成员访问项目被拒(NO_AUTH 40101)",
        lambda: asyncio.run(require_participant_by_id(db, 100, OUTSIDER())),
        ErrorCode.NO_AUTH_ERROR,
    )
    expect_error(
        "成员不能执行管理操作",
        lambda: asyncio.run(require_manager(db, app, MEMBER())),
        ErrorCode.NO_AUTH_ERROR,
    )
    expect_error(
        "appId<=0 参数错误",
        lambda: asyncio.run(require_participant_by_id(db, 0, MEMBER())),
        ErrorCode.PARAMS_ERROR,
    )
    expect_error(
        "项目不存在",
        lambda: asyncio.run(require_participant_by_id(db, 999, MEMBER())),
        ErrorCode.NOT_FOUND_ERROR,
    )
    # 成员/属主/管理员可通过
    got = asyncio.run(require_participant_by_id(db, 100, MEMBER()))
    check("成员通过 require_participant_by_id", got is app)
    got = asyncio.run(require_participant_by_id(db, 100, ADMIN()))
    check("管理员通过 require_participant_by_id", got is app)


def _soft_delete_member(db: FakeSession, app_id: int, user_id: int) -> App:
    for m in db.table_stores["app_member"]:
        if m.app_id == app_id and m.user_id == user_id:
            m.is_delete = 1
    return db.table_stores["app"][0]


# ────────────────────────── 2. 成员文件隔离 ──────────────────────────


def test_file_isolation() -> None:
    print("[2] 成员文件隔离（.data/{userId}/{appId}/code）")
    import asyncio

    db = build_session()
    with tempfile.TemporaryDirectory() as tmp:
        constants_mod.DATA_ROOT_DIR = Path(tmp)
        svc = AppService(db)  # type: ignore[arg-type]

        asyncio.run(svc.create_file(100, "a.txt", MEMBER()))
        path_a = Path(tmp) / "2" / "100" / "code" / "a.txt"
        check("成员A文件落在 A 自己目录", path_a.exists())

        tree_b = asyncio.run(svc.get_code_tree(100, MEMBER2()))
        check("成员B看不到A的文件", tree_b == [])

        expect_error(
            "成员B读取A的文件被拒",
            lambda: asyncio.run(svc.get_code_file(100, "a.txt", MEMBER2())),
            ErrorCode.NOT_FOUND_ERROR,
        )

        asyncio.run(svc.create_file(100, "b.txt", MEMBER2()))
        tree_a = asyncio.run(svc.get_code_tree(100, MEMBER()))
        names_a = sorted(n["name"] for n in tree_a)
        check("成员A目录只含自己的文件", names_a == ["a.txt"], f"got {names_a}")

        expect_error(
            "路径穿越被拒",
            lambda: asyncio.run(svc.save_code_file(100, "../evil.txt", "x", MEMBER())),
            ErrorCode.PARAMS_ERROR,
        )

        # 非成员连自己的"目录视角"都进不来
        expect_error(
            "非成员读取文件树被拒",
            lambda: asyncio.run(svc.get_code_tree(100, OUTSIDER())),
            ErrorCode.NO_AUTH_ERROR,
        )
    import shutil

    shutil.rmtree(Path("D:/Project/agent/CodeGenX/.claude/worktrees/project-member-storage-plan/.data"), ignore_errors=True)


# ────────────────────────── 3. dbName 后端覆盖 ──────────────────────────


def test_db_name_override() -> None:
    print("[3] gateway chat dbName 后端覆盖")
    import asyncio

    import codegenx.gateway.api.chat as chat_mod

    captured: dict = {}

    async def fake_stream(request):
        captured["request"] = request
        return "sse"

    class FakeRateLimit:
        def __init__(self, redis) -> None: ...

        async def check_user_rate_limit(self, *a, **k) -> None: ...

    chat_mod.generate_code_stream = fake_stream
    chat_mod.RateLimitService = FakeRateLimit

    db = build_session()

    payload = {
        "appId": "100",
        "message": "hi",
        "dbName": "victim_db",
        "sessionId": "s1",
        "traceId": "t1",
        "requestId": "r1",
    }
    asyncio.run(chat_mod.chat_to_gen_code_post(payload, login_user=MEMBER(), redis=None, db=db))  # type: ignore[arg-type]
    req = captured["request"]
    check("dbName 以项目记录覆盖前端伪造值", req.db_name == "proj_db", f"got {req.db_name!r}")
    check("userId 注入当前登录人", req.user_id == "2", f"got {req.user_id!r}")
    check("appId 解析为 int", req.app_id == 100)

    payload2 = {"appId": 101, "message": "hi", "dbName": "victim_db", "sessionId": "s", "traceId": "t", "requestId": "r"}
    asyncio.run(chat_mod.chat_to_gen_code_post(payload2, login_user=MEMBER(), redis=None, db=db))  # type: ignore[arg-type]
    check("未绑定库的项目 dbName 置空", captured["request"].db_name is None, f"got {captured['request'].db_name!r}")

    expect_error(
        "非成员聊天被拒",
        lambda: asyncio.run(
            chat_mod.chat_to_gen_code_post(
                {"appId": 100, "message": "hi", "sessionId": "s", "traceId": "t", "requestId": "r"},
                login_user=OUTSIDER(),
                redis=None,
                db=db,  # type: ignore[arg-type]
            )
        ),
        ErrorCode.NO_AUTH_ERROR,
    )


# ────────────────────────── 4. 成员增删（逻辑删除 + 恢复） ──────────────────────────


def test_member_add_remove() -> None:
    print("[4] 成员增删（逻辑删除 + 重新加入恢复原记录）")
    import asyncio

    db = build_session()
    svc = AppService(db)  # type: ignore[arg-type]

    # eve(user 5) 不存在 → NOT_FOUND
    expect_error(
        "邀请不存在的账号报 NOT_FOUND",
        lambda: asyncio.run(svc.add_member(AppMemberAddRequest(appId=100, userAccount="eve"), OWNER())),
        ErrorCode.NOT_FOUND_ERROR,
    )
    # 成员没有管理权
    expect_error(
        "普通成员不能邀请他人",
        lambda: asyncio.run(svc.add_member(AppMemberAddRequest(appId=100, userAccount="dave"), MEMBER())),
        ErrorCode.NO_AUTH_ERROR,
    )
    # 邀请属主本人
    expect_error(
        "不能邀请属主本人",
        lambda: asyncio.run(svc.add_member(AppMemberAddRequest(appId=100, userAccount="alice"), OWNER())),
        ErrorCode.PARAMS_ERROR,
    )
    # 重复邀请
    expect_error(
        "重复邀请报已是成员",
        lambda: asyncio.run(svc.add_member(AppMemberAddRequest(appId=100, userAccount="bob"), OWNER())),
        ErrorCode.PARAMS_ERROR,
    )

    # 正常按账号邀请 eve 先注册 → dave 邀请进 app 101
    db.add_user(5, "eve", "Eve", "user")
    ok = asyncio.run(svc.add_member(AppMemberAddRequest(appId=101, userAccount="eve"), jwt(4, "dave")))
    check("属主按账号邀请成功", ok is True)
    rows = [m for m in db.table_stores["app_member"] if m.app_id == 101 and m.user_id == 5]
    check("新增成员记录 is_delete=0", len(rows) == 1 and rows[0].is_delete == 0)

    # 按 userId 邀请
    ok = asyncio.run(svc.add_member(AppMemberAddRequest(appId=101, userId=3), jwt(4, "dave")))
    check("按 userId 邀请成功", ok is True and len([m for m in db.table_stores["app_member"] if m.app_id == 101 and m.user_id == 3]) == 1)

    # 移除 → 逻辑删除
    ok = asyncio.run(svc.remove_member(AppMemberRemoveRequest(appId=101, userId=5), jwt(4, "dave")))
    check("移除成员成功", ok is True)
    check("移除后记录仍在且 is_delete=1", rows[0].is_delete == 1)
    check("get_active_member 不再命中", asyncio.run(get_active_member(db, 101, 5)) is None)

    # 非成员本人无权移除他人
    expect_error(
        "被移除者（已非成员）不能再移除他人",
        lambda: asyncio.run(svc.remove_member(AppMemberRemoveRequest(appId=101, userId=3), jwt(5, "eve"))),
        ErrorCode.NO_AUTH_ERROR,
    )

    # 重新加入 → 恢复原记录（不新增行）
    before = len(db.table_stores["app_member"])
    asyncio.run(svc.add_member(AppMemberAddRequest(appId=101, userId=5), jwt(4, "dave")))
    after = [m for m in db.table_stores["app_member"] if m.app_id == 101 and m.user_id == 5]
    check("重新加入恢复原记录（无新增行）", len(after) == 1 and after[0] is rows[0] and after[0].is_delete == 0)
    check("成员总行数不变", len(db.table_stores["app_member"]) == before)

    # list_members：join User 输出 VO
    vos = asyncio.run(svc.list_members(101, jwt(4, "dave")))
    by_uid = {v.user_id: v for v in vos}
    check(
        "list_members 返回账号/昵称",
        by_uid.get(5) is not None and by_uid[5].user_account == "eve" and by_uid[5].user_name == "Eve",
    )


# ────────────────────────── 5. 我参与的项目 ──────────────────────────


def test_my_project_list() -> None:
    print("[5] 我参与的项目（owner 或 成员）")
    import asyncio

    db = build_session()
    svc = AppService(db)  # type: ignore[arg-type]

    page = asyncio.run(svc.list_my_app_vo_by_page(AppQueryRequest(pageNum=1, pageSize=10), MEMBER()))
    ids = sorted(r.id for r in page.records)
    check("成员视角包含 属主+成员 项目 {100,101}", ids == [100, 101], f"got {ids}")

    page = asyncio.run(svc.list_my_app_vo_by_page(AppQueryRequest(pageNum=1, pageSize=10), jwt(3, "carol")))
    ids = sorted(r.id for r in page.records)
    check("仅成员视角只含成员项目 {100}", ids == [100], f"got {ids}")

    page = asyncio.run(svc.list_my_app_vo_by_page(AppQueryRequest(pageNum=1, pageSize=10), OUTSIDER()))
    check("局外人列表为空", page.records == [] and page.total_row == 0)

    vo = page  # noqa: F841
    page100 = asyncio.run(svc.list_my_app_vo_by_page(AppQueryRequest(pageNum=1, pageSize=10), OWNER()))
    target = next(r for r in page100.records if r.id == 100)
    check(
        "VO 字段精简（appName/owner/dbName）",
        target.app_name == "项目甲" and target.owner == 1 and target.db_name == "proj_db",
    )


def main() -> int:
    test_permission_ladder()
    test_file_isolation()
    test_db_name_override()
    test_member_add_remove()
    test_my_project_list()
    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
