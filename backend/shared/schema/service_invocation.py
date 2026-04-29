from __future__ import annotations

from pydantic import Field

from shared.schema.common import CamelBaseModel


class ServiceInvocationError(CamelBaseModel):
    service_name: str = Field(alias="serviceName")
    protocol: str
    operation: str
    target: str | None = None
    message: str
    trace_id: str | None = Field(default=None, alias="traceId")
    code: str | int | None = None
    retryable: bool = False

    def to_message(self) -> str:
        return f"调用 {self.service_name}({self.protocol}) 失败: {self.message}"