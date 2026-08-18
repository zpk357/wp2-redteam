"""Ollama protocol adapter with injected HTTP transport for offline verification."""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_brief import MinimalFactBrief, MutationCandidateResponse
from .v2_contracts import MutationPlan
from .v2_provider import (
    MutationProviderAttempt,
    MutationProviderResult,
    ProviderAttemptState,
    ProviderFailureClass,
    V2ProviderFailure,
    seal_failed_provider_attempt,
)


class OllamaHTTPResponse(OfficeV2Contract):
    status: int = Field(ge=100, le=599)
    body: bytes


class OllamaTransport(Protocol):
    async def post_json(
        self, *, endpoint: str, payload: dict[str, object], timeout_ms: int
    ) -> OllamaHTTPResponse: ...


class OllamaV2MutationProvider:
    provider_id = "provider-ollama-v2"

    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        model_identity_digest: str,
        transport: OllamaTransport,
    ) -> None:
        self.endpoint = endpoint
        self.model_name = model_name
        self.model_identity_digest = model_identity_digest
        self.transport = transport

    async def generate(
        self, *, plan: MutationPlan, brief: MinimalFactBrief, attempt_index: int
    ) -> MutationProviderResult:
        identity_request = sha256_digest(
            {"plan": plan.plan_digest, "attempt_index": attempt_index}
        )
        if plan.provider_id != self.provider_id:
            raise V2ProviderFailure(
                "mutation plan selects a different provider",
                attempt=seal_failed_provider_attempt(
                    plan=plan,
                    attempt_index=attempt_index,
                    request_digest=identity_request,
                    failure_class=ProviderFailureClass.CONFIGURATION_PERMANENT,
                ),
            )
        if plan.model_identity_digest != self.model_identity_digest:
            raise V2ProviderFailure(
                "Ollama model identity drifted",
                attempt=seal_failed_provider_attempt(
                    plan=plan,
                    attempt_index=attempt_index,
                    request_digest=identity_request,
                    failure_class=ProviderFailureClass.MODEL_IDENTITY_PERMANENT,
                ),
            )
        payload = {
            "model": self.model_name,
            "stream": False,
            "format": MutationCandidateResponse.model_json_schema(),
            "prompt": brief.model_dump_json(exclude_none=False),
            "options": {
                "temperature": 0,
                "num_predict": plan.budget.per_attempt_token_limit,
                "seed": int(plan.plan_digest.removeprefix("sha256:")[:8], 16),
            },
        }
        request_digest = sha256_digest(payload)
        try:
            response = await self.transport.post_json(
                endpoint=self.endpoint,
                payload=payload,
                timeout_ms=plan.budget.timeout_ms,
            )
        except TimeoutError as exc:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.TIMEOUT_TRANSIENT
            ) from exc
        except ConnectionError as exc:
            raise self._failure(
                plan,
                attempt_index,
                request_digest,
                ProviderFailureClass.TRANSPORT_TRANSIENT,
            ) from exc
        except Exception as exc:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.AMBIGUOUS
            ) from exc
        if response.status != 200:
            failure_class = self._http_failure_class(response.status)
            raise self._failure(
                plan,
                attempt_index,
                request_digest,
                failure_class,
                response=response,
            )
        try:
            envelope = json.loads(response.body)
            if envelope.get("done") is False or envelope.get("done_reason") == "length":
                raise self._failure(
                    plan,
                    attempt_index,
                    request_digest,
                    ProviderFailureClass.TRUNCATED_TRANSIENT,
                    response=response,
                    truncated=True,
                )
            candidate_text = envelope["response"]
            candidate = MutationCandidateResponse.model_validate_json(candidate_text)
        except V2ProviderFailure:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise self._failure(
                plan,
                attempt_index,
                request_digest,
                ProviderFailureClass.PROTOCOL_INTEGRITY_PERMANENT,
                response=response,
            ) from exc
        response_digest = sha256_bytes(response.body)
        attempt_payload = {
            "provider_attempt_id": (
                "provider-attempt."
                + sha256_digest(
                    {"request": request_digest, "response": response_digest}
                ).removeprefix("sha256:")[:24]
            ),
            "mutation_plan_digest": plan.plan_digest,
            "attempt_index": attempt_index,
            "state": ProviderAttemptState.SUCCEEDED,
            "request_digest": request_digest,
            "response_digest": response_digest,
            "response_bytes": len(response.body),
            "response_summary": response.body[:256].decode("utf-8", errors="replace"),
            "http_status": response.status,
            "input_tokens": int(envelope.get("prompt_eval_count", 0)),
            "output_tokens": int(envelope.get("eval_count", 0)),
        }
        draft = MutationProviderAttempt.model_construct(
            **attempt_payload, attempt_digest="sha256:" + "0" * 64
        )
        attempt = MutationProviderAttempt(
            **attempt_payload,
            attempt_digest=sha256_digest(draft.digest_payload()),
        )
        return MutationProviderResult(candidate=candidate, attempt=attempt)

    @staticmethod
    def _http_failure_class(status: int) -> ProviderFailureClass:
        if status == 429:
            return ProviderFailureClass.RATE_LIMIT_TRANSIENT
        if status in {500, 502, 503, 504}:
            return ProviderFailureClass.SERVER_TRANSIENT
        if 400 <= status < 500:
            return ProviderFailureClass.CONFIGURATION_PERMANENT
        return ProviderFailureClass.PROTOCOL_INTEGRITY_PERMANENT

    @staticmethod
    def _failure(
        plan: MutationPlan,
        attempt_index: int,
        request_digest: str,
        failure_class: ProviderFailureClass,
        *,
        response: OllamaHTTPResponse | None = None,
        truncated: bool = False,
    ) -> V2ProviderFailure:
        body = response.body if response is not None else b""
        return V2ProviderFailure(
            failure_class.value,
            attempt=seal_failed_provider_attempt(
                plan=plan,
                attempt_index=attempt_index,
                request_digest=request_digest,
                failure_class=failure_class,
                response_digest=sha256_bytes(body) if body else None,
                response_bytes=len(body),
                response_summary=body[:256].decode("utf-8", errors="replace"),
                http_status=response.status if response is not None else None,
                truncated=truncated,
            ),
        )


__all__ = [
    "OllamaHTTPResponse",
    "OllamaTransport",
    "OllamaV2MutationProvider",
]
