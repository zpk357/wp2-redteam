"""Provider contracts and deterministic provider for Office V2 mutation."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, Self

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_brief import (
    MinimalFactBrief,
    MutationCandidateResponse,
    ProviderSlotValue,
)
from .v2_contracts import MutationPlan


class ProviderFailureClass(StrEnum):
    TRANSPORT_TRANSIENT = "transport_transient"
    TIMEOUT_TRANSIENT = "timeout_transient"
    RATE_LIMIT_TRANSIENT = "rate_limit_transient"
    SERVER_TRANSIENT = "server_transient"
    TRUNCATED_TRANSIENT = "truncated_transient"
    CONFIGURATION_PERMANENT = "configuration_permanent"
    MODEL_IDENTITY_PERMANENT = "model_identity_permanent"
    PROTOCOL_INTEGRITY_PERMANENT = "protocol_integrity_permanent"
    AMBIGUOUS = "ambiguous"

    @property
    def retryable(self) -> bool:
        return self in {
            self.TRANSPORT_TRANSIENT,
            self.TIMEOUT_TRANSIENT,
            self.RATE_LIMIT_TRANSIENT,
            self.SERVER_TRANSIENT,
            self.TRUNCATED_TRANSIENT,
        }


class ProviderAttemptState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class MutationProviderAttempt(OfficeV2Contract):
    provider_attempt_id: Identifier
    mutation_plan_digest: Sha256Digest
    attempt_index: int = Field(ge=1)
    state: ProviderAttemptState
    request_digest: Sha256Digest
    response_digest: Sha256Digest | None = None
    response_bytes: int = Field(default=0, ge=0)
    response_summary: str = Field(default="", max_length=512)
    http_status: int | None = Field(default=None, ge=100, le=599)
    truncated: bool = False
    failure_class: ProviderFailureClass | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    actual_cost_microunits: int = Field(default=0, ge=0)
    attempt_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"attempt_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def state_and_digest_match(self) -> Self:
        if self.state is ProviderAttemptState.SUCCEEDED:
            if self.response_digest is None or self.failure_class is not None:
                raise ValueError("successful provider attempt requires response and no failure")
        elif self.failure_class is None:
            raise ValueError("failed provider attempt requires classified failure")
        if (
            self.state is ProviderAttemptState.AMBIGUOUS
            and self.failure_class is not ProviderFailureClass.AMBIGUOUS
        ):
            raise ValueError("ambiguous attempt requires ambiguous failure class")
        if self.attempt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("provider attempt digest does not match")
        return self


class MutationProviderResult(OfficeV2Contract):
    candidate: MutationCandidateResponse
    attempt: MutationProviderAttempt


class V2ProviderFailure(RuntimeError):
    def __init__(self, message: str, *, attempt: MutationProviderAttempt) -> None:
        super().__init__(message)
        self.attempt = attempt


def seal_failed_provider_attempt(
    *,
    plan: MutationPlan,
    attempt_index: int,
    request_digest: str,
    failure_class: ProviderFailureClass,
    response_digest: str | None = None,
    response_bytes: int = 0,
    response_summary: str = "",
    http_status: int | None = None,
    truncated: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    actual_cost_microunits: int = 0,
) -> MutationProviderAttempt:
    state = (
        ProviderAttemptState.AMBIGUOUS
        if failure_class is ProviderFailureClass.AMBIGUOUS
        else ProviderAttemptState.FAILED
    )
    payload = {
        "provider_attempt_id": (
            "provider-attempt."
            + sha256_digest(
                {
                    "plan": plan.plan_digest,
                    "attempt": attempt_index,
                    "request": request_digest,
                    "failure": failure_class.value,
                    "response": response_digest,
                }
            ).removeprefix("sha256:")[:24]
        ),
        "mutation_plan_digest": plan.plan_digest,
        "attempt_index": attempt_index,
        "state": state,
        "request_digest": request_digest,
        "response_digest": response_digest,
        "response_bytes": response_bytes,
        "response_summary": response_summary[:512],
        "http_status": http_status,
        "truncated": truncated,
        "failure_class": failure_class,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "actual_cost_microunits": actual_cost_microunits,
    }
    draft = MutationProviderAttempt.model_construct(
        **payload, attempt_digest="sha256:" + "0" * 64
    )
    return MutationProviderAttempt(
        **payload, attempt_digest=sha256_digest(draft.digest_payload())
    )


class V2MutationProvider(Protocol):
    provider_id: str

    async def generate(
        self, *, plan: MutationPlan, brief: MinimalFactBrief, attempt_index: int
    ) -> MutationProviderResult: ...


class RuleBasedV2MutationProvider:
    """Deterministic no-model provider used to prove the preparation pipeline."""

    provider_id = "provider-rule-based-v2"

    async def generate(
        self, *, plan: MutationPlan, brief: MinimalFactBrief, attempt_index: int
    ) -> MutationProviderResult:
        if plan.provider_id != self.provider_id:
            raise ValueError("mutation plan selects a different provider")
        request_digest = sha256_digest(
            {
                "plan": plan.plan_digest,
                "brief": brief.brief_digest,
                "attempt_index": attempt_index,
            }
        )
        values = tuple(
            ProviderSlotValue(
                payload_slot_id=slot.payload_slot_id,
                generated_content=(
                    f"Controlled test variant {attempt_index}: "
                    f"{brief.parent_payload_texts[index % len(brief.parent_payload_texts)]}"
                )[: slot.max_length],
            )
            for index, slot in enumerate(brief.slots)
        )
        candidate = MutationCandidateResponse(
            slot_values=values,
            expression_metadata={"generator": self.provider_id},
        )
        response_text = candidate.model_dump_json()
        payload = {
            "provider_attempt_id": (
                "provider-attempt."
                + sha256_digest(
                    {"request": request_digest, "response": response_text}
                ).removeprefix("sha256:")[:24]
            ),
            "mutation_plan_digest": plan.plan_digest,
            "attempt_index": attempt_index,
            "state": ProviderAttemptState.SUCCEEDED,
            "request_digest": request_digest,
            "response_digest": sha256_digest(response_text),
            "response_bytes": len(response_text.encode("utf-8")),
            "response_summary": response_text[:256],
            "input_tokens": max(1, len(brief.model_dump_json()) // 4),
            "output_tokens": max(1, len(response_text) // 4),
        }
        draft = MutationProviderAttempt.model_construct(
            **payload, attempt_digest="sha256:" + "0" * 64
        )
        attempt = MutationProviderAttempt(
            **payload, attempt_digest=sha256_digest(draft.digest_payload())
        )
        return MutationProviderResult(candidate=candidate, attempt=attempt)


__all__ = [
    "MutationProviderAttempt",
    "MutationProviderResult",
    "ProviderAttemptState",
    "ProviderFailureClass",
    "RuleBasedV2MutationProvider",
    "V2ProviderFailure",
    "V2MutationProvider",
    "seal_failed_provider_attempt",
]
