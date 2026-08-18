"""Deterministic pre-execution resolution over an actor-visible Office view."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.models import (
    ActorContext,
    BindingResolutionStatus,
    EvidenceId,
    Identifier,
    OfficeV2Contract,
    PredicateField,
    PredicateOperator,
    QueryCardinality,
    QueryTiePolicy,
    RelationDirection,
    ResolvedBinding,
    ResourcePredicate,
    ResourceQuery,
    ResourceRef,
    Sha256Digest,
    canonicalize_identifiers,
    canonicalize_resource_refs,
)
from sandbox.scenarios.office_v2.observation import (
    DriveVersionView,
    ObservationPolicy,
    ObservationQuery,
    ObservedResource,
    observe,
    resource_rights,
)

RESOLVER_VERSION = "office-resource-resolver-v1"
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:-[^\W_]+)*", flags=re.UNICODE)


class ResolutionDisposition(StrEnum):
    MATCHED = "matched"
    PREDICATE_MISMATCH = "predicate_mismatch"
    ACCESS_MISMATCH = "access_mismatch"
    RELATION_MISMATCH = "relation_mismatch"


class ResolutionFailureCode(StrEnum):
    NO_VISIBLE_MATCH = "no_visible_match"
    VISIBLE_ACCESS_MISMATCH = "visible_access_mismatch"
    AMBIGUOUS_MATCH = "ambiguous_match"
    RELATED_BINDING_MISSING = "related_binding_missing"
    RELATED_BINDING_STALE = "related_binding_stale"
    CLARIFICATION_STALE = "clarification_stale"
    SELECTION_OUT_OF_SCOPE = "selection_out_of_scope"


class ResolutionEvidence(OfficeV2Contract):
    evidence_id: EvidenceId
    resource: ResourceRef
    disposition: ResolutionDisposition
    detail_digest: Sha256Digest

    def sort_key(self) -> tuple[str, str, str, str]:
        return (*self.resource.sort_key(), self.disposition.value)


class ResolutionClarification(OfficeV2Contract):
    query_id: Identifier
    binding_name: Identifier
    candidate_refs: tuple[ResourceRef, ...] = Field(min_length=2)
    candidate_evidence_refs: tuple[EvidenceId, ...] = Field(min_length=2)
    resolver_version: Identifier
    world_digest: Sha256Digest
    actor_view_digest: Sha256Digest
    clarification_digest: Sha256Digest

    @field_validator("candidate_refs")
    @classmethod
    def refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("candidate_evidence_refs")
    @classmethod
    def evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="candidate_evidence_refs")


class ResolutionFailure(OfficeV2Contract):
    query_id: Identifier
    code: ResolutionFailureCode
    candidate_evidence_refs: tuple[EvidenceId, ...] = Field(default_factory=tuple)
    resolver_version: Identifier
    world_digest: Sha256Digest
    actor_view_digest: Sha256Digest
    failure_digest: Sha256Digest

    @field_validator("candidate_evidence_refs")
    @classmethod
    def evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="candidate_evidence_refs")


class ResolutionOutcome(OfficeV2Contract):
    query_id: Identifier
    evidence: tuple[ResolutionEvidence, ...]
    binding: ResolvedBinding | None = None
    clarification: ResolutionClarification | None = None
    failure: ResolutionFailure | None = None
    outcome_digest: Sha256Digest

    @field_validator("evidence")
    @classmethod
    def evidence_is_canonical(
        cls, value: tuple[ResolutionEvidence, ...]
    ) -> tuple[ResolutionEvidence, ...]:
        ids = tuple(item.evidence_id for item in value)
        canonicalize_identifiers(ids, field_name="resolution evidence ids")
        return tuple(sorted(value, key=ResolutionEvidence.sort_key))

    @model_validator(mode="after")
    def exactly_one_result(self) -> Self:
        if sum(item is not None for item in (self.binding, self.clarification, self.failure)) != 1:
            raise ValueError("resolution outcome requires exactly one result")
        return self


def resolve_resource_query(
    state: OfficeWorldState,
    actor: ActorContext,
    query: ResourceQuery,
    *,
    related_bindings: Mapping[str, ResolvedBinding] | None = None,
    policy: ObservationPolicy | None = None,
) -> ResolutionOutcome:
    """Resolve a query once without observing or selecting hidden resources."""

    policy = policy or ObservationPolicy()
    world_digest = state.canonical_digest()
    visible = _collect_visible(state, actor, query, policy)
    actor_view_digest = sha256_digest(
        tuple(item.model_dump(mode="json", exclude_none=False) for item in visible)
    )
    relation_failure = _validate_related_bindings(
        query, related_bindings or {}, world_digest
    )
    if relation_failure is not None:
        return _failed(
            query,
            relation_failure,
            (),
            world_digest,
            actor_view_digest,
        )

    evidence = tuple(
        _evaluate_candidate(
            state,
            actor,
            item,
            query,
            related_bindings or {},
        )
        for item in visible
    )
    matched = tuple(
        item for item in evidence if item.disposition is ResolutionDisposition.MATCHED
    )
    if not matched:
        code = (
            ResolutionFailureCode.VISIBLE_ACCESS_MISMATCH
            if any(
                item.disposition is ResolutionDisposition.ACCESS_MISMATCH
                for item in evidence
            )
            else ResolutionFailureCode.NO_VISIBLE_MATCH
        )
        return _failed(query, code, evidence, world_digest, actor_view_digest)

    if query.cardinality is QueryCardinality.EXACTLY_ONE and len(matched) > 1:
        if query.tie_policy is QueryTiePolicy.CLARIFICATION_REQUIRED:
            return _clarification(
                query, evidence, matched, world_digest, actor_view_digest
            )
        return _failed(
            query,
            ResolutionFailureCode.AMBIGUOUS_MATCH,
            evidence,
            world_digest,
            actor_view_digest,
        )

    selected = matched if query.cardinality is QueryCardinality.ONE_OR_MORE else matched[:1]
    refs = tuple(item.resource for item in selected)
    matched_refs = tuple(item.evidence_id for item in selected)
    candidate_refs = tuple(item.evidence_id for item in evidence)
    status = (
        BindingResolutionStatus.RESOLVED_UNIQUE
        if len(refs) == 1
        else BindingResolutionStatus.RESOLVED_SET
    )
    resolution_digest = sha256_digest(
        {
            "query": query,
            "resources": refs,
            "matched_evidence": matched_refs,
            "candidate_evidence": candidate_refs,
            "resolver_version": RESOLVER_VERSION,
            "world_digest": world_digest,
            "actor_view_digest": actor_view_digest,
        }
    )
    binding = ResolvedBinding(
        query_id=query.query_id,
        binding_name=query.binding_name,
        resource_refs=refs,
        matched_fact_refs=matched_refs,
        candidate_evidence_refs=candidate_refs,
        resolution_status=status,
        resolver_version=RESOLVER_VERSION,
        world_digest=world_digest,
        actor_view_digest=actor_view_digest,
        resolution_digest=resolution_digest,
    )
    binding.assert_matches_query(query)
    return _outcome(query.query_id, evidence, binding=binding)


def binding_matches_state(binding: ResolvedBinding, state: OfficeWorldState) -> bool:
    """Detect drift without changing the resource IDs frozen in a binding."""

    return binding.world_digest == state.canonical_digest()


def resolve_clarification_selection(
    state: OfficeWorldState,
    actor: ActorContext,
    query: ResourceQuery,
    clarification: ResolutionClarification,
    selected_ref: ResourceRef,
    *,
    policy: ObservationPolicy | None = None,
) -> ResolutionOutcome:
    """Freeze one authenticated selection from a still-current clarification."""

    current = resolve_resource_query(state, actor, query, policy=policy)
    world_digest, actor_view_digest = _result_context(current)
    if current.clarification != clarification:
        return _failed(
            query,
            ResolutionFailureCode.CLARIFICATION_STALE,
            current.evidence,
            world_digest,
            actor_view_digest,
        )
    if selected_ref not in clarification.candidate_refs:
        return _failed(
            query,
            ResolutionFailureCode.SELECTION_OUT_OF_SCOPE,
            current.evidence,
            world_digest,
            actor_view_digest,
        )

    matched = next(
        item for item in current.evidence if item.resource == selected_ref
    )
    candidate_evidence = tuple(item.evidence_id for item in current.evidence)
    resolution_digest = sha256_digest(
        {
            "query": query,
            "clarification_digest": clarification.clarification_digest,
            "selected_resource": selected_ref,
            "matched_evidence": matched.evidence_id,
            "candidate_evidence": candidate_evidence,
            "resolver_version": RESOLVER_VERSION,
            "world_digest": world_digest,
            "actor_view_digest": actor_view_digest,
        }
    )
    binding = ResolvedBinding(
        query_id=query.query_id,
        binding_name=query.binding_name,
        resource_refs=(selected_ref,),
        matched_fact_refs=(matched.evidence_id,),
        candidate_evidence_refs=candidate_evidence,
        resolution_status=BindingResolutionStatus.RESOLVED_AFTER_CLARIFICATION,
        resolver_version=RESOLVER_VERSION,
        world_digest=world_digest,
        actor_view_digest=actor_view_digest,
        resolution_digest=resolution_digest,
    )
    binding.assert_matches_query(query)
    return _outcome(query.query_id, current.evidence, binding=binding)


def _collect_visible(
    state: OfficeWorldState,
    actor: ActorContext,
    query: ResourceQuery,
    policy: ObservationPolicy,
) -> tuple[ObservedResource, ...]:
    base = ObservationQuery(
        resource_kinds=(query.resource_kind,),
        drive_version_view=DriveVersionView.ALL,
        page_size=policy.maximum_page_size,
    )
    items: list[ObservedResource] = []
    page_query = base
    while True:
        page = observe(state, actor, page_query, policy=policy)
        items.extend(page.items)
        if not page.has_more:
            return tuple(items)
        page_query = base.model_copy(update={"page_token": page.next_page_token})


def _validate_related_bindings(
    query: ResourceQuery,
    bindings: Mapping[str, ResolvedBinding],
    world_digest: str,
) -> ResolutionFailureCode | None:
    for constraint in query.relation_constraints:
        binding = bindings.get(constraint.related_query_id)
        if binding is None:
            return ResolutionFailureCode.RELATED_BINDING_MISSING
        if binding.world_digest != world_digest:
            return ResolutionFailureCode.RELATED_BINDING_STALE
    return None


def _evaluate_candidate(
    state: OfficeWorldState,
    actor: ActorContext,
    item: ObservedResource,
    query: ResourceQuery,
    related_bindings: Mapping[str, ResolvedBinding],
) -> ResolutionEvidence:
    if not all(_matches_predicate(state, item, predicate) for predicate in query.predicates):
        disposition = ResolutionDisposition.PREDICATE_MISMATCH
    elif not set(query.actor_access).issubset(resource_rights(state, actor, item.resource)):
        disposition = ResolutionDisposition.ACCESS_MISMATCH
    elif not all(
        _matches_relation(state, item.resource, constraint, related_bindings)
        for constraint in query.relation_constraints
    ):
        disposition = ResolutionDisposition.RELATION_MISMATCH
    else:
        disposition = ResolutionDisposition.MATCHED
    detail_digest = sha256_digest(
        {
            "query_id": query.query_id,
            "resource": item.resource,
            "disposition": disposition,
            "observation": item,
        }
    )
    return ResolutionEvidence(
        evidence_id=f"resolution-evidence-{detail_digest.removeprefix('sha256:')[:24]}",
        resource=item.resource,
        disposition=disposition,
        detail_digest=detail_digest,
    )


def _matches_predicate(
    state: OfficeWorldState, item: ObservedResource, predicate: ResourcePredicate
) -> bool:
    actual = _predicate_value(state, item, predicate.field)
    if actual is None:
        return False
    expected = predicate.value
    if predicate.operator is PredicateOperator.CONTAINS_TOKEN:
        return _tokens(str(expected)).issubset(_tokens(str(actual)))
    if predicate.operator is PredicateOperator.BEFORE:
        return isinstance(actual, int) and actual < expected
    if predicate.operator is PredicateOperator.AFTER:
        return isinstance(actual, int) and actual > expected
    equal = _normalized(actual) == _normalized(expected)
    return equal if predicate.operator is PredicateOperator.EQUALS else not equal


def _predicate_value(
    state: OfficeWorldState, item: ObservedResource, field: PredicateField
) -> str | int | None:
    if field is PredicateField.PROJECT:
        return item.project_key
    if field is PredicateField.SUBJECT:
        return item.display_name
    if field is PredicateField.OWNER:
        return item.owner_id
    if field is PredicateField.CLASSIFICATION:
        return item.sensitivity.value if item.sensitivity is not None else None
    if field is PredicateField.LIFECYCLE:
        return item.lifecycle_state.value if item.lifecycle_state is not None else None
    if field is PredicateField.START_TIME:
        return item.start_time
    if field is PredicateField.END_TIME:
        return item.end_time
    if field is PredicateField.VERSION_STATE:
        if item.resource.kind.value == "drive_file":
            return "current"
        if item.resource.kind.value != "drive_file_version":
            return None
        current_ids = {file.current_version_id for file in state.domain_graph.drive.files}
        return "current" if item.resource.resource_id in current_ids else "old"
    return None


def _matches_relation(state, candidate, constraint, bindings) -> bool:
    related = bindings[constraint.related_query_id].resource_refs
    for link in state.domain_graph.resource_links:
        if link.relation is not constraint.relation:
            continue
        outbound = _same_resource(candidate, link.source) and any(
            _same_resource(link.target, ref) for ref in related
        )
        inbound = _same_resource(candidate, link.target) and any(
            _same_resource(link.source, ref) for ref in related
        )
        if constraint.direction is RelationDirection.OUTBOUND and outbound:
            return True
        if constraint.direction is RelationDirection.INBOUND and inbound:
            return True
        if constraint.direction is RelationDirection.EITHER and (outbound or inbound):
            return True
    return False


def _same_resource(left: ResourceRef, right: ResourceRef) -> bool:
    if left.kind is not right.kind or left.resource_id != right.resource_id:
        return False
    return (
        left.version_id is None
        or right.version_id is None
        or left.version_id == right.version_id
    )


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _TOKEN_PATTERN.findall(value)}


def _normalized(value: str | int) -> str | int:
    return " ".join(value.split()).casefold() if isinstance(value, str) else value


def _clarification(query, evidence, matched, world_digest, actor_view_digest):
    refs = tuple(item.resource for item in matched)
    evidence_refs = tuple(item.evidence_id for item in matched)
    digest = sha256_digest(
        {
            "query_id": query.query_id,
            "candidates": refs,
            "candidate_evidence": evidence_refs,
            "resolver_version": RESOLVER_VERSION,
            "world_digest": world_digest,
            "actor_view_digest": actor_view_digest,
        }
    )
    clarification = ResolutionClarification(
        query_id=query.query_id,
        binding_name=query.binding_name,
        candidate_refs=refs,
        candidate_evidence_refs=evidence_refs,
        resolver_version=RESOLVER_VERSION,
        world_digest=world_digest,
        actor_view_digest=actor_view_digest,
        clarification_digest=digest,
    )
    return _outcome(query.query_id, evidence, clarification=clarification)


def _failed(query, code, evidence, world_digest, actor_view_digest):
    evidence_refs = tuple(item.evidence_id for item in evidence)
    digest = sha256_digest(
        {
            "query_id": query.query_id,
            "code": code,
            "candidate_evidence": evidence_refs,
            "resolver_version": RESOLVER_VERSION,
            "world_digest": world_digest,
            "actor_view_digest": actor_view_digest,
        }
    )
    failure = ResolutionFailure(
        query_id=query.query_id,
        code=code,
        candidate_evidence_refs=evidence_refs,
        resolver_version=RESOLVER_VERSION,
        world_digest=world_digest,
        actor_view_digest=actor_view_digest,
        failure_digest=digest,
    )
    return _outcome(query.query_id, evidence, failure=failure)


def _outcome(query_id, evidence, *, binding=None, clarification=None, failure=None):
    payload = {
        "query_id": query_id,
        "evidence": evidence,
        "binding": binding,
        "clarification": clarification,
        "failure": failure,
    }
    return ResolutionOutcome(**payload, outcome_digest=sha256_digest(payload))


def _result_context(outcome: ResolutionOutcome) -> tuple[str, str]:
    result = outcome.binding or outcome.clarification or outcome.failure
    if result is None:  # pragma: no cover - guarded by ResolutionOutcome validation
        raise ValueError("resolution outcome has no result")
    return result.world_digest, result.actor_view_digest


__all__ = [
    "RESOLVER_VERSION",
    "ResolutionClarification",
    "ResolutionDisposition",
    "ResolutionEvidence",
    "ResolutionFailure",
    "ResolutionFailureCode",
    "ResolutionOutcome",
    "binding_matches_state",
    "resolve_clarification_selection",
    "resolve_resource_query",
]
