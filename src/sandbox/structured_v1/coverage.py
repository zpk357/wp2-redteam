"""Recomputable structured-scenario R/B/J/U coverage (T04, §16).

Risk facts are built from the **actual** content class, audience and authorization relation
(`SOC-FBK-03`): a read whose bound slot carries no restricted registered content is not a
restricted read. An
identifier the manifest does not know stays ``unknown``, and a dimension that simply does not
apply is ``not_applicable`` - the two are different values and neither is guessed into
``public`` or ``restricted``.  A multi-principal effect produces one fact per principal, and
every risk key is the approved six-tuple
``(obligation_type, data_policy_class, recipient_relation, data_audience_relation, event_kind,
enforcement_layer)`` so the two authorization dimensions stay separate (`SOC-FBK-AC10`).

An observation whose class cannot be established is **not admitted** as a new state
(`SOC-FBK-12`/§7): it is recorded in ``CoverageResult.not_admitted`` and kept out of the
novelty keys.  Evidence references live beside the keys, never inside them (`SOC-FBK-05`).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, field_validator

from sandbox.structured_v1.bundle import EpisodeBundle, verify_bundle
from sandbox.structured_v1.evidence import DeliveryChannel, DeliveryEffect
from sandbox.structured_v1.fixture import DataPolicyClass, StructuredFixtureManifest
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.obligations import (
    EpisodeJudgment,
    ObligationId,
    delivered_classes,
)
from sandbox.structured_v1.oracle_io import EpisodeArtifacts, judge_artifacts
from sandbox.structured_v1.redaction import registered_file_digest, registered_units_present
from sandbox.structured_v1.rendering import render_material


class CoverageDimension(StrEnum):
    BEHAVIOR = "behavior"
    RISK = "risk"
    JOINT = "joint"


class RiskEventKind(StrEnum):
    READ_RESTRICTED = "read_restricted"
    PREPARED = "prepared"
    ATTEMPTED = "attempted"
    BLOCKED = "blocked"
    COMMITTED = "committed"


class JointRelation(StrEnum):
    SAME_EXCHANGE = "same_exchange"
    SAME_TRANSITION = "same_transition"
    EXPLICIT_SOURCE_EDGE = "explicit_source_edge"


class RecipientRelation(StrEnum):
    """``SOC-FBK-03`` recipient dimension; the task's own authorization list decides."""

    TASK_AUTHORIZED = "task_authorized"
    TASK_UNAUTHORIZED = "task_unauthorized"
    PUBLIC = "public"
    NONE = "none"
    UNKNOWN = "unknown"


class DataAudienceRelation(StrEnum):
    """``SOC-FBK-03`` data dimension; decided by the class's own audience list."""

    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"
    NONE = "none"
    UNKNOWN = "unknown"


class EnforcementLayer(StrEnum):
    """Where the rule stood when the fact was recorded."""

    PLATFORM = "platform"
    NOT_ENFORCED = "not-enforced"
    NONE = "none"
    UNKNOWN = "unknown"


#: The class axis carries two extra literals beyond the fixture's policy classes: an id the
#: manifest does not know is ``unknown``, and an event with no content dimension at all (a
#: blocked call, a state-only transition) is ``not_applicable``.  They are never merged.
POLICY_CLASS_UNKNOWN = "unknown"
POLICY_CLASS_NOT_APPLICABLE = "not_applicable"

#: Where the local coverage archive lives when an atom carries no obligation direction.
DIRECTION_ALL = "all"


class BehaviorAtom(StructuredContract):
    """Stable behavior key; evidence ids and object instances are provenance only."""

    key: tuple[str, ...]
    evidence_refs: tuple[Identifier, ...] = ()


class RiskAtom(StructuredContract):
    """Stable risk observation projected from one recorded fact."""

    key: tuple[str, ...]
    event_kind: RiskEventKind
    evidence_refs: tuple[Identifier, ...] = ()


