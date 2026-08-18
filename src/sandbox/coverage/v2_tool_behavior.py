"""Extract Office V2 tool-path behavior from trusted coverage input."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    OfficeV2Contract,
    Sha256Digest,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    TimelineEntryKind,
    ToolEvidenceExchange,
)
from sandbox.scenarios.office_v2.oracle_models import EvidenceRef, OutputEvidenceRef

from .v2_behavior import (
    V2BehaviorDimension,
    V2BehaviorFeature,
    V2BehaviorFeatureKind,
    V2BehaviorFeatureTier,
    V2PathAtom,
    V2PathAtomKind,
    build_v2_behavior_feature,
)
from .v2_contracts import V2_COVERAGE_CONTRACT_IDENTITY
from .v2_input import V2CoverageInput


class V2ToolBehaviorExtractionError(ValueError):
    """Trusted V2 facts are insufficient or inconsistent for tool extraction."""


class V2ToolBehaviorExtraction(OfficeV2Contract):
    coverage_identity_digest: Sha256Digest
    canonical_fact_digest: Sha256Digest
    primary_features: tuple[V2BehaviorFeature, ...] = Field(default_factory=tuple)
    secondary_diversity: tuple[V2BehaviorFeature, ...] = Field(default_factory=tuple)
    ordered_tool_atoms: tuple[V2PathAtom, ...] = Field(default_factory=tuple)
    extraction_digest: Sha256Digest

    @field_validator("primary_features", "secondary_diversity")
    @classmethod
    def features_are_unique_and_canonical(
        cls, value: tuple[V2BehaviorFeature, ...]
    ) -> tuple[V2BehaviorFeature, ...]:
        keys = tuple(item.feature_key_digest for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("tool extraction must not repeat feature keys")
        return tuple(sorted(value, key=lambda item: item.feature_key_digest))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"extraction_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def identity_tiers_and_digest_match(self) -> Self:
        if self.coverage_identity_digest != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest:
            raise ValueError("tool extraction uses the wrong coverage identity")
        if any(
            item.tier is not V2BehaviorFeatureTier.PRIMARY
            for item in self.primary_features
        ):
            raise ValueError("tool primary features contain a secondary feature")
        if any(
            item.tier is not V2BehaviorFeatureTier.SECONDARY
            for item in self.secondary_diversity
        ):
            raise ValueError("tool secondary diversity contains a primary feature")
        if self.extraction_digest != sha256_digest(self.digest_payload()):
            raise ValueError("tool behavior extraction digest does not match")
        return self


_TOOL_DOMAINS = {
    "search_email": "mail",
    "read_email": "mail",
    "send_email": "mail",
    "search_calendar_events": "calendar",
    "create_calendar_event": "calendar",
    "update_calendar_event": "calendar",
    "cancel_calendar_event": "calendar",
    "search_drive_files": "drive",
    "read_drive_file": "drive",
    "create_drive_file": "drive",
    "share_drive_file": "drive",
    "update_drive_acl": "drive",
    "delete_drive_file": "drive",
    "list_directory": "workspace",
    "search_files": "workspace",
    "read_file": "workspace",
    "write_file": "workspace",
}


def _dimension(name: str, value: str) -> V2BehaviorDimension:
    return V2BehaviorDimension(name=name, value=value)


def _count_bucket(count: int) -> str:
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"


def _bool_branch(value: bool | None) -> str:
    if value is None:
        return "not-evaluated"
    return "allowed" if value else "denied"


def _tool_domain(exchange: ToolEvidenceExchange) -> str | None:
    return _TOOL_DOMAINS.get(exchange.invocation_ref.tool_name)


def _canonical_refs(refs: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    by_id: dict[str, EvidenceRef] = {}
    for ref in refs:
        existing = by_id.get(ref.evidence_id)
        if existing is not None and existing != ref:
            raise V2ToolBehaviorExtractionError(
                f"evidence id {ref.evidence_id} resolves to conflicting facts"
            )
        by_id[ref.evidence_id] = ref
    return tuple(sorted(by_id.values(), key=lambda item: item.sort_key()))


class _FeatureAccumulator:
    def __init__(self) -> None:
        self._items: dict[
            tuple[V2BehaviorFeatureTier, V2BehaviorFeatureKind, tuple[tuple[str, str], ...]],
            list[EvidenceRef],
        ] = {}

    def add(
        self,
        *,
        tier: V2BehaviorFeatureTier,
        kind: V2BehaviorFeatureKind,
        dimensions: tuple[V2BehaviorDimension, ...],
        evidence_refs: Iterable[EvidenceRef],
    ) -> None:
        canonical_dimensions = tuple(sorted(dimensions, key=lambda item: item.name))
        key = (
            tier,
            kind,
            tuple((item.name, item.value) for item in canonical_dimensions),
        )
        self._items.setdefault(key, []).extend(evidence_refs)

    def build(self) -> tuple[tuple[V2BehaviorFeature, ...], tuple[V2BehaviorFeature, ...]]:
        primary = []
        secondary = []
        for (tier, kind, dimensions), refs in self._items.items():
            feature = build_v2_behavior_feature(
                tier=tier,
                kind=kind,
                dimensions=tuple(
                    V2BehaviorDimension(name=name, value=value)
                    for name, value in dimensions
                ),
                evidence_refs=_canonical_refs(refs),
            )
            (secondary if tier is V2BehaviorFeatureTier.SECONDARY else primary).append(
                feature
            )
        return (
            tuple(sorted(primary, key=lambda item: item.feature_key_digest)),
            tuple(sorted(secondary, key=lambda item: item.feature_key_digest)),
        )


def _tool_runs(coverage_input: V2CoverageInput) -> tuple[tuple[ToolEvidenceExchange, ...], ...]:
    facts = coverage_input.behavior_source_facts
    by_sequence = {item.sequence: item for item in facts.tool_exchanges}
    if len(by_sequence) != len(facts.tool_exchanges):
        raise V2ToolBehaviorExtractionError("tool exchange sequence is not unique")
    seen: set[int] = set()
    runs: list[list[ToolEvidenceExchange]] = []
    current: list[ToolEvidenceExchange] = []
    for entry in facts.timeline:
        if entry.entry_kind is TimelineEntryKind.INTERACTION:
            if current:
                runs.append(current)
                current = []
            continue
        exchange = by_sequence.get(entry.item_sequence)
        if exchange is None or exchange.sequence in seen:
            raise V2ToolBehaviorExtractionError(
                "tool timeline contains a missing or repeated exchange"
            )
        seen.add(exchange.sequence)
        current.append(exchange)
    if current:
        runs.append(current)
    if seen != set(by_sequence):
        raise V2ToolBehaviorExtractionError("tool timeline does not cover every exchange")
    return tuple(tuple(run) for run in runs)


def _add_tool_ngrams(
    accumulator: _FeatureAccumulator,
    runs: tuple[tuple[ToolEvidenceExchange, ...], ...],
) -> None:
    kinds = {
        1: V2BehaviorFeatureKind.TOOL_UNIGRAM,
        2: V2BehaviorFeatureKind.TOOL_BIGRAM,
        3: V2BehaviorFeatureKind.TOOL_TRIGRAM,
    }
    for run in runs:
        for size, kind in kinds.items():
            for start in range(len(run) - size + 1):
                window = run[start : start + size]
                accumulator.add(
                    tier=V2BehaviorFeatureTier.PRIMARY,
                    kind=kind,
                    dimensions=(
                        _dimension(
                            "tools",
                            ">".join(item.invocation_ref.tool_name for item in window),
                        ),
                    ),
                    evidence_refs=(item.invocation_ref for item in window),
                )
        for previous, current in zip(run, run[1:], strict=False):
            source_domain = _tool_domain(previous)
            sink_domain = _tool_domain(current)
            if source_domain is None or sink_domain is None or source_domain == sink_domain:
                continue
            accumulator.add(
                tier=V2BehaviorFeatureTier.PRIMARY,
                kind=V2BehaviorFeatureKind.CROSS_DOMAIN_EDGE,
                dimensions=(
                    _dimension("source_domain", source_domain),
                    _dimension("sink_domain", sink_domain),
                    _dimension("source_tool", previous.invocation_ref.tool_name),
                    _dimension("sink_tool", current.invocation_ref.tool_name),
                ),
                evidence_refs=(previous.invocation_ref, current.invocation_ref),
            )


def _add_argument_features(
    accumulator: _FeatureAccumulator,
    coverage_input: V2CoverageInput,
    exchanges: tuple[ToolEvidenceExchange, ...],
) -> None:
    facts = coverage_input.behavior_source_facts
    outputs: dict[str, tuple[OutputEvidenceRef, ToolEvidenceExchange]] = {}
    for exchange in exchanges:
        for ref in exchange.output_refs:
            outputs[ref.evidence_id] = (ref, exchange)
    frozen = set(facts.frozen_binding_evidence_ids)

    for exchange in exchanges:
        if not exchange.argument_shape_complete:
            raise V2ToolBehaviorExtractionError(
                "tool evidence predates the complete redacted argument-shape contract"
            )
        shape = ",".join(
            f"{item.argument_name}:{item.value_shape}"
            for item in exchange.argument_shape
        ) or "empty"
        accumulator.add(
            tier=V2BehaviorFeatureTier.PRIMARY,
            kind=V2BehaviorFeatureKind.PARAMETER_SHAPE,
            dimensions=(
                _dimension("tool", exchange.invocation_ref.tool_name),
                _dimension("shape", shape),
            ),
            evidence_refs=(exchange.invocation_ref,),
        )

        current_domain = _tool_domain(exchange)
        for source in exchange.argument_sources:
            output_matches = [
                outputs[evidence_id]
                for evidence_id in source.source_evidence_ids
                if evidence_id in outputs
            ]
            output_ids = {item[0].evidence_id for item in output_matches}
            frozen_ids = set(source.source_evidence_ids).intersection(frozen)
            unknown = set(source.source_evidence_ids) - output_ids - frozen_ids
            if unknown:
                raise V2ToolBehaviorExtractionError(
                    "argument source chain contains evidence outside prior tool output "
                    "or frozen task binding"
                )
            if any(item[1].sequence >= exchange.sequence for item in output_matches):
                raise V2ToolBehaviorExtractionError(
                    "argument source chain points to a non-prior tool output"
                )
            origin = (
                "mixed"
                if output_ids and frozen_ids
                else "tool-output"
                if output_ids
                else "frozen-binding"
            )
            source_domains = {
                domain
                for _, source_exchange in output_matches
                if (domain := _tool_domain(source_exchange)) is not None
            }
            cross_domain = bool(
                current_domain is not None
                and source_domains
                and source_domains != {current_domain}
            )
            refs: list[EvidenceRef] = [exchange.invocation_ref]
            refs.extend(item[0] for item in output_matches)
            if frozen_ids:
                refs.append(facts.task_ref)
            accumulator.add(
                tier=V2BehaviorFeatureTier.PRIMARY,
                kind=V2BehaviorFeatureKind.ARGUMENT_SOURCE_CHAIN,
                dimensions=(
                    _dimension("tool", exchange.invocation_ref.tool_name),
                    _dimension("argument_path", ".".join(source.argument_path)),
                    _dimension("mode", source.mode.value),
                    _dimension("origin", origin),
                    _dimension("source_count", _count_bucket(len(source.source_evidence_ids))),
                    _dimension("cross_tool", "yes" if output_ids else "no"),
                    _dimension("cross_domain", "yes" if cross_domain else "no"),
                ),
                evidence_refs=refs,
            )


def _add_permission_and_result_features(
    accumulator: _FeatureAccumulator,
    exchanges: tuple[ToolEvidenceExchange, ...],
) -> None:
    for exchange in exchanges:
        decision = exchange.policy_decision
        if decision is not None:
            assert exchange.decision_ref is not None
            accumulator.add(
                tier=V2BehaviorFeatureTier.PRIMARY,
                kind=V2BehaviorFeatureKind.PERMISSION_BRANCH,
                dimensions=(
                    _dimension("tool", exchange.invocation_ref.tool_name),
                    _dimension("capability", _bool_branch(decision.capability_available)),
                    _dimension("platform", _bool_branch(decision.platform_allowed)),
                    _dimension("delegation", _bool_branch(decision.delegation_allowed)),
                    _dimension("policy", _bool_branch(decision.policy_allowed)),
                    _dimension("effective", _bool_branch(decision.effective_allowed)),
                    _dimension(
                        "policy_mode",
                        "not-evaluated"
                        if decision.policy_enforcement_mode is None
                        else decision.policy_enforcement_mode.value,
                    ),
                    _dimension("enforcement_layer", decision.enforcement_layer.value),
                    _dimension("outcome", decision.outcome.value),
                    _dimension(
                        "reasons",
                        "+".join(item.value for item in decision.reason_codes),
                    ),
                ),
                evidence_refs=(exchange.invocation_ref, exchange.decision_ref),
            )

        transition = (
            "none"
            if exchange.transition_ref is None
            else "committed"
            if exchange.transition_ref.committed
            else "rolled-back"
        )
        refs: list[EvidenceRef] = [exchange.invocation_ref, exchange.result_ref]
        if exchange.transition_ref is not None:
            refs.append(exchange.transition_ref)
        accumulator.add(
            tier=V2BehaviorFeatureTier.PRIMARY,
            kind=V2BehaviorFeatureKind.RESULT_BRANCH,
            dimensions=(
                _dimension("tool", exchange.invocation_ref.tool_name),
                _dimension("status", exchange.status.value),
                _dimension(
                    "failure_code",
                    "none" if exchange.failure_code is None else exchange.failure_code.value,
                ),
                _dimension("transaction", transition),
            ),
            evidence_refs=refs,
        )


def extract_v2_tool_behavior(
    coverage_input: V2CoverageInput,
) -> V2ToolBehaviorExtraction:
    """Extract only Stage 2.2 tool behavior; state and interactions come later."""

    runs = _tool_runs(coverage_input)
    exchanges = tuple(item for run in runs for item in run)
    accumulator = _FeatureAccumulator()
    _add_tool_ngrams(accumulator, runs)
    _add_argument_features(accumulator, coverage_input, exchanges)
    _add_permission_and_result_features(accumulator, exchanges)

    counts = Counter(item.invocation_ref.tool_name for item in exchanges)
    first_by_tool = {
        item.invocation_ref.tool_name: item.invocation_ref for item in exchanges
    }
    for tool_name, count in counts.items():
        accumulator.add(
            tier=V2BehaviorFeatureTier.SECONDARY,
            kind=V2BehaviorFeatureKind.INVOCATION_COUNT,
            dimensions=(
                _dimension("tool", tool_name),
                _dimension("count", _count_bucket(count)),
            ),
            evidence_refs=(first_by_tool[tool_name],),
        )

    primary, secondary = accumulator.build()
    ordered_tool_atoms = tuple(
        V2PathAtom(
            atom_kind=V2PathAtomKind.TOOL,
            semantic_id=f"tool.{item.invocation_ref.tool_name}",
        )
        for item in exchanges
    )
    payload = {
        "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
        "canonical_fact_digest": coverage_input.canonical_fact_digest,
        "primary_features": primary,
        "secondary_diversity": secondary,
        "ordered_tool_atoms": ordered_tool_atoms,
    }
    draft = V2ToolBehaviorExtraction.model_construct(
        **payload,
        extraction_digest="sha256:" + "0" * 64,
    )
    return V2ToolBehaviorExtraction(
        **payload,
        extraction_digest=sha256_digest(draft.digest_payload()),
    )


__all__ = [
    "V2ToolBehaviorExtraction",
    "V2ToolBehaviorExtractionError",
    "extract_v2_tool_behavior",
]
