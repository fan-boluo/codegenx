"""Hook 装载兜底测试：监听器宿主模块加载后，有当前消费者的事件均有监听器注册。

监听器依赖"模块被 import 才会 @on 上报"，本测试防止业务重构误删
装配或 import 链导致监听器静默丢失（docs/Hook设计.md §4.2）。
通道定位（docs/链路追踪设计.md）：hook 是纯决策/业务信号通道——
BEFORE/AFTER_LLM_INVOKE 当前无消费者（输入输出护轨未来挂载点），允许零监听器。
运行：backend/.venv/Scripts/python.exe backend/tests/test_hook_coverage.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import codegenx.ai_service.api.router  # noqa: F401  与 main.py 相同的应用 import 入口
# 监听器宿主模块（与 system_app 冻结前的显式加载集合一致，防兜底丢失）
import codegenx.ai_service.agent.tool_executor  # noqa: F401  safe_path_guard
import codegenx.ai_service.tools.base  # noqa: F401  file_tool_param_guard
import codegenx.ai_service.hook.analysis_trace  # noqa: F401  sql_trace_start/end
import codegenx.ai_service.hook.tool_semantics  # noqa: F401  tool_semantics_recorder
import codegenx.ai_service.guardrail.prompt_safety_input_guardrail  # noqa: F401  inputput_safety_check
import codegenx.ai_service.chat_message.persist  # noqa: F401  persist_tool_log
import codegenx.ai_service.memory.trigger  # noqa: F401  memory_turn_signal
import codegenx.ai_service.memory.memory_manager  # noqa: F401  memory_session_end
from codegenx.ai_service.hook import EVENT_DEFINITIONS, HookEvent, hook_manager

# 零监听器合法事件：
# - BEFORE/AFTER_LLM_INVOKE：输入/输出护轨未来挂载点
# - SESSION_START/TURN_START/INTERNAL_ON_ERROR：生命周期边界（观测已直调 trace），
#   事件保留为业务信号挂载点
PENDING_GUARDRAIL_EVENTS = {
    HookEvent.BEFORE_LLM_INVOKE, HookEvent.AFTER_LLM_INVOKE,
    HookEvent.SESSION_START, HookEvent.TURN_START, HookEvent.INTERNAL_ON_ERROR,
}


class TestHookCoverage(unittest.TestCase):
    def test_consumer_events_have_listeners_on_import_chain(self):
        summary = hook_manager.summary()
        missing = [e for e in EVENT_DEFINITIONS
                   if e not in PENDING_GUARDRAIL_EVENTS and summary.get(e, 0) == 0]
        self.assertEqual(
            missing, [],
            f"以下有当前消费者的事件在 import 链上无任何监听器（检查装配或 import 链）: {missing}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
