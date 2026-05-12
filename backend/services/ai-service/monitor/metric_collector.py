
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monitor.telemetry_schema import OperationName, SpanRecord, TurnTelemetry, BaseTelemetry, SessionTelemetry

if TYPE_CHECKING:
    pass


class MetricCollector:
    """In-memory 指标 buffer for one agent session.
    放redis更新是不是更快？
    """

    def __init__(self) -> None:
        self._session_buffer: list[SessionTelemetry] = []
        self._turn_buffer: list[TurnTelemetry] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_session(self, tele: SessionTelemetry) -> None:
        self._session_buffer.append(tele)

    def add_turn(self, tele: TurnTelemetry) -> None:
        self._turn_buffer.append(tele)

    def update_turn(self,tele:TurnTelemetry) -> None:
        for item in reversed(self._turn_buffer):
            if item.turn_id == tele.turn_id:
                item.ended_at = tele.ended_at
                item.duration_ms = tele.duration_ms
                item.status = tele.status
                item.llm = tele.llm
                item.tool = tele.tool
                item.memory = tele.memory
                item.context = tele.context

                return


