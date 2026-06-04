from bot.hook.runner import HookRunner
from bot.hook.handlers import (
    on_session_start,
    on_turn_start,
    pre_llm_call,
    post_llm_call,
    pre_tool_use,
    post_tool_use,
    on_turn_end,
    on_error,
    on_session_end,
)

def register_all_hooks(runner: HookRunner):
    runner.register('OnSessionStart', on_session_start)
    runner.register('OnTurnStart', on_turn_start)
    runner.register('PreLLMCall', pre_llm_call)
    runner.register('PostLLMCall', post_llm_call)
    runner.register('PreToolUse', pre_tool_use)
    runner.register('PostToolUse', post_tool_use)
    runner.register('OnTurnEnd', on_turn_end)
    runner.register('OnError', on_error)
    runner.register('OnSessionEnd', on_session_end)