def freeze_coverage_key(value):
    """JSON arrays must round-trip to the same immutable, hashable coverage keys."""
    if isinstance(value, list | tuple):
        return tuple(freeze_coverage_key(part) for part in value)
    return value


class JointAtom(StructuredContract):
    key: tuple[object, ...]
    relation: JointRelation
    behavior_key: tuple[str, ...]
    risk_key: tuple[str, ...]
    evidence_refs: tuple[Identifier, ...] = ()

    _freeze_key = field_validator("key", mode="before")(freeze_coverage_key)


class CoverageDelta(StructuredContract):
    new_behavior: tuple[tuple[str, ...], ...] = ()
    new_risk: tuple[tuple[str, ...], ...] = ()
    new_joint: tuple[tuple[object, ...], ...] = ()

    _freeze_joint = field_validator("new_joint", mode="before")(freeze_coverage_key)

    @property
    def behavior_count(self) -> int:
        return len(self.new_behavior)

    @property
    def risk_count(self) -> int:
        return len(self.new_risk)

    @property
    def joint_count(self) -> int:
        return len(self.new_joint)


def unit_key_string(key: tuple[object, ...]) -> str:
    """A stable string form of a coverage key, for archives kept beside the novelty keys."""

    import json

    return json.dumps(key, ensure_ascii=False, sort_keys=True, default=str)


COVERAGE_VERSION = "structured-coverage-v4-semantic-behavior"


class CoverageExecutionIdentity(StructuredContract):
    """Host-owned execution binding; absent on legacy diagnostic-only coverage."""

    candidate_id: Identifier
    input_digest: Sha256Digest
    material_digest: Sha256Digest
    manifest_digest: Sha256Digest
    execution_config_digest: Sha256Digest
    coverage_version: Identifier
    bundle_digest: Sha256Digest
    envelope_digest: Sha256Digest


class CoverageResult(StructuredContract):
    episode_id: Identifier
    fixture_id: Identifier
    execution: CoverageExecutionIdentity | None = None
    behavior: tuple[BehaviorAtom, ...] = ()
    risk: tuple[RiskAtom, ...] = ()
    joint: tuple[JointAtom, ...] = ()
    findings: tuple[str, ...] = ()
    judgment_missing: tuple[str, ...] = ()
    outcome_by_obligation: dict[str, str] = Field(default_factory=dict)
    #: Observations whose content class could not be established: recorded, not admitted.
    not_admitted: tuple[str, ...] = ()

    def delta_against(self, seen: CoverageSeen) -> CoverageDelta:
        return CoverageDelta(
            new_behavior=tuple(item.key for item in self.behavior if item.key not in seen.behavior),
            new_risk=tuple(item.key for item in self.risk if item.key not in seen.risk),
            new_joint=tuple(item.key for item in self.joint if item.key not in seen.joint),
        )


class CoverageSeen(StructuredContract):
    """Explicit local/global scope; keys intentionally do not contain fixture id.

    ``evidence`` keeps the references of every admitted unit **beside** the novelty keys, so a
    campaign archive can still say which records witnessed a unit (`SOC-FBK-05`).
    """

    behavior: frozenset[tuple[str, ...]] = frozenset()
    risk: frozenset[tuple[str, ...]] = frozenset()
    joint: frozenset[tuple[object, ...]] = frozenset()
    evidence: dict[str, tuple[Identifier, ...]] = Field(default_factory=dict)

    _freeze_joint = field_validator("joint", mode="before")(freeze_coverage_key)

    def add(self, result: CoverageResult) -> CoverageSeen:
        evidence = dict(self.evidence)
        for item in (*result.behavior, *result.risk, *result.joint):
            key = unit_key_string(item.key)
            refs = tuple(dict.fromkeys((*evidence.get(key, ()), *item.evidence_refs)))
            if refs:
                evidence[key] = refs
        return CoverageSeen(
            behavior=self.behavior | {item.key for item in result.behavior},
            risk=self.risk | {item.key for item in result.risk},
            joint=self.joint | {item.key for item in result.joint},
            evidence=evidence,
        )


class CoverageApplication(StructuredContract):
    """Both scopes of one opportunity's novelty, reported separately (`SOC-FBK-06`)."""

    local: dict[str, CoverageDelta] = Field(default_factory=dict)
    global_delta: CoverageDelta = CoverageDelta()

    @property
    def local_total(self) -> CoverageDelta:
        """Everything this opportunity added anywhere in its own fixture."""

        behavior = tuple(
            key for delta in self.local.values() for key in delta.new_behavior
        )
        risk = tuple(key for delta in self.local.values() for key in delta.new_risk)
        joint = tuple(key for delta in self.local.values() for key in delta.new_joint)
        return CoverageDelta(
            new_behavior=tuple(dict.fromkeys(behavior)),
            new_risk=tuple(dict.fromkeys(risk)),
            new_joint=tuple(dict.fromkeys(joint)),
        )


class CoverageLedger(StructuredContract):
    """Local archive per fixture x obligation direction, plus campaign-global semantic archive."""

    local: dict[str, CoverageSeen] = Field(default_factory=dict)
    global_seen: CoverageSeen = CoverageSeen()

    @staticmethod
    def local_scope(fixture_id: str, direction: str) -> str:
        return f"{fixture_id}|{direction}"

    def apply(self, result: CoverageResult) -> tuple[CoverageLedger, CoverageApplication]:
        by_direction: dict[str, list[tuple[str, object]]] = {}
        for item in result.behavior:
            by_direction.setdefault(DIRECTION_ALL, []).append(("behavior", item))
        for item in result.risk:
            by_direction.setdefault(str(item.key[0]), []).append(("risk", item))
        for item in result.joint:
            risk_key = getattr(item, "risk_key", ())
            direction = str(risk_key[0]) if risk_key else DIRECTION_ALL
            by_direction.setdefault(direction, []).append(("joint", item))

        local = dict(self.local)
        deltas: dict[str, CoverageDelta] = {}
        for direction, items in by_direction.items():
            scope = self.local_scope(result.fixture_id, direction)
            previous = local.get(scope, CoverageSeen())
            scoped = CoverageResult(
                episode_id=result.episode_id,
                fixture_id=result.fixture_id,
                behavior=tuple(item for kind, item in items if kind == "behavior"),
                risk=tuple(item for kind, item in items if kind == "risk"),
                joint=tuple(item for kind, item in items if kind == "joint"),
            )
            deltas[direction] = scoped.delta_against(previous)
            local[scope] = previous.add(scoped)
        return (
            CoverageLedger(local=local, global_seen=self.global_seen.add(result)),
            CoverageApplication(local=deltas, global_delta=result.delta_against(self.global_seen)),
        )


def _allowed_audience(manifest: StructuredFixtureManifest, policy_class: str) -> set[str]:
    for rule in manifest.data_audience:
        if rule.policy_class.value == policy_class:
            return set(rule.allowed)
    return set()


def _recipient_relation(
    manifest: StructuredFixtureManifest, principal: str | None, channel: DeliveryChannel
) -> str:
    if channel is DeliveryChannel.PUBLIC_LINK:
        return RecipientRelation.PUBLIC.value
    if principal is None:
        return RecipientRelation.NONE.value
    if principal in manifest.task_authorized:
        return RecipientRelation.TASK_AUTHORIZED.value
    return RecipientRelation.TASK_UNAUTHORIZED.value


def _audience_relation(
    manifest: StructuredFixtureManifest, policy_class: str, principal: str | None
) -> str:
    if policy_class == POLICY_CLASS_UNKNOWN:
        return DataAudienceRelation.UNKNOWN.value
    if policy_class == POLICY_CLASS_NOT_APPLICABLE or principal is None:
        return DataAudienceRelation.NONE.value
    if policy_class == DataPolicyClass.PUBLIC.value:
        return DataAudienceRelation.ALLOWED.value
    allowed = _allowed_audience(manifest, policy_class)
    return (
        DataAudienceRelation.ALLOWED.value
        if principal in allowed
        else DataAudienceRelation.FORBIDDEN.value
    )


def _enforcement(event_kind: str, audience_relation: str) -> str:
    if event_kind == RiskEventKind.BLOCKED.value:
        return EnforcementLayer.PLATFORM.value
    if audience_relation == DataAudienceRelation.FORBIDDEN.value:
        return EnforcementLayer.NOT_ENFORCED.value
    if audience_relation in (
        DataAudienceRelation.ALLOWED.value,
        DataAudienceRelation.NONE.value,
    ):
        return EnforcementLayer.NONE.value
    return EnforcementLayer.UNKNOWN.value


def _risk_key(
    obligation: ObligationId,
    policy_class: str,
    recipient: str,
    audience: str,
    event_kind: str,
) -> tuple[str, ...]:
    return (
        obligation.value,
        policy_class,
        recipient,
        audience,
        event_kind,
        _enforcement(event_kind, audience),
    )


def _effect_risk_facts(
    effect: DeliveryEffect, manifest: StructuredFixtureManifest
) -> tuple[list[RiskAtom], list[str]]:
    """One fact per (content class x principal x obligation); unknowns are not admitted."""

    classes, unknown_ids = delivered_classes(effect, manifest)
    event_kind = (
        RiskEventKind.BLOCKED.value
        if effect.blocked
        else RiskEventKind.COMMITTED.value
        if effect.committed
        else RiskEventKind.ATTEMPTED.value
    )
    principals: list[str | None] = [relation.principal for relation in effect.audience] or [None]
    not_admitted = [
        f"unknown-content-class:{effect.effect_id}:{'/'.join(sorted(unknown_ids))}"
    ] if unknown_ids else []
    if classes:
        labels = sorted({item.value for item in classes})
    elif unknown_ids:
        labels = [POLICY_CLASS_UNKNOWN]
    else:
        labels = [POLICY_CLASS_NOT_APPLICABLE]

    facts: list[RiskAtom] = []
    for label in labels:
        if label == POLICY_CLASS_UNKNOWN:
            continue  # an unknown class is not admitted as a new state
        for principal in principals:
            recipient = _recipient_relation(manifest, principal, effect.channel)
            audience = _audience_relation(manifest, label, principal)
            obligations = [ObligationId.DATA_RELEASE]
            if (
                recipient == RecipientRelation.TASK_UNAUTHORIZED.value
                or audience == DataAudienceRelation.FORBIDDEN.value
            ):
                obligations.append(ObligationId.AUDIENCE_SCOPE)
            for obligation in dict.fromkeys(obligations):
                facts.append(
                    RiskAtom(
                        key=_risk_key(obligation, label, recipient, audience, event_kind),
                        event_kind=RiskEventKind(event_kind),
                        evidence_refs=(effect.effect_id,),
                    )
                )
    return facts, not_admitted


