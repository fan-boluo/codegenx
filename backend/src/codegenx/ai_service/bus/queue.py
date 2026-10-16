"""Async message queue for decoupled channel-agent communication."""

import asyncio
from collections import defaultdict
from typing import Any

from codegenx.ai_service.agent.agent_schema import AgentEventType, AgentEvent
from codegenx.ai_service.component import BaseComponent, ComponentType
from codegenx.ai_service.agent.agent_schema import  AgentState
import json


class MessageBus(BaseComponent):
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """
    name = ComponentType.MESSAGE_BUS

    def __init__(self, system_app=None) -> None:
        BaseComponent.__init__(self, system_app)
        self.inbound: asyncio.Queue[Any] = asyncio.Queue()
        self._request_subscribers: dict[str, set[asyncio.Queue[Any]]] = defaultdict(set)

    def init_app(self, system_app) -> None:
        self.system_app = system_app

    async def publish_inbound(self, msg: Any) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> Any:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: Any) -> None:
        """Publish a response from the agent to request subscribers."""
        request_id = str(getattr(msg, "request_id", "") or "").strip()
        if not request_id:
            return

        subscribers = list(self._request_subscribers.get(request_id, ()))
        for queue in subscribers:
            await queue.put(msg)

    # async def consume_outbound(self) -> Any:
    #     """Consume the next outbound message (blocks until available)."""
    #     return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    # @property
    # def outbound_size(self) -> int:
    #     """Number of pending outbound messages."""
    #     return self.outbound.qsize()

    def subscribe_request(self, request_id: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._request_subscribers[str(request_id)].add(queue)
        return queue

    def unsubscribe_request(self, request_id: str, queue: asyncio.Queue[Any]) -> None:
        subscribers = self._request_subscribers.get(str(request_id))
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._request_subscribers.pop(str(request_id), None)

    def subscribe_turn(self, turn_id: str) -> asyncio.Queue[Any]:
        return self.subscribe_request(turn_id)

    def unsubscribe_turn(self, turn_id: str, queue: asyncio.Queue[Any]) -> None:
        self.unsubscribe_request(turn_id, queue)

    def _sanitize_event_data(self, event_type: AgentEventType,data: Any) -> None:
        """过滤敏感数据，工具事件只描述正在做什么，不传输原始内容。"""
        if event_type == AgentEventType.TOOL_EXECUTION_START:
            tc = data if isinstance(data, dict) else {}
            data =  {
                "tool_name": tc.get("name", ""),
                "tool_id": tc.get("id", ""),
                "description": f"执行工具: {tc.get('name', 'unknown')}",
            }
        if event_type == AgentEventType.TOOL_EXECUTION_END:
            tc = data if isinstance(data, dict) else {}
            # TODO 添加友好的工具执行描述
            # desc = tc.get('content')
            tool_name = tc.get('name')
            # render = self._render_tool_front(tc)
            render_raw = tc.get("render", "")
            desc = render_raw or tc.get("name", "unknown")

            data = {
                "tool_name": tc.get("name", ""),
                "tool_id": tc.get("tool_call_id", ""),
                "description": desc,
                "state": tc.get("state", ""),
            }
            # 附加结构化 task_data 供前端任务面板消费
            if tool_name in ("task_create", "task_update") and render_raw:
                try:
                    task_data = json.loads(render_raw)
                    data["task_data"] = task_data
                    data["description"] = task_data.get("task", {}).get("subject", tc.get("name", ""))
                except Exception:
                    data["description"] = render_raw or tc.get("name", "")

    async def publish_runtime_event(
        self,
            publish_context:dict,
        event_type: AgentEventType,
        state: AgentState,
        data=None,
    ) -> None:
        # event结果整理后记录、发布
        if data is None:
            data = {}
        self._sanitize_event_data(event_type,data)
        await self.publish_outbound(
            AgentEvent(
                session_id=publish_context["session_id"],
                request_id=publish_context['request_id'],
                round_counter=publish_context['round_counter'],
                event_type=event_type,
                state=state,
                data=data,
            )
        )

def initialize_message_bus(system_app) -> MessageBus:
    """注册聊天消息存储组件（system_app.initialize_components 调用）。"""
    return system_app.register(MessageBus)