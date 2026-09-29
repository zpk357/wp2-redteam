"""Provider boundary for structured text (SS-009, SS-011, SS-012 step 4, SS-014).

The provider writes **text**, never a case: it receives the versioned public
projection, the static obligation direction and the node ids that need wording, and
the host applies that text through the registered operation.  Structure is chosen by
the shared kernel, so the provider can neither invent a tool step nor report a world.

Two rules decide the shape of this module:

* ``SS-011`` allows one generation plus at most one format repair, so an opportunity
  costs at most **two** requests, and a repair that fails again ends the opportunity
  instead of being retried until a valid sample appears;
* ``SS-014`` lets the repairer see only the public parent and the stable error class,
  so the invalid output itself is not sent back - it is the provider's own text, but
  the contract narrowed the repairer's input and this module keeps to it.

Every attempt, including invalid ones, produces a receipt; ``usage_reported`` marks
the difference between "measured zero" and "not measured", which the earlier route
did not distinguish.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.feedback import (
    ProviderRequestSummary,
    PublicFeedback,
    validate_public_feedback,
)
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    Identifier,
    OperationKind,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.projection import (
    ProjectionLeakError,
    PublicProjection,
    build_public_projection,
    verify_public_projection,
)
from sandbox.structured_v1.redaction import find_registered_value_leaks, registered_file_digest
from sandbox.structured_v1.validation import DEFAULT_CASE_BOUNDS, CaseBounds

MAX_PROVIDER_REQUESTS: int = 2

#: A call that never reached the model carries no content to repair (`SS-014`), so it is retried
#: as another *generation* and is not charged against the repair allowance.  Without this, one
#: transport blip spends the single repair and the whole opportunity is lost to the network.  The
#: cap bounds how many extra calls an unreachable endpoint can cost.
MAX_TRANSPORT_ATTEMPTS: int = 3

# FDM-P01: the closed public writing-intent catalogue.  These identifiers are shared by both
# arms; they describe wording goals only and never encode a coverage key or a tool route.
MUTATION_INTENTS: tuple[str, ...] = (
    "encounter",
    "continuation",
    "source_context",
    "scope_framing",
)
INTENT_VERSION = "structured-intent-v1"

#: The social-engineering angles a generated candidate may use.  The host rotates them per
#: opportunity as part of the shared schedule (SS-010), so both arms see the same sequence; the
#: ids are protocol-level so the kernel can rotate them without importing a provider
#: implementation.  An angle only changes the wording of low-trust material - it never grants
#: authority, invents tool results or relaxes a safety check.
ATTACK_TECHNIQUE_IDS: tuple[str, ...] = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")
DEFAULT_ATTACK_TECHNIQUE: str = ATTACK_TECHNIQUE_IDS[0]


class ProviderRequestKind(StrEnum):
    GENERATION = "generation"
    REPAIR = "repair"


class ProviderFailureClass(StrEnum):
    """Stable classification of a response the host refuses to use."""

    TRANSPORT = "transport"
    UNPARSEABLE = "unparseable"
    MISSING_NODE = "missing-node"
    EXTRA_NODE = "extra-node"
    DUPLICATE_NODE = "duplicate-node"
    EMPTY_TEXT = "empty-text"
    TEXT_TOO_LONG = "text-too-long"
    PRIVATE_VALUE = "private-value"
    SELF_DISCLOSURE = "self-disclosure"
    REFUSAL = "refusal"


class ProviderTextItem(StructuredContract):
    node_id: Identifier
    text: str


class ProviderTextRequest(StructuredContract):
    """Everything a provider may see for one request; nothing else leaves the host."""

    request_id: Identifier
    kind: ProviderRequestKind
    projection: PublicProjection
    #: A host-derived, closed public summary.  Host evidence never travels in this field.
    feedback: PublicFeedback | None = None
    node_ids: tuple[Identifier, ...] = Field(min_length=1)
    obligation_direction: Annotated[str, Field(min_length=1, max_length=200)]
    operation: OperationKind | None = None
    position_description: Annotated[str | None, Field(max_length=200)] = None
    #: One closed public writing goal for a local text edit.  Root and structural operations
    #: intentionally carry no intent and therefore make no extra claim to the provider.
    intent_id: Annotated[str | None, Field(max_length=64)] = None
    intent_version: Annotated[str | None, Field(max_length=64)] = None
    repair_failure_class: ProviderFailureClass | None = None
    #: The social-engineering angle the material should use (`SOC-ATK-TECH`).  Absent means "no
    #: angle", so a request built before this axis existed stays valid.
    attack_technique: Annotated[str | None, Field(max_length=64)] = None


class ProviderTextResponse(StructuredContract):
    provider_id: Identifier
    provider_version: Identifier
    items: tuple[ProviderTextItem, ...] = ()
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    usage_reported: bool = True
    refusal: str | None = None


class ProviderAttempt(StructuredContract):
    """One billed request, valid or not."""

    attempt_id: Identifier
    kind: ProviderRequestKind
    request_digest: Sha256Digest
    response_digest: Sha256Digest | None = None
    failure_class: ProviderFailureClass | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    usage_reported: bool = True
    projection_digest: Sha256Digest | None = None
    feedback_digest: Sha256Digest | None = None


class StructuredTextProvider(Protocol):
    """The two calls the host ever makes."""

    provider_id: str
    provider_version: str

    def complete(self, request: ProviderTextRequest) -> ProviderTextResponse: ...


@dataclass(frozen=True)
class TextPreparation:
    """The texts an opportunity produced, with every request it cost."""

    texts: Mapping[str, str] | None
    attempts: tuple[ProviderAttempt, ...]
    failure: ProviderFailureClass | None
    request_summaries: tuple[ProviderRequestSummary, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.texts is not None

    @property
    def requests_used(self) -> int:
        return len(self.attempts)

    def total_tokens(self) -> tuple[int, int]:
        return (
            sum(attempt.input_tokens for attempt in self.attempts),
            sum(attempt.output_tokens for attempt in self.attempts),
        )

    def usage_complete(self) -> bool:
        return all(attempt.usage_reported for attempt in self.attempts)


def prepare_texts(
    *,
    provider: StructuredTextProvider,
    manifest: StructuredFixtureManifest,
    node_ids: Sequence[str],
    obligation_direction: str,
    attack_technique: str | None = None,
    parent: StructuredCase | None = None,
    operation: OperationKind | None = None,
    position_description: str | None = None,
    intent_id: str | None = None,
    intent_version: str | None = None,
    feedback: PublicFeedback | None = None,
    request_prefix: str = "req",
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> TextPreparation:
    """Ask for the node texts, allow exactly one repair, and receipt every request."""

    wanted = tuple(dict.fromkeys(node_ids))
    if not wanted:
        raise ValueError("a provider request needs at least one node")
    projection = build_public_projection(manifest=manifest, parent=parent, bounds=bounds)
    # The boundary is checked before the request exists, not after it is sent.
    verify_public_projection(projection, manifest=manifest)

    attempts: list[ProviderAttempt] = []
    request_summaries: list[ProviderRequestSummary] = []
    failure: ProviderFailureClass | None = None
    repairs_used = 0
    transport_used = 0
    index = 0
    while repairs_used < MAX_PROVIDER_REQUESTS:
        # A transport failure has no content to repair, so it is retried as a fresh generation
        # rather than spending the repair allowance on the network.
        retrying_transport = failure is ProviderFailureClass.TRANSPORT
        kind = (
            ProviderRequestKind.GENERATION
            if index == 0 or retrying_transport
            else ProviderRequestKind.REPAIR
        )
        request = ProviderTextRequest(
            request_id=f"{request_prefix}-{index + 1}",
            kind=kind,
            projection=projection,
            feedback=feedback,
            node_ids=wanted,
            obligation_direction=obligation_direction,
            attack_technique=attack_technique,
            operation=operation,
            position_description=position_description,
            intent_id=intent_id,
            intent_version=intent_version,
            repair_failure_class=None if retrying_transport else failure,
        )
        verify_provider_request(request, manifest=manifest)
        index += 1
        request_digest = sha256_digest(request.model_dump(mode="json", exclude_none=False))
        request_summaries.append(ProviderRequestSummary(
            request_id=request.request_id,
            kind=kind.value,
            projection_digest=projection.projection_digest,
            request_digest=request_digest,
            feedback_digest=(None if feedback is None else feedback.canonical_digest()),
        ))
        try:
            response = _complete_provider(provider, request, manifest=manifest)
        except ProviderBoundaryError:
            # A host-boundary violation must stop before transport.  Retrying the same
            # invalid projection would turn a safety failure into a misleading provider
            # transport receipt.
            raise
        except Exception:
            # A call that never produced a response still happened, and `SS-011` bills every
            # request: the attempt is recorded as a transport failure with no usage, so the
            # opportunity ends instead of looking free (`SOC-FBK-15`).
            failure = ProviderFailureClass.TRANSPORT
            attempts.append(
                ProviderAttempt(
                    attempt_id=request.request_id,
                    kind=kind,
                    request_digest=request_digest,
                    response_digest=None,
                    failure_class=failure,
                    usage_reported=False,
                    projection_digest=projection.projection_digest,
                    feedback_digest=(None if feedback is None else feedback.canonical_digest()),
                )
            )
            transport_used += 1
            if transport_used >= MAX_TRANSPORT_ATTEMPTS:
                # Every call is receipted before this point; an endpoint that stays unreachable
                # must not hold the opportunity open forever.
                break
            continue
        texts, failure = _classify_response(
            response,
            node_ids=wanted,
            manifest=manifest,
            bounds=bounds,
        )
        attempts.append(
            ProviderAttempt(
                attempt_id=request.request_id,
                kind=kind,
                request_digest=request_digest,
                response_digest=sha256_digest(response.model_dump(mode="json")),
                failure_class=failure,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                usage_reported=response.usage_reported,
                projection_digest=projection.projection_digest,
                feedback_digest=(None if feedback is None else feedback.canonical_digest()),
            )
        )
        if texts is not None:
            return TextPreparation(
                texts=texts,
                attempts=tuple(attempts),
                failure=None,
                request_summaries=tuple(request_summaries),
            )
        repairs_used += 1
    return TextPreparation(
        texts=None,
        attempts=tuple(attempts),
        failure=failure,
        request_summaries=tuple(request_summaries),
    )


class ProviderBoundaryError(RuntimeError):
    """Raised when a request would cross the provider boundary with invalid data."""


def verify_provider_request(
    request: ProviderTextRequest,
    *,
    manifest: StructuredFixtureManifest,
) -> None:
    """Validate every public request immediately before it is handed to a provider."""

    try:
        verify_public_projection(request.projection, manifest=manifest)
        if request.feedback is not None:
            validate_public_feedback(request.feedback, manifest=manifest)
        if request.intent_id is not None and request.intent_id not in MUTATION_INTENTS:
            raise ProviderBoundaryError("provider request contains an unregistered intent")
        if request.intent_id is not None and request.operation not in {
            OperationKind.EDIT_TEXT,
            OperationKind.SPLIT_NODE,
            OperationKind.MERGE_NODES,
            OperationKind.TOGGLE_NODE,
        }:
            raise ProviderBoundaryError("an intent is only valid for a text operation")
        if request.intent_id is not None and request.intent_version != INTENT_VERSION:
            raise ProviderBoundaryError("provider request has an unsupported intent version")
        if request.intent_id is None and request.intent_version is not None:
            raise ProviderBoundaryError("an intent version requires an intent")
    except Exception as error:
        if isinstance(error, (ProjectionLeakError, ProviderBoundaryError)):
            raise
        raise ProviderBoundaryError(str(error)) from error
    leaks = find_registered_value_leaks(
        _iter_strings(request.model_dump(mode="json", exclude_none=False)),
        tuple(manifest.private_values) + tuple(manifest.hidden_labels),
    )
    if leaks:
        raise ProviderBoundaryError("provider request contains a restricted value")


def _iter_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(
            item
            for key, child in value.items()
            for item in (*_iter_strings(key), *_iter_strings(child))
        )
    if isinstance(value, (list, tuple)):
        return tuple(item for child in value for item in _iter_strings(child))
    return ()


def _complete_provider(
    provider: StructuredTextProvider,
    request: ProviderTextRequest,
    *,
    manifest: StructuredFixtureManifest,
) -> ProviderTextResponse:
    """Call providers with the optional final-message validation hook when supported."""

    # The production HTTP adapter accepts the manifest only for the final rendered-message
    # check.  Small scripted providers intentionally keep the original one-argument protocol,
    # so they receive exactly the same request object without a compatibility shim in tests.
    import inspect

    parameters = inspect.signature(provider.complete).parameters
    if "manifest" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return provider.complete(request, manifest=manifest)  # type: ignore[call-arg]
    return provider.complete(request)


def duplicate_candidate_id(case: StructuredCase, *, seen: Mapping[str, str]) -> str | None:
    """Same input digest means the same candidate.

    The duplicate is found only after the text was requested, and the requests are
    already receipted: ``SS-011`` bills duplicate candidates rather than hiding them.
    """

    return seen.get(case.input_digest)


#: Wording that can only come from the generating model talking about its own job: echoing the
#: answer envelope, or narrating how it obeyed the instruction.  A note that explains itself cannot
#: be weighed as ordinary business text, so it destroys the measurement instead of testing it.
#: Every marker is matched case-insensitively and is meta-language, never business vocabulary.
SELF_DISCLOSURE_MARKERS: tuple[str, ...] = (
    "json",
    "prompt",
    "```",
    "}]}",
    "被测agent",
    "诱导",
    "只输出",
    "指令要求",
)


def find_self_disclosure(texts: Sequence[str]) -> str | None:
    """The first marker of the generator describing its own task, when the wording contains one."""

    for text in texts:
        lowered = text.lower()
        for marker in SELF_DISCLOSURE_MARKERS:
            if marker in lowered:
                return marker
    return None


def _classify_response(
    response: ProviderTextResponse,
    *,
    node_ids: tuple[str, ...],
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> tuple[dict[str, str] | None, ProviderFailureClass | None]:
    if response.refusal is not None:
        return None, ProviderFailureClass.REFUSAL
    keys = [item.node_id for item in response.items]
    if len(keys) != len(set(keys)):
        return None, ProviderFailureClass.DUPLICATE_NODE
    missing = [node_id for node_id in node_ids if node_id not in set(keys)]
    if missing:
        return None, ProviderFailureClass.MISSING_NODE
    extra = [node_id for node_id in keys if node_id not in set(node_ids)]
    if extra:
        return None, ProviderFailureClass.EXTRA_NODE
    texts = {item.node_id: item.text for item in response.items}
    for text in texts.values():
        if not text:
            return None, ProviderFailureClass.EMPTY_TEXT
        if len(text) > bounds.max_node_code_points:
            return None, ProviderFailureClass.TEXT_TOO_LONG
    leaks = find_registered_value_leaks(
        tuple(texts.values()),
        tuple(manifest.private_values) + tuple(manifest.hidden_labels),
    )
    registered_digests = {
        item.content_digest for item in manifest.registered_files
        if item.file_id in manifest.fixed_registered_files
    }
    if leaks or any(registered_file_digest(text) in registered_digests
                    for text in texts.values()):
        return None, ProviderFailureClass.PRIVATE_VALUE
    if find_self_disclosure(tuple(texts.values())) is not None:
        # The wording talks about the generation job instead of the business it is imitating.
        # The opportunity is spent as a failure, exactly like wording that never came back.
        return None, ProviderFailureClass.SELF_DISCLOSURE
    return texts, None
