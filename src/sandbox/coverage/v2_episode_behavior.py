"""Complete Office V2 behavior profiles with state, interaction, and termination."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sandbox.scenarios.office_v2.oracle_evidence import TimelineEntryKind
from sandbox.scenarios.office_v2.oracle_models import EvidenceRef
from sandbox.scenarios.office_v2.world import StateObjectKind

from .v2_behavior import (
    V2BehaviorDimension,
    V2BehaviorFeature,
    V2BehaviorFeatureKind,
    V2BehaviorFeatureTier,
    V2BehaviorProfile,
    V2PathAtom,
    V2PathAtomKind,
    build_v2_behavior_feature,
    build_v2_behavior_profile,
    normalize_v2_behavior_path,
)
from .v2_input import V2CoverageInput
from .v2_tool_behavior import extract_v2_tool_behavior


class V2EpisodeBehaviorExtractionError(ValueError):
    """Trusted V2 facts cannot form one complete behavior profile."""


_STATE_DOMAINS = {
    StateObjectKind.MAIL_THREAD: "mail",
    StateObjectKind.MAIL_MESSAGE: "mail",
    StateObjectKind.MAIL_DELIVERY: "mail",
    StateObjectKind.DRIVE_FILE: "drive",
    StateObjectKind.DRIVE_FILE_VERSION: "drive",
    StateObjectKind.ACL_ENTRY: "drive",
    StateObjectKind.SHARE_RECORD: "drive",
    StateObjectKind.CALENDAR_EVENT: "calendar",
    StateObjectKind.ATTENDANCE: "calendar",
    StateObjectKind.WORKSPACE_FILE: "workspace",
}


def _dimension(name: str, value: str) -> V2BehaviorDimension:
    return V2BehaviorDimension(name=name, value=value)


def _count_bucket(count: int) -> str:
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"


def _feature(
    kind: V2BehaviorFeatureKind,
    dimensions: tuple[V2BehaviorDimension, ...],
    refs: Iterable[EvidenceRef],
) -> V2BehaviorFeature:
    return build_v2_behavior_feature(
        tier=V2BehaviorFeatureTier.PRIMARY,
        kind=kind,
        dimensions=dimensions,
        evidence_refs=tuple(refs),
    )


def _merge_features(features: Iterable[V2BehaviorFeature]) -> tuple[V2BehaviorFeature, ...]:
    grouped: dict[str, list[EvidenceRef]] = defaultdict(list)
    templates: dict[str, V2BehaviorFeature] = {}
    for feature in features:
        grouped[feature.feature_key_digest].extend(feature.evidence_refs)
        templates[feature.feature_key_digest] = feature
    merged = []
    for key, refs in grouped.items():
        template = templates[key]
        by_id = {ref.evidence_id: ref for ref in refs}
        merged.append(
            build_v2_behavior_feature(
                tier=template.tier,
                kind=template.kind,
                dimensions=template.dimensions,
                evidence_refs=tuple(
                    sorted(by_id.values(), key=lambda item: item.sort_key())
                ),
            )
        )
    return tuple(sorted(merged, key=lambda item: item.feature_key_digest))


def _state_features(coverage_input: V2CoverageInput) -> tuple[V2BehaviorFeature, ...]:
    features: list[V2BehaviorFeature] = []
    for exchange in coverage_input.behavior_source_facts.tool_exchanges:
        transition = exchange.state_transition
        if transition is None or not transition.committed:
            continue
        assert exchange.transition_ref is not None
        delta = transition.state_delta
        object_groups: dict[tuple[str, str], int] = defaultdict(int)
        for operation, objects in (
            ("created", delta.created_objects),
            ("removed", delta.removed_objects),
        ):
            for item in objects:
                object_groups[(operation, item.kind.value)] += 1
        for (operation, object_kind), count in object_groups.items():
            features.append(
                _feature(
                    V2BehaviorFeatureKind.STATE_OBJECT_CHANGE,
                    (
                        _dimension("operation", operation),
                        _dimension("object_kind", object_kind),
                        _dimension("count", _count_bucket(count)),
                    ),
                    (exchange.transition_ref,),
                )
            )
        for item in delta.changed_fields:
            field_path = ".".join(
                "[]" if str(segment).isdigit() else str(segment)
                for segment in item.field_path
            )
            features.append(
                _feature(
                    V2BehaviorFeatureKind.STATE_FIELD_CHANGE,
                    (
                        _dimension("object_kind", item.object_ref.kind.value),
                        _dimension("operation", item.operation.value),
                        _dimension("field_path", field_path),
                    ),
                    (exchange.transition_ref,),
                )
            )
        for item in delta.changed_relations:
            features.append(
                _feature(
                    V2BehaviorFeatureKind.STATE_RELATION_CHANGE,
                    (
                        _dimension("operation", item.operation.value),
                        _dimension("relation", item.relation),
                    ),
                    (exchange.transition_ref,),
                )
            )
        domains = sorted(
            {
                domain
                for item in (
                    *delta.created_objects,
                    *delta.removed_objects,
                    *(field.object_ref for field in delta.changed_fields),
                )
                if (domain := _STATE_DOMAINS.get(item.kind)) is not None
            }
        )
        if len(domains) > 1:
            features.append(
                _feature(
                    V2BehaviorFeatureKind.STATE_CROSS_DOMAIN,
                    (_dimension("domains", "+".join(domains)),),
                    (exchange.transition_ref,),
                )
            )
    return tuple(features)


def _interaction_dimensions(fact) -> tuple[V2BehaviorDimension, ...]:
    return (
        _dimension("event", fact.event_kind.value),
        _dimension("status", "none" if fact.status is None else fact.status),
        _dimension(
            "failure_code", "none" if fact.failure_code is None else fact.failure_code
        ),
        _dimension(
            "authenticated",
            "not-applicable"
            if fact.authenticated is None
            else "yes"
            if fact.authenticated
            else "no",
        ),
        _dimension("state_advanced", "yes" if fact.advances_state else "no"),
    )


def _path_and_interactions(
    coverage_input: V2CoverageInput,
) -> tuple[tuple[V2PathAtom, ...], tuple[V2BehaviorFeature, ...]]:
    facts = coverage_input.behavior_source_facts
    tools = {item.sequence: item for item in facts.tool_exchanges}
    interactions = {item.sequence: item for item in facts.interaction_facts}
    atoms: list[V2PathAtom] = []
    features: list[V2BehaviorFeature] = []
    refs: list[EvidenceRef] = []
    for entry in facts.timeline:
        if entry.entry_kind is TimelineEntryKind.TOOL:
            exchange = tools.get(entry.item_sequence)
            if exchange is None:
                raise V2EpisodeBehaviorExtractionError("timeline tool is missing")
            atom = V2PathAtom(
                atom_kind=V2PathAtomKind.TOOL,
                semantic_id=f"tool.{exchange.invocation_ref.tool_name}",
            )
            ref: EvidenceRef = exchange.invocation_ref
        else:
            fact = interactions.get(entry.item_sequence)
            if fact is None:
                raise V2EpisodeBehaviorExtractionError("timeline interaction is missing")
            atom = V2PathAtom(
                atom_kind=V2PathAtomKind.INTERACTION,
                semantic_id=f"interaction.{fact.event_kind.value}",
            )
            ref = fact.evidence_ref()
            interaction_refs: list[EvidenceRef] = [ref]
            if fact.transition_ref is not None:
                interaction_refs.append(fact.transition_ref)
            features.append(
                _feature(
                    V2BehaviorFeatureKind.INTERACTION,
                    _interaction_dimensions(fact),
                    interaction_refs,
                )
            )
        if atoms and (
            atoms[-1].atom_kind is V2PathAtomKind.INTERACTION
            or atom.atom_kind is V2PathAtomKind.INTERACTION
        ):
            features.append(
                _feature(
                    V2BehaviorFeatureKind.INTERACTION_EDGE,
                    (
                        _dimension("source", atoms[-1].semantic_id),
                        _dimension("sink", atom.semantic_id),
                    ),
                    (refs[-1], ref),
                )
            )
        atoms.append(atom)
        refs.append(ref)
    termination_atom = V2PathAtom(
        atom_kind=V2PathAtomKind.TERMINATION,
        semantic_id=f"termination.{facts.termination_reason}",
    )
    atoms.append(termination_atom)
    features.append(
        _feature(
            V2BehaviorFeatureKind.TERMINATION,
            (
                _dimension("reason", facts.termination_reason),
                _dimension("submitted", "yes" if facts.submitted else "no"),
            ),
            (facts.termination_ref,),
        )
    )
    return tuple(atoms), tuple(features)


def extract_v2_behavior_profile(coverage_input: V2CoverageInput) -> V2BehaviorProfile:
    tool = extract_v2_tool_behavior(coverage_input)
    atoms, interaction_features = _path_and_interactions(coverage_input)
    primary = _merge_features(
        (*tool.primary_features, *_state_features(coverage_input), *interaction_features)
    )
    return build_v2_behavior_profile(
        canonical_fact_digest=coverage_input.canonical_fact_digest,
        primary_features=primary,
        secondary_diversity=tool.secondary_diversity,
        normalized_path=normalize_v2_behavior_path(atoms),
    )


__all__ = [
    "V2EpisodeBehaviorExtractionError",
    "extract_v2_behavior_profile",
]
