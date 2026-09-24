"""Async message queue for decoupled channel-agent communication."""

import asyncio
from collections import defaultdict
from typing import Any


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[Any] = asyncio.Queue()
        # self.outbound: asyncio.Queue[Any] = asyncio.Queue()
        self._request_subscribers: dict[str, set[asyncio.Queue[Any]]] = defaultdict(set)

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
