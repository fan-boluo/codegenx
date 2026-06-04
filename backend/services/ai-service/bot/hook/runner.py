import inspect
from typing import Any, Callable, Dict, List

from shared.config.log_config import log

class HookRunner:
    def __init__(self):
        # Maps event_name -> list of hook callbacks
        self._hooks: Dict[str, List[Callable]] = {}

    def register(self, event_name: str, callback: Callable) -> None:
        """
        注册一个 hook 回调函数到指定的事件上。
        """
        if event_name not in self._hooks:
            self._hooks[event_name] = []
        self._hooks[event_name].append(callback)
        log.debug(f"Registered hook '{callback.__name__}' for event '{event_name}'")

    def _merge_result(self, merged: dict[str, Any], callback_result: Any) -> dict[str, Any]:
        if callback_result is None:
            return merged
        if isinstance(callback_result, dict):
            merged.update(callback_result)
            return merged
        merged.setdefault("results", []).append(callback_result)
        return merged
    def _normalize_result(self, callback_result: Any) -> dict[str, Any]:
        """
        将hook的返回值统一转化为标准格式

        """
        if callback_result is None:
            return {}
        if isinstance(callback_result, dict):
            return callback_result
        return {"result":callback_result}


    async def dispatch(self, event_name: str, state: Any, **kwargs) -> dict[str, Any]:
        """
        分发事件，按顺序调用所有注册和该事件的回调函数。
        如果 hook 发生异常，将捕获异常并抛出，由调用的地方转换为 OnError 分支。
        """
        if event_name not in self._hooks:
            return {}
        result = {
            "hook_name":event_name,
            "action":"", # continue blocked inject
            "message":""
        }

        merged_result: dict[str, Any] = {"success": True,"errors":[]}
        
        for callback in self._hooks[event_name]:
            log.debug(f"Dispatching hook '{event_name}', running callback '{callback.__name__}'")
            try:
                if inspect.iscoroutinefunction(callback):
                    callback_result = await callback(state, **kwargs)
                else:
                    callback_result = callback(state, **kwargs)

                normalized_result = self._normalize_result(callback_result)
                merged_result.update(normalized_result)
                result['action'] = "continue"
            except Exception as e:
                log.error(f"Error executing hook '{event_name}' in '{callback.__name__}': {e}")
                # 不抛异常，hook不能影响主循环
                result['action'] = "blocked"
                result['message'] = f'{callback.__name__}: {str(e)}'

        return result