from shared.schema.common import CamelBaseModel


class BlacklistRequest(CamelBaseModel):
    ip: str
    reason: str | None = None
