from __future__ import annotations

from dataclasses import dataclass

EXTERNAL_SIGNER_ERROR_SCHEMA_VERSION = 1
EXTERNAL_SIGNER_ERROR_TYPE = "external_signer_error"


@dataclass(frozen=True)
class ExternalSignerFailure:
    code: str
    message: str
    provider: str = "external"
    key_id: str = ""
    request_id: str = ""
    timeout_ms: int | None = None
    exit_status: int | None = None
    cause: str | None = None

    def to_jsonable(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": EXTERNAL_SIGNER_ERROR_SCHEMA_VERSION,
            "type": EXTERNAL_SIGNER_ERROR_TYPE,
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "key_id": self.key_id,
            "request_id": self.request_id,
        }
        if self.timeout_ms is not None:
            payload["timeout_ms"] = self.timeout_ms
        if self.exit_status is not None:
            payload["exit_status"] = self.exit_status
        if self.cause is not None:
            payload["cause"] = self.cause
        return payload


class ExternalSignerError(RuntimeError):
    """Raised when an external corpus signer fails its protocol contract."""

    def __init__(self, failure: ExternalSignerFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)
