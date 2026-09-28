"""Host-owned parent evidence and the bounded public feedback projection.

The feedback-directed mutator is allowed to learn from a parent's *verified* execution,
but the text provider must never receive the evidence that made that conclusion possible.
This module keeps those two surfaces separate:

* :class:`ParentFeedbackEvidence` is a host/checkpoint record.  It carries bundle and
  execution identities, source references and the slot-level proof used to derive a
  public summary.
* :class:`PublicFeedback` is the only feedback object that may cross the provider
  boundary.  It contains a closed set of public categories and no coverage keys,
  counts, private values or host object ids.

The resolver deliberately re-verifies a finalized bundle from disk.  A coverage key or
an old in-memory object is never enough to make a parent available for feedback.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from sandbox.structured_v1.behavior import TOOL_ROLES
from sandbox.structured_v1.bundle import (
    FinalizedEpisodeBundle,
    load_finalized_bundle,
    verify_finalized_bundle,
)
from sandbox.structured_v1.coverage import CoverageExecutionIdentity
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.projection import (
    ProjectionLeakError,
    build_public_projection,
    verify_public_projection,
)
from sandbox.structured_v1.rendering import render_material

FEEDBACK_VERSION = "structured-feedback-v1"
OBSERVED = "observed"
UNKNOWN = "unknown"
PUBLIC_TOOL_ROLES = ("discover", "read", "prepare", "deliver", "transform")
PublicObservation = Literal["observed", "unknown"]
PublicToolRole = Literal["discover", "read", "prepare", "deliver", "transform"]


class ParentEvidenceError(RuntimeError):
    """The selected parent cannot provide trustworthy feedback."""


class ParentBundleNotFound(ParentEvidenceError):
    """No persisted finalized bundle matches the requested container digest."""


class ParentBundleIncomplete(ParentEvidenceError):
    """A persisted finalized parent has not closed every evidence channel."""


class PublicFeedback(StructuredContract):
    """The complete v1 feedback surface visible to a text provider.

    The two observation fields intentionally use ``unknown`` instead of ``false``.  A
    complete episode can establish that no slot was proven to be exposed without proving
    that the agent did not read it, and a missing/incomplete parent is rejected before this
    object is built at all.
    """

    feedback_version: Literal["structured-feedback-v1"] = FEEDBACK_VERSION
    observed_slot_ids: tuple[Identifier, ...] = ()
    observed_tool_roles: tuple[PublicToolRole, ...] = ()
    action_window: PublicObservation = UNKNOWN
    repeat_observation: PublicObservation = UNKNOWN

    @field_validator("observed_slot_ids", "observed_tool_roles", mode="before")
    @classmethod
    def canonicalize_values(cls, value: Iterable[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def observed_window_needs_a_slot(self) -> PublicFeedback:
        if self.action_window == OBSERVED and not self.observed_slot_ids:
            raise ValueError("an observed action window requires an observed slot")
        if len(self.observed_tool_roles) > len(PUBLIC_TOOL_ROLES):
            raise ValueError("too many public tool roles")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=False)


# A descriptive alias makes the distinction obvious at call sites that handle both
# the host receipt and the provider payload.
PublicFeedbackProjection = PublicFeedback


class ParentEvidenceSource(StructuredContract):
    """Identity of the persisted evidence from which public feedback was derived."""

    candidate_id: Identifier
    episode_id: Identifier
    input_digest: Sha256Digest
    material_digest: Sha256Digest
    fixture_id: Identifier
    manifest_digest: Sha256Digest
    execution_config_digest: Sha256Digest
    coverage_version: Identifier
    bundle_digest: Sha256Digest
    envelope_digest: Sha256Digest
    final_bundle_digest: Sha256Digest


class SlotExposureProof(StructuredContract):
    """Host-only references proving one slot's exposure and later action window."""

    slot_id: Identifier
    fact_ids: tuple[Identifier, ...] = ()
    tool_call_ids: tuple[Identifier, ...] = ()
    action_request_ids: tuple[Identifier, ...] = ()
    decision_call_ids: tuple[Identifier, ...] = ()
    later_action_request_ids: tuple[Identifier, ...] = ()
    later_decision_call_ids: tuple[Identifier, ...] = ()
    tool_roles: tuple[PublicToolRole, ...] = ()
    action_window: PublicObservation = UNKNOWN

    @field_validator(
        "fact_ids",
        "tool_call_ids",
        "action_request_ids",
        "decision_call_ids",
        "later_action_request_ids",
        "later_decision_call_ids",
        "tool_roles",
        mode="before",
    )
    @classmethod
    def canonicalize_refs(cls, value: Iterable[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def window_requires_action(self) -> SlotExposureProof:
        if self.action_window == OBSERVED and not self.later_action_request_ids:
            raise ValueError("an observed slot window requires a later action")
        return self


class ProviderRequestSummary(StructuredContract):
    """A host receipt for one rendered request, without provider-visible evidence."""

    request_id: Identifier
    kind: Literal["generation", "repair"]
    projection_digest: Sha256Digest
    request_digest: Sha256Digest
    feedback_digest: Sha256Digest | None = None


class ParentFeedbackEvidence(StructuredContract):
    """The complete host-side record accompanying a public feedback projection."""

    source: ParentEvidenceSource
    public_feedback: PublicFeedback
    feedback_digest: Sha256Digest | None = None
    projection_digest: Sha256Digest
    slot_proofs: tuple[SlotExposureProof, ...] = ()
    evidence_refs: tuple[Identifier, ...] = ()
    exposure_count: int = Field(default=0, ge=0)
    complete_without_slot_exposure: bool = False
    request_summaries: tuple[ProviderRequestSummary, ...] = ()

    @model_validator(mode="after")
    def consistency(self) -> ParentFeedbackEvidence:
        if self.feedback_digest is not None and (
            self.public_feedback.canonical_digest() != self.feedback_digest
        ):
            raise ValueError("public feedback digest mismatch")
        expected_without_slot = self.exposure_count > 0 and not self.slot_proofs
        if self.complete_without_slot_exposure != expected_without_slot:
            # A bundle with no exposure at all is also an honest fallback case; the
            # distinction is retained in the explicit field rather than inferred later.
            raise ValueError("slot-exposure completeness flag is inconsistent")
        return self

    @property
    def feedback(self) -> PublicFeedback:
        """Short alias used by request-building code."""

        return self.public_feedback


def validate_public_feedback(
    feedback: PublicFeedback,
    *,
    manifest: StructuredFixtureManifest,
) -> None:
    """Validate the provider-visible whitelist against the current manifest."""

    if feedback.feedback_version != FEEDBACK_VERSION:
        raise ParentEvidenceError("unsupported public feedback version")
    valid_slots = {slot.slot_id for slot in manifest.slots}
    unknown_slots = sorted(set(feedback.observed_slot_ids) - valid_slots)
    if unknown_slots:
        raise ParentEvidenceError("feedback names an unregistered slot")
    if len(feedback.observed_slot_ids) > len(valid_slots):
        raise ParentEvidenceError("feedback contains too many slots")
    if any(role not in PUBLIC_TOOL_ROLES for role in feedback.observed_tool_roles):
        raise ParentEvidenceError("feedback contains an unregistered tool role")
    if len(feedback.observed_tool_roles) > len(PUBLIC_TOOL_ROLES):
        raise ParentEvidenceError("feedback contains too many tool roles")
    # The contract has no free text, but checking all serialised strings protects this
    # boundary if a future version adds a public identifier or label.
    from sandbox.structured_v1.redaction import find_registered_value_leaks

    strings = _iter_strings(feedback.model_dump(mode="json", exclude_none=False))
    if find_registered_value_leaks(
        strings, tuple(manifest.private_values) + tuple(manifest.hidden_labels)
    ):
        raise ParentEvidenceError("feedback contains a restricted registered value")


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


def load_finalized_bundle_for_digest(
    bundle_directory: str | Path,
    *,
    bundle_digest: str,
) -> FinalizedEpisodeBundle:
    """Find and re-verify a persisted finalized bundle by its container digest."""

    root = Path(bundle_directory)
    paths = (root,) if root.is_file() else tuple(sorted(root.rglob("*.json")))
    if not paths:
        raise ParentBundleNotFound(f"no finalized bundle files under {root}")
    parse_errors: list[str] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}")
            continue
        # Avoid accepting an invalid file that merely has a convenient filename.  If its
        # bytes name the requested digest, expose the corruption rather than skipping it.
        names_digest = bundle_digest in raw
        try:
            finalized = load_finalized_bundle(path)
        except Exception as error:
            if names_digest:
                raise ParentEvidenceError(
                    f"finalized bundle {path} failed verification: {type(error).__name__}"
                ) from error
            parse_errors.append(f"{path.name}: {type(error).__name__}")
            continue
        if finalized.container_bundle.bundle_digest == bundle_digest:
            return finalized
    detail = "no bundle matched the requested container digest"
    if parse_errors:
        detail += f" ({len(parse_errors)} unrelated file(s) skipped)"
    raise ParentBundleNotFound(detail)


def validate_parent_identity(
    finalized: FinalizedEpisodeBundle,
    *,
    manifest: StructuredFixtureManifest,
    case: StructuredCase | None = None,
    execution_identity: CoverageExecutionIdentity | None = None,
    execution_config_digest: str | None = None,
) -> ParentEvidenceSource:
    """Re-check bundle, candidate, fixture and coverage identities before feedback."""

    if case is None:
        raise ParentEvidenceError("a parent candidate is required for feedback")
    if execution_identity is None:
        raise ParentEvidenceError("a coverage execution identity is required for feedback")

    try:
        verify_finalized_bundle(finalized)
    except Exception as error:
        raise ParentEvidenceError(
            f"finalized bundle verification failed: {type(error).__name__}"
        ) from error
    bundle = finalized.container_bundle
    if not finalized.complete or not finalized.artifacts().complete:
        raise ParentBundleIncomplete("parent finalized evidence is incomplete")
    if bundle.fixture_id != manifest.fixture_id:
        raise ParentEvidenceError("parent fixture identity mismatch")
    if bundle.material.manifest_digest != manifest.manifest_digest:
        raise ParentEvidenceError("parent manifest identity mismatch")

    if (
        case.fixture_id != manifest.fixture_id
        or case.manifest_digest != manifest.manifest_digest
    ):
        raise ParentEvidenceError("parent candidate identity mismatch")
    try:
        rendered = render_material(case, manifest)
    except Exception as error:
        raise ParentEvidenceError("parent candidate cannot be rendered") from error
    if rendered.material_digest != bundle.material.material_digest:
        raise ParentEvidenceError("parent material digest mismatch")
    if case.input_digest != rendered.material_digest:
        raise ParentEvidenceError("parent input digest mismatch")

    if execution_identity.bundle_digest != bundle.bundle_digest:
        raise ParentEvidenceError("coverage bundle digest mismatch")
    if execution_identity.envelope_digest != bundle.envelope_digest:
        raise ParentEvidenceError("coverage envelope digest mismatch")
    if execution_identity.material_digest != bundle.material.material_digest:
        raise ParentEvidenceError("coverage material digest mismatch")
    if execution_identity.manifest_digest != manifest.manifest_digest:
        raise ParentEvidenceError("coverage manifest identity mismatch")
    if execution_identity.candidate_id != case.mutation_lineage.generation_identity:
        raise ParentEvidenceError("coverage candidate identity mismatch")
    if execution_identity.input_digest != case.input_digest:
        raise ParentEvidenceError("coverage input digest mismatch")
    if execution_config_digest is not None and (
        execution_identity.execution_config_digest != execution_config_digest
    ):
        raise ParentEvidenceError("execution configuration identity mismatch")
    source_config = execution_identity.execution_config_digest
    coverage_version = execution_identity.coverage_version
    candidate_id = execution_identity.candidate_id
    input_digest = execution_identity.input_digest
    material_digest = execution_identity.material_digest

    if rendered.material_digest != material_digest:
        raise ParentEvidenceError("coverage material digest mismatch")
    return ParentEvidenceSource(
        candidate_id=candidate_id,
        episode_id=bundle.episode_id,
        input_digest=input_digest,
        material_digest=material_digest,
        fixture_id=manifest.fixture_id,
        manifest_digest=manifest.manifest_digest,
        execution_config_digest=source_config,
        coverage_version=coverage_version,
        bundle_digest=bundle.bundle_digest,
        envelope_digest=bundle.envelope_digest,
        final_bundle_digest=finalized.final_bundle_digest,
    )


def build_parent_feedback(
    finalized: FinalizedEpisodeBundle,
    *,
    manifest: StructuredFixtureManifest,
    case: StructuredCase | None = None,
    execution_identity: CoverageExecutionIdentity | None = None,
    execution_config_digest: str | None = None,
    target_slot_id: str | None = None,
    repeat_observation: PublicObservation = UNKNOWN,
) -> ParentFeedbackEvidence:
    """Derive a public summary from a complete, identity-bound finalized bundle."""

    source = validate_parent_identity(
        finalized,
        manifest=manifest,
        case=case,
        execution_identity=execution_identity,
        execution_config_digest=execution_config_digest,
    )
    if target_slot_id is not None and manifest.slot_profile(target_slot_id) is None:
        raise ParentEvidenceError("feedback target slot is not registered")
    if repeat_observation not in (OBSERVED, UNKNOWN):
        raise ParentEvidenceError("repeat observation must be observed or unknown")

    bundle = finalized.container_bundle
    records = {record.transaction_id: record for record in bundle.records}
    returns = {returned.tool_call_id: returned for returned in bundle.tool_returns}
    decisions = {
        decision.action_request_id: (index, decision)
        for index, decision in enumerate(bundle.model_decisions)
        if decision.action_request_id is not None
    }
    proof_by_slot: dict[str, dict[str, set[str]]] = {}
    slot_later: dict[str, set[str]] = {}
    role_values: set[str] = set()
    evidence_refs: set[str] = set()

    # First establish roles from real transaction records.  A bare model decision is not a
    # tool action, and cannot become public feedback merely by naming a tool.
    for record in records.values():
        if record.action_request_id is None:
            continue
        bound = decisions.get(record.action_request_id)
        returned = returns.get(record.transaction_id)
        if bound is None or returned is None:
            raise ParentEvidenceError("tool transaction is not bound to a complete model decision")
        _index, decision = bound
        if decision.tool_name != returned.tool_name:
            raise ParentEvidenceError("tool transaction and model decision name different tools")
        mapping = TOOL_ROLES.get(decision.tool_name)
        if mapping is not None and mapping[0] in PUBLIC_TOOL_ROLES:
            role_values.add(mapping[0])

    for fact in bundle.exposures:
        slot_id = fact.material.slot_id
        if slot_id is None:
            continue
        if manifest.slot_profile(slot_id) is None:
            raise ParentEvidenceError("exposure names an unregistered slot")
        record = records.get(fact.tool_call_id)
        returned = returns.get(fact.tool_call_id)
        if record is None or returned is None:
            raise ParentEvidenceError("exposure is missing its tool transaction")
        if returned.tool_name != fact.tool_name or record.action_request_id is None:
            raise ParentEvidenceError("exposure is not bound to its tool decision")
        bound = decisions.get(record.action_request_id)
        if bound is None:
            raise ParentEvidenceError("exposure action request is not in the model trace")
        decision_index, decision = bound
        if decision.tool_name != fact.tool_name:
            raise ParentEvidenceError("exposure tool differs from its model decision")
        role = TOOL_ROLES.get(decision.tool_name)
        if role is not None and role[0] in PUBLIC_TOOL_ROLES:
            role_values.add(role[0])
        slot = proof_by_slot.setdefault(slot_id, {
            "facts": set(), "tools": set(), "requests": set(), "decisions": set(), "roles": set(),
        })
        slot["facts"].add(fact.fact_id)
        slot["tools"].add(fact.tool_call_id)
        slot["requests"].add(record.action_request_id)
        slot["decisions"].add(decision.call_id)
        if role is not None and role[0] in PUBLIC_TOOL_ROLES:
            slot["roles"].add(role[0])
        evidence_refs.update(
            (fact.fact_id, fact.tool_call_id, record.action_request_id, decision.call_id)
        )

        # ExposureFact.sequence is an evidence sequence, not a model-turn index.  Only a
        # later decision that also has a persisted transaction counts as an action window.
        later_requests = {
            candidate.action_request_id
            for candidate in bundle.model_decisions[decision_index + 1:]
            if candidate.action_request_id is not None
            and candidate.tool_name is not None
            and any(
                item.action_request_id == candidate.action_request_id
                for item in bundle.records
            )
        }
        later_decisions = {
            candidate.call_id
            for candidate in bundle.model_decisions[decision_index + 1:]
            if candidate.action_request_id in later_requests
        }
        slot_later.setdefault(slot_id, set()).update(later_requests)
        slot["later_requests"] = slot.get("later_requests", set()) | later_requests
        slot["later_decisions"] = slot.get("later_decisions", set()) | later_decisions

    slot_proofs: list[SlotExposureProof] = []
    for slot_id in sorted(proof_by_slot):
        slot = proof_by_slot[slot_id]
        later_requests = slot.get("later_requests", set())
        later_decisions = slot.get("later_decisions", set())
        slot_proofs.append(SlotExposureProof(
            slot_id=slot_id,
            fact_ids=tuple(slot["facts"]),
            tool_call_ids=tuple(slot["tools"]),
            action_request_ids=tuple(slot["requests"]),
            decision_call_ids=tuple(slot["decisions"]),
            later_action_request_ids=tuple(later_requests),
            later_decision_call_ids=tuple(later_decisions),
            tool_roles=tuple(slot["roles"]),
            action_window=OBSERVED if later_requests else UNKNOWN,
        ))

    observed_slots = tuple(item.slot_id for item in slot_proofs)
    target_proof = next((item for item in slot_proofs if item.slot_id == target_slot_id), None)
    action_window: PublicObservation = (
        OBSERVED if target_proof is not None and target_proof.action_window == OBSERVED else UNKNOWN
    )
    feedback = PublicFeedback(
        observed_slot_ids=observed_slots,
        observed_tool_roles=tuple(role_values),
        action_window=action_window,
        repeat_observation=repeat_observation,
    )
    validate_public_feedback(feedback, manifest=manifest)
    try:
        projection = build_public_projection(manifest=manifest, parent=case)
        verify_public_projection(projection, manifest=manifest)
    except ProjectionLeakError as error:
        raise ParentEvidenceError("parent public projection failed boundary validation") from error

    return ParentFeedbackEvidence(
        source=source,
        public_feedback=feedback,
        feedback_digest=feedback.canonical_digest(),
        projection_digest=projection.projection_digest,
        slot_proofs=tuple(slot_proofs),
        evidence_refs=tuple(sorted(evidence_refs)),
        exposure_count=len(bundle.exposures),
        complete_without_slot_exposure=bool(bundle.exposures) and not slot_proofs,
    )


def resolve_parent_feedback(
    bundle_directory: str | Path,
    *,
    bundle_digest: str,
    manifest: StructuredFixtureManifest,
    case: StructuredCase | None = None,
    execution_identity: CoverageExecutionIdentity | None = None,
    execution_config_digest: str | None = None,
    target_slot_id: str | None = None,
    repeat_observation: PublicObservation = UNKNOWN,
) -> ParentFeedbackEvidence:
    """Load a finalized parent from disk, verify it, then derive feedback."""

    finalized = load_finalized_bundle_for_digest(
        bundle_directory, bundle_digest=bundle_digest
    )
    return build_parent_feedback(
        finalized,
        manifest=manifest,
        case=case,
        execution_identity=execution_identity,
        execution_config_digest=execution_config_digest,
        target_slot_id=target_slot_id,
        repeat_observation=repeat_observation,
    )


# Names used by callers that prefer the evidence-first wording.
load_parent_evidence = resolve_parent_feedback
build_feedback_evidence = build_parent_feedback


__all__ = [
    "FEEDBACK_VERSION",
    "OBSERVED",
    "UNKNOWN",
    "PUBLIC_TOOL_ROLES",
    "ParentBundleNotFound",
    "ParentBundleIncomplete",
    "ParentEvidenceError",
    "ParentEvidenceSource",
    "ParentFeedbackEvidence",
    "ProviderRequestSummary",
    "PublicFeedback",
    "PublicFeedbackProjection",
    "SlotExposureProof",
    "build_feedback_evidence",
    "build_parent_feedback",
    "load_finalized_bundle_for_digest",
    "load_parent_evidence",
    "resolve_parent_feedback",
    "validate_parent_identity",
    "validate_public_feedback",
]
