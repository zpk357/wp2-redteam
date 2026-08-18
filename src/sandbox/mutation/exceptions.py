"""Mutation pipeline errors."""

from __future__ import annotations

from enum import StrEnum


class MutationError(RuntimeError):
    pass


class MutationConfigError(MutationError):
    pass


class MutationProviderFailureKind(StrEnum):
    PROVIDER = "provider"
    TRANSPORT = "transport"
    HTTP = "http"
    RESPONSE_TOO_LARGE = "response_too_large"
    TRUNCATED = "truncated"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    MODEL_MISMATCH = "model_mismatch"


class MutationProviderError(MutationError):
    def __init__(
        self,
        message: str,
        *,
        kind: MutationProviderFailureKind = MutationProviderFailureKind.PROVIDER,
        recoverable: bool = False,
        request_digest: str | None = None,
        response_digest: str | None = None,
        response_bytes: int | None = None,
        response_summary: str = "",
        http_status: int | None = None,
        done_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.recoverable = recoverable
        self.request_digest = request_digest
        self.response_digest = response_digest
        self.response_bytes = response_bytes
        self.response_summary = response_summary
        self.http_status = http_status
        self.done_reason = done_reason


class MutationSchemaError(MutationError):
    pass


class MutationIntegrityError(MutationError):
    pass


class MutationStorageError(MutationError):
    pass


class MutationTargetError(MutationError):
    pass