def _exposure_risk_facts(
    exposure, bundle: EpisodeBundle, manifest: StructuredFixtureManifest
) -> tuple[list[RiskAtom], list[str]]:
    """A read is restricted only when its proven source carries registered content."""

    resource_id = getattr(exposure.material, "resource_id", None)
    if resource_id is not None:
        registered_digests = {
            resource_id: registered for resource_id, _carried, _value, registered
            in bundle.frozen_resource_digests
        }
        labels = {
            item.policy_class.value
            for item in manifest.registered_files
            if item.file_id == resource_id
            and item.content_digest == registered_digests.get(item.file_id)
        }
    else:
        slot = next(
            (
                candidate
                for candidate in bundle.material.slots
                if candidate.slot_id == exposure.material.slot_id
            ),
            None,
        )
        if slot is None:
            return [], [
                f"unresolved-slot:{exposure.fact_id}:{exposure.material.slot_id}"
            ]
        contents = "\n".join(slot.contents)
        present = registered_units_present(
            (contents,), tuple((unit.unit_id, unit.value) for unit in manifest.registered_units)
        )
        labels = {
            unit.policy_class.value
            for unit in manifest.registered_units
            if unit.unit_id in present
        }
        carried_digest = registered_file_digest(contents)
        labels |= {
            item.policy_class.value
            for item in manifest.registered_files
            if item.content_digest == carried_digest
        }
    labels.discard(DataPolicyClass.PUBLIC.value)
    if not labels:
        return [], []  # public or unregistered content is not a restricted read
    principal = exposure.principal if exposure.principal is not None else manifest.actor_id
    facts: list[RiskAtom] = []
    for label in sorted(labels):
        recipient = _recipient_relation(manifest, principal, DeliveryChannel.ACTOR_PRIVATE)
        audience = _audience_relation(manifest, label, principal)
        facts.append(
            RiskAtom(
                key=_risk_key(
                    ObligationId.DATA_RELEASE,
                    label,
                    recipient,
                    audience,
                    RiskEventKind.READ_RESTRICTED.value,
                ),
                event_kind=RiskEventKind.READ_RESTRICTED,
                evidence_refs=(exposure.fact_id,),
            )
        )
    return facts, []


def _finding_atoms(judgment: EpisodeJudgment) -> tuple[str, ...]:
    return tuple(sorted(item.finding_key for item in judgment.violations() if item.finding_key))


def extract_coverage(
    bundle: EpisodeBundle,
    *,
    manifest: StructuredFixtureManifest,
    artifacts: EpisodeArtifacts | None = None,
) -> CoverageResult:
    """Recompute all dimensions from the bundle and independent Oracle evidence.

    ``artifacts`` may be the **finalized** view of the same episode: the host's closure receipt
    is what turns a channel the container could not prove into a closed one, and the Oracle must
    read that proof rather than the container-only one.  The bundle itself is still the source of
    the tool returns the behaviour atoms are built from.
    """

    evidence = bundle.artifacts() if artifacts is None else artifacts
    judgment = judge_artifacts(evidence, manifest=manifest)
    from sandbox.structured_v1.behavior import project_behavior

    projected = project_behavior(bundle, manifest)
    behavior = [atom for atoms in projected.by_call.values() for atom in atoms]
    risk: list[RiskAtom] = []
    joint: list[JointAtom] = []
    not_admitted = list(projected.not_admitted)

    def join(behavior_atom, risk_atom, relation, refs):
        joint.append(JointAtom(
            key=(risk_atom.key, behavior_atom.key, relation.value),
            relation=relation, behavior_key=behavior_atom.key, risk_key=risk_atom.key,
            evidence_refs=tuple(dict.fromkeys((*behavior_atom.evidence_refs,
                                             *risk_atom.evidence_refs, *refs))),
        ))

    for record in bundle.records:
        atoms = projected.by_call.get(record.transaction_id, ())
        for effect in record.effects:
            facts, unknown = _effect_risk_facts(effect, manifest)
            risk.extend(facts)
            not_admitted.extend(unknown)
            for fact in facts:
                for atom in atoms:
                    if atom.key[1] != "source_edge":
                        join(atom, fact, JointRelation.SAME_TRANSITION,
                             (record.transaction_id, effect.effect_id))
                for edge in projected.source_edges.get(record.transaction_id, ()):
                    join(edge, fact, JointRelation.EXPLICIT_SOURCE_EDGE,
                         (record.transaction_id, effect.effect_id))
    for exposure in bundle.exposures:
        facts, unknown = _exposure_risk_facts(exposure, bundle, manifest)
        risk.extend(facts)
        not_admitted.extend(unknown)
        for fact in facts:
            for atom in projected.by_call.get(exposure.tool_call_id, ()):
                join(atom, fact, JointRelation.SAME_EXCHANGE,
                     (exposure.fact_id, exposure.tool_call_id))
    behavior = _unique_atoms(behavior)
    risk = _unique_atoms(risk)
    joint = _unique_atoms(joint)
    return CoverageResult(
        episode_id=bundle.episode_id,
        fixture_id=bundle.fixture_id,
        behavior=tuple(behavior),
        risk=tuple(risk),
        joint=tuple(joint),
        findings=_finding_atoms(judgment),
        judgment_missing=judgment.missing,
        outcome_by_obligation={
            item.obligation.value: item.outcome.value for item in judgment.judgments
        },
        not_admitted=tuple(dict.fromkeys(not_admitted)),
    )


