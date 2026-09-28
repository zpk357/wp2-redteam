"""Provider-neutral model decisions recorded by the structured episode."""

from __future__ import annotations

from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from sandbox.structured_v1.models import Identifier, Sha256Digest, StructuredContract
from sandbox.structured_v1.phases import PhaseId


class ModelDecision(StructuredContract):
    """One model turn and the action, if any, selected from that turn."""

    call_id: Identifier
    phase_id: PhaseId | None = None
    action_request_id: Identifier | None = None
    tool_name: Identifier | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    assistant_text: str | None = None
    provider_version: str = "unknown"
    model_name: str = "unknown"
    input_digest: Sha256Digest | None = None
    tool_catalogue_digest: Sha256Digest | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    expense_units: int = Field(default=0, ge=0)
    wall_clock_seconds: int = Field(default=0, ge=0)
    usage_reported: bool = True

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        if self.phase_id is None:
            payload.pop("phase_id", None)
        return payload

    @property
    def stopped(self) -> bool:
        return self.action_request_id is None
