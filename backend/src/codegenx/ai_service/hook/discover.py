"""Hook 启动装载：import 各模块原有文件内的监听器 → 冻结注册表。

内置监听器归属各功能模块（docs/Hook设计.md §4.2），此处仅维护 import 清单；
业务扩展模块通过 .env HOOK_EXTRA_MODULES 追加。
"""

from __future__ import annotations

import importlib

from shared import log
from shared.config.config import get_settings

from .core import hook_manager

# 内置监听器所在模块（均为原有业务文件，@on 随模块 import 完成收集）
BUILTIN_HOOK_MODULES = [
    "codegenx.ai_service.agent.runtime",                        # 会话/turn 生命周期编排
    "codegenx.ai_service.monitor.monitor_pipeline",             # 监控上报
    "codegenx.ai_service.memory.trigger",                       # 记忆漏斗信号
    "codegenx.ai_service.context.assembler",                    # 上下文注入
    "codegenx.ai_service.tools.base",                           # 工具参数守卫
    "codegenx.ai_service.session.manager",                      # 工具日志落盘
    "codegenx.ai_service.guardrail.prompt_safety_input_guardrail",  # 输出安全校验
]


def _extra_modules() -> list[str]:
    """解析 .env HOOK_EXTRA_MODULES（逗号分隔的模块 import 路径）。"""
    raw = str(getattr(get_settings(), "hook_extra_modules", "") or "").strip()
    return [m.strip() for m in raw.split(",") if m.strip()]


def _fail_fast() -> bool:
    """启动校验失败是否阻断服务启动（AgentConfig.hook_fail_fast，默认 true）。"""
    try:
        from codegenx.ai_service.utils.config import config as agent_config
        agent = agent_config.get_default_agent()
        return bool(getattr(agent, "hook_fail_fast", True))
    except Exception:
        return True


def load_hooks() -> None:
    """服务启动时调用：装载内置 + 扩展监听器模块并冻结注册表。

    fail_fast=true 时任一模块装载失败或注册表校验失败均抛异常阻断启动；
    false 时降级为告警继续（冻结仍会执行，非法项跳过）。
    """
    fail_fast = _fail_fast()
    for mod in BUILTIN_HOOK_MODULES + _extra_modules():
        try:
            importlib.import_module(mod)
        except Exception as exc:
            log.error("hook 模块装载失败: {} -> {}", mod, exc)
            if fail_fast:
                raise

    try:
        hook_manager.load_and_freeze()
    except ValueError as exc:
        log.error("hook 注册表校验失败: {}", exc)
        if fail_fast:
            raise
        # 降级：尽力冻结（非法事件组跳过）
        log.warning("hook_fail_fast=false，以部分装载继续启动")
        hook_manager._frozen = True