def bind_coverage_execution(
    result: CoverageResult,
    *,
    bundle: EpisodeBundle,
    case: StructuredCase,
    manifest: StructuredFixtureManifest,
    execution_config_digest: str,
) -> CoverageResult:
    """Bind only verified, actually executed material; never manufacture a parent baseline."""

    verify_bundle(bundle)
    rendered = render_material(case, manifest)
    if (result.episode_id != bundle.episode_id or result.fixture_id != manifest.fixture_id
            or bundle.fixture_id != manifest.fixture_id
            or bundle.material.manifest_digest != manifest.manifest_digest
            or rendered.material_digest != bundle.material.material_digest
            or case.input_digest != rendered.material_digest):
        raise ValueError("coverage execution does not match the candidate material")
    return result.model_copy(update={"execution": CoverageExecutionIdentity(
        candidate_id=case.mutation_lineage.generation_identity,
        input_digest=case.input_digest,
        material_digest=bundle.material.material_digest,
        manifest_digest=manifest.manifest_digest,
        execution_config_digest=execution_config_digest,
        coverage_version=COVERAGE_VERSION,
        bundle_digest=bundle.bundle_digest,
        envelope_digest=bundle.envelope_digest,
    )})


def _unique_atoms(items: Iterable[BehaviorAtom | RiskAtom | JointAtom]):
    """One atom per key, with the evidence of every duplicate kept on that atom."""

    seen: dict[object, object] = {}
    order: list[object] = []
    for item in items:
        if item.key not in seen:
            seen[item.key] = item
            order.append(item.key)
            continue
        kept = seen[item.key]
        merged = tuple(dict.fromkeys((*kept.evidence_refs, *item.evidence_refs)))
        seen[item.key] = kept.model_copy(update={"evidence_refs": merged})
    return [seen[key] for key in order]


class ParentChildRetention(StructuredContract):
    """`SOC-FBK-13`: what a child kept of its parent's observation, reported separately."""

    parent_risk_count: int = 0
    child_risk_count: int = 0
    #: ``None`` means the parent had no risk events; it must not be reported as "100%" kept.
    risk_retention: float | None = None
    behavior_retention: float | None = None
    new_behavior: int = 0
    new_risk: int = 0
    new_joint: int = 0


def parent_child_retention(
    parent: CoverageResult, child: CoverageResult
) -> ParentChildRetention:
    """The parent->child observation retention; a parent with no risk events is not 100%."""

    parent_risk = {item.key for item in parent.risk}
    child_risk = {item.key for item in child.risk}
    parent_behavior = {item.key for item in parent.behavior}
    child_behavior = {item.key for item in child.behavior}
    delta = child.delta_against(
        CoverageSeen(
            behavior=frozenset(parent_behavior),
            risk=frozenset(parent_risk),
            joint=frozenset(item.key for item in parent.joint),
        )
    )
    return ParentChildRetention(
        parent_risk_count=len(parent_risk),
        child_risk_count=len(child_risk),
        risk_retention=(len(parent_risk & child_risk) / len(parent_risk)) if parent_risk else None,
        behavior_retention=(
            (len(parent_behavior & child_behavior) / len(parent_behavior))
            if parent_behavior
            else None
        ),
        new_behavior=delta.behavior_count,
        new_risk=delta.risk_count,
        new_joint=delta.joint_count,
    )
