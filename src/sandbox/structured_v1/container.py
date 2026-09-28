"""What the host hands the container, and what the container must refuse (R2; P6).

The new protocol reaches the container through its own request kind, never through the old
envelope: a request whose kind is not ours is refused with the identity code, so a payload
cannot be routed half-way into the old execution path. The rest of the request is the
envelope the host built plus **the image identity the host actually launched** - the host
observes that, so the container never has to take its own word for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ValidationError

from sandbox.structured_v1.envelope import StructuredEnvelope
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.models import Identifier, StructuredContract

REQUEST_KIND = "structured-case-execution"


class StructuredCaseRequest(StructuredContract):
    """One structured-scenario episode, as the host orders it."""

    request_id: Identifier
    kind: Literal["structured-case-execution"] = REQUEST_KIND
    envelope: StructuredEnvelope


def parse_structured_case_request(payload: Mapping[str, Any]) -> StructuredCaseRequest:
    """Parse the request, refusing anything that is not ours or is not well formed."""

    kind = payload.get("kind")
    if kind != REQUEST_KIND:
        raise EnvelopeRefusal(
            FailureCode.IDENTITY_MISMATCH,
            f"this is not a {REQUEST_KIND} request",
        )
    try:
        return StructuredCaseRequest.model_validate(dict(payload))
    except ValidationError as error:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING,
            f"the structured case request is not well formed: {error.error_count()} problem(s)",
        ) from error
