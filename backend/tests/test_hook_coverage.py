"""Hook 装载兜底测试：应用 import 链加载后，全部事件均有监听器注册。

监听器依赖"模块被 import 才会 @on 上报"，本测试防止业务重构误删
包门面装配或 import 链导致监听器静默丢失（docs/Hook设计.md §4.2）。
运行：backend/.venv/Scripts/python.exe backend/tests/test_hook_coverage.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import codegenx.ai_service.router  # noqa: F401  与 main.py 相同的应用 import 入口
from codegenx.ai_service.hook import EVENT_DEFINITIONS, hook_manager


class TestHookCoverage(unittest.TestCase):
    def test_all_events_have_listeners_on_import_chain(self):
        summary = hook_manager.summary()
        missing = [e for e in EVENT_DEFINITIONS if summary.get(e, 0) == 0]
        self.assertEqual(
            missing, [],
            f"以下事件在应用 import 链上无任何监听器（检查包 __init__ 装配或 import 链）: {missing}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
