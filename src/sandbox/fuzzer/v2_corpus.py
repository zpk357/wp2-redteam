"""Office V2 seed, execution, and corpus contracts.

The four persistent objects deliberately answer different questions:
ExecutableSeedSupport is runtime materialization support, MaterializedCandidate
is delivered input, ExecutionRecord is observed execution, and CorpusEntry is
scheduling state. The formal semantic seed lives in v2_seed_pools.FrozenSeed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
)


class SeedKind(StrEnum):
    RISK = "risk"
    EXPLORATION = "exploration"


class CorpusEntryState(StrEnum):
    ACTIVE = "active"
    COOLED = "cooled"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class PayloadSpec(OfficeV2Contract):
    payload_spec_id: Identifier
    content: str = Field(min_length=1, max_length=8192)
    carrier_kind: Identifier
    field_path: str = Field(min_length=1, max_length=512)
    placement_round: int = Field(default=0, ge=0)
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def content_digest_matches(self) -> Self:
        if self.content_digest != sha256_digest({"content": self.content}):
            raise ValueError("payload spec content digest does not match")
        return self


class ExecutableSeedSupport(OfficeV2Contract):
    """Execution-only support for one formal semantic seed.

    This record is deliberately not the formal seed. It may contain carrier,
    binding, and lineage data needed to materialize a scenario, while the
    formal seed remains only ``attack_target`` plus ``base_text``.
    """

    seed_id: Identifier
    payload_specs: tuple[PayloadSpec, ...] = Field(min_length=1)
    operator_history: tuple[Identifier, ...] = Field(default_factory=tuple)
    parent_seed_id: Identifier | None = None
    root_seed_id: Identifier
    generation_depth: int = Field(ge=0)
    seed_content_digest: Sha256Digest

    @field_validator("payload_specs")
    @classmethod
    def payloads_are_canonical(cls, value: tuple[PayloadSpec, ...]) -> tuple[PayloadSpec, ...]:
        ids = tuple(item.payload_spec_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("attack seed payload ids must be unique")
        return tuple(sorted(value, key=lambda item: item.payload_spec_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"seed_content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def lineage_and_digest_match(self) -> Self:
        if self.generation_depth == 0:
            if self.parent_seed_id is not None or self.root_seed_id != self.seed_id:
                raise ValueError("root executable support lineage does not close")
        elif self.parent_seed_id is None:
            raise ValueError("derived executable support requires parent_seed_id")
        if self.seed_content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("executable support digest does not match")
        return self


class DeliveredPayload(OfficeV2Contract):
    payload_spec_id: Identifier
    resource_id: Identifier
    resource_version: Identifier
    field_path: str = Field(min_length=1, max_length=512)
    content_digest: Sha256Digest
    materialization_evidence_digest: Sha256Digest


class MaterializedCandidate(OfficeV2Contract):
    materialized_candidate_id: Identifier
    seed_id: Identifier
    generation_allocation_id: Identifier
    scenario_case_id: Identifier
    actor_id: Identifier
    task_id: Identifier
    resource_binding_digest: Sha256Digest
    delivered_payloads: tuple[DeliveredPayload, ...] = Field(min_length=1)
    binding_source_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    materialization_digest: Sha256Digest

    @field_validator("delivered_payloads")
    @classmethod
    def delivered_are_canonical(
        cls, value: tuple[DeliveredPayload, ...]
    ) -> tuple[DeliveredPayload, ...]:
        keys = tuple((item.payload_spec_id, item.resource_id, item.field_path) for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("delivered payload locations must be unique")
        return tuple(sorted(value, key=lambda item: (item.payload_spec_id, item.resource_id)))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"materialization_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.materialization_digest != sha256_digest(self.digest_payload()):
            raise ValueError("materialized candidate digest does not match")
        return self


class PayloadExecutionRef(OfficeV2Contract):
    payload_spec_id: Identifier
    evidence_digest: Sha256Digest


class ExecutionCosts(OfficeV2Contract):
    mutator_tokens: int = Field(default=0, ge=0)
    agent_tokens: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    monetary_microunits: int = Field(default=0, ge=0)


class FrozenRootSupportRecord(OfficeV2Contract):
    """Frozen materialization facts used before any real Episode executes."""

    support_record_id: Identifier
    seed_id: Identifier
    materialized_candidate_id: Identifier
    scenario_case_id: Identifier
    actor_id: Identifier
    task_id: Identifier
    resource_binding_digest: Sha256Digest
    binding_source_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    support_digest: Sha256Digest

    @property
    def execution_record_id(self) -> str:
        """Compatibility accessor for allocation/receipt contracts."""
        return self.support_record_id

    @property
    def normal_task_completed(self) -> bool:
        return False

    @property
    def costs(self) -> ExecutionCosts:
        return ExecutionCosts()

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"support_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.support_digest != sha256_digest(self.digest_payload()):
            raise ValueError("frozen root support digest does not match")
        return self


class ExecutionRecord(OfficeV2Contract):
    execution_record_id: Identifier
    seed_id: Identifier
    materialized_candidate_id: Identifier
    scenario_case_id: Identifier
    actor_id: Identifier
    task_id: Identifier
    resource_binding_digest: Sha256Digest
    binding_source_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    final_state_digest: Sha256Digest
    evidence_bundle_digest: Sha256Digest
    behavior_source_digest: Sha256Digest
    episode_digest: Sha256Digest
    manifest_digest: Sha256Digest
    oracle_fact_digest: Sha256Digest
    oracle_result_digest: Sha256Digest
    coverage_facts_digest: Sha256Digest
    coverage_delta_digest: Sha256Digest
    observed_contribution_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    observed_payload_refs: tuple[PayloadExecutionRef, ...] = Field(default_factory=tuple)
    used_payload_refs: tuple[PayloadExecutionRef, ...] = Field(default_factory=tuple)
    exposure_stages: tuple[Identifier, ...] = Field(default_factory=tuple)
    utility_disposition: Identifier
    normal_task_completed: bool
    submitted: bool
    termination_reason: Identifier
    cleanup_confirmed: bool
    attempt_receipt_ids: tuple[Identifier, ...] = Field(min_length=1)
    costs: ExecutionCosts
    record_digest: Sha256Digest

    @field_validator("observed_contribution_keys", "attempt_receipt_ids")
    @classmethod
    def simple_lists_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("execution record values must be unique")
        return tuple(sorted(value))

    @field_validator("exposure_stages")
    @classmethod
    def exposure_is_ordered_prefix(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        order = ("planned", "delivered", "observed", "used")
        if value != order[: len(value)]:
            raise ValueError("exposure stages must be an ordered factual prefix")
        return value

    @model_validator(mode="after")
    def evidence_prefix_and_digest_match(self) -> Self:
        observed = {item.payload_spec_id for item in self.observed_payload_refs}
        used = {item.payload_spec_id for item in self.used_payload_refs}
        if not used.issubset(observed):
            raise ValueError("used payloads must first be observed")
        stages = set(self.exposure_stages)
        if observed and "observed" not in stages:
            raise ValueError("observed payload evidence requires observed exposure")
        if used and "used" not in stages:
            raise ValueError("used payload evidence requires used exposure")
        if "used" in stages and "observed" not in stages:
            raise ValueError("used exposure requires observed exposure")
        if self.record_digest != sha256_digest(self.digest_payload()):
            raise ValueError("execution record digest does not match")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"record_digest"}, exclude_none=False)


class CorpusStatistics(OfficeV2Contract):
    selection_count: int = Field(default=0, ge=0)
    child_count: int = Field(default=0, ge=0)
    productive_child_count: int = Field(default=0, ge=0)
    consecutive_no_gain: int = Field(default=0, ge=0)
    total_cost_microunits: int = Field(default=0, ge=0)


class CorpusEntry(OfficeV2Contract):
    corpus_entry_id: Identifier
    seed_id: Identifier
    seed_kind: SeedKind
    promotion_reasons: tuple[Identifier, ...] = Field(min_length=1)
    execution_record_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    supporting_root_record_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    risk_contribution_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    behavior_contribution_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    compatibility_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    state: CorpusEntryState = CorpusEntryState.ACTIVE
    statistics: CorpusStatistics = Field(default_factory=CorpusStatistics)
    entry_digest: Sha256Digest

    @field_validator(
        "promotion_reasons",
        "execution_record_ids",
        "supporting_root_record_ids",
        "risk_contribution_keys",
        "behavior_contribution_keys",
        "compatibility_digests",
    )
    @classmethod
    def indexes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("corpus indexes must be unique")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"entry_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.seed_kind is SeedKind.RISK and not self.risk_contribution_keys:
            raise ValueError("risk corpus entry requires risk contribution")
        if not self.execution_record_ids and not self.supporting_root_record_ids:
            raise ValueError("corpus entry requires execution or frozen root support")
        if self.entry_digest != sha256_digest(self.digest_payload()):
            raise ValueError("corpus entry digest does not match")
        return self


class V2CorpusSnapshot(OfficeV2Contract):
    execution_supports: tuple[ExecutableSeedSupport, ...] = Field(default_factory=tuple)
    materialized_candidates: tuple[MaterializedCandidate, ...] = Field(default_factory=tuple)
    execution_records: tuple[ExecutionRecord, ...] = Field(default_factory=tuple)
    root_support_records: tuple[FrozenRootSupportRecord, ...] = Field(default_factory=tuple)
    entries: tuple[CorpusEntry, ...] = Field(default_factory=tuple)
    snapshot_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"snapshot_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def lineage_and_digest_match(self) -> Self:
        for values, label, identity in (
            (self.execution_supports, "execution support", lambda item: item.seed_id),
            (
                self.materialized_candidates,
                "materialized candidate",
                lambda item: item.materialized_candidate_id,
            ),
            (
                self.execution_records,
                "execution record",
                lambda item: item.execution_record_id,
            ),
            (
                self.root_support_records,
                "frozen root support record",
                lambda item: item.support_record_id,
            ),
            (self.entries, "corpus entry", lambda item: item.corpus_entry_id),
        ):
            ids = tuple(identity(item) for item in values)
            if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
                raise ValueError(f"corpus snapshot {label} values must be canonical")
        seed_ids = {item.seed_id for item in self.execution_supports}
        candidate_by_id = {
            item.materialized_candidate_id: item for item in self.materialized_candidates
        }
        execution_by_id = {item.execution_record_id: item for item in self.execution_records}
        support_by_id = {item.support_record_id: item for item in self.root_support_records}
        if any(item.seed_id not in seed_ids for item in self.materialized_candidates):
            raise ValueError("corpus snapshot candidate refers to unknown seed")
        if any(
            item.materialized_candidate_id not in candidate_by_id
            or candidate_by_id[item.materialized_candidate_id].seed_id != item.seed_id
            for item in self.execution_records
        ):
            raise ValueError("corpus snapshot execution lineage does not close")
        if any(
            item.seed_id not in seed_ids
            or any(
                record_id not in execution_by_id
                or execution_by_id[record_id].seed_id != item.seed_id
                for record_id in item.execution_record_ids
            )
            for item in self.entries
        ):
            raise ValueError("corpus snapshot entry lineage does not close")
        if any(
            item.seed_id not in seed_ids
            or any(
                record_id not in support_by_id or support_by_id[record_id].seed_id != item.seed_id
                for record_id in item.supporting_root_record_ids
            )
            for item in self.entries
        ):
            raise ValueError("corpus snapshot root support lineage does not close")
        if self.snapshot_digest != sha256_digest(self.digest_payload()):
            raise ValueError("corpus snapshot digest does not match")
        return self


def seal_contract(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "sha256:" + "0" * 64})
    digest_payload = draft.digest_payload()
    return model_type(**payload, **{digest_field: sha256_digest(digest_payload)})


class V2Corpus:
    """A single physical corpus with deterministic logical views."""

    def __init__(self) -> None:
        self._execution_supports: dict[str, ExecutableSeedSupport] = {}
        self._candidates: dict[str, MaterializedCandidate] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._root_support_records: dict[str, FrozenRootSupportRecord] = {}
        self._entries: dict[str, CorpusEntry] = {}

    def add_execution_support(self, support: ExecutableSeedSupport) -> None:
        self._insert_immutable(
            self._execution_supports, support.seed_id, support, "execution support"
        )

    def add_candidate(self, candidate: MaterializedCandidate) -> None:
        if candidate.seed_id not in self._execution_supports:
            raise ValueError("materialized candidate refers to unknown seed")
        self._insert_immutable(
            self._candidates,
            candidate.materialized_candidate_id,
            candidate,
            "candidate",
        )

    def add_execution(self, record: ExecutionRecord) -> None:
        candidate = self._candidates.get(record.materialized_candidate_id)
        if candidate is None or candidate.seed_id != record.seed_id:
            raise ValueError("execution record lineage does not close")
        self._insert_immutable(self._executions, record.execution_record_id, record, "execution")

    def add_root_support(self, record: FrozenRootSupportRecord) -> None:
        if record.seed_id not in self._execution_supports:
            raise ValueError("frozen root support refers to unknown seed")
        candidate = self._candidates.get(record.materialized_candidate_id)
        if candidate is None or candidate.seed_id != record.seed_id:
            raise ValueError("frozen root support candidate lineage does not close")
        self._insert_immutable(
            self._root_support_records,
            record.support_record_id,
            record,
            "frozen root support",
        )

    def add_entry(self, entry: CorpusEntry) -> None:
        if entry.seed_id not in self._execution_supports:
            raise ValueError("corpus entry refers to unknown seed")
        if any(
            item not in self._executions or self._executions[item].seed_id != entry.seed_id
            for item in entry.execution_record_ids
        ):
            raise ValueError("corpus entry refers to an incompatible execution")
        if any(
            item not in self._root_support_records
            or self._root_support_records[item].seed_id != entry.seed_id
            for item in entry.supporting_root_record_ids
        ):
            raise ValueError("corpus entry refers to incompatible frozen root support")
        self._insert_immutable(self._entries, entry.corpus_entry_id, entry, "entry")

    def replace_entry(self, entry: CorpusEntry) -> None:
        if entry.corpus_entry_id not in self._entries:
            raise ValueError("cannot replace an unknown corpus entry")
        if entry.seed_id != self._entries[entry.corpus_entry_id].seed_id:
            raise ValueError("corpus entry replacement cannot change seed lineage")
        self._entries[entry.corpus_entry_id] = entry

    @staticmethod
    def _insert_immutable(store: dict, key: str, value: object, kind: str) -> None:
        existing = store.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"{kind} id already has different immutable content")
        store[key] = value

    def risk_view(self, contribution_key: str) -> tuple[CorpusEntry, ...]:
        return self._view(lambda item: contribution_key in item.risk_contribution_keys)

    def behavior_view(self, contribution_key: str) -> tuple[CorpusEntry, ...]:
        return self._view(lambda item: contribution_key in item.behavior_contribution_keys)

    def compatibility_view(self, digest: str) -> tuple[CorpusEntry, ...]:
        return self._view(lambda item: digest in item.compatibility_digests)

    def lineage_view(self, root_seed_id: str) -> tuple[ExecutableSeedSupport, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._execution_supports.values()
                    if item.root_seed_id == root_seed_id
                ),
                key=lambda item: (item.generation_depth, item.seed_content_digest),
            )
        )

    def supporting_executions(self, entry_id: str) -> tuple[ExecutionRecord, ...]:
        entry = self._entries[entry_id]
        return tuple(self._executions[item] for item in entry.execution_record_ids)

    def supporting_root_records(self, entry_id: str) -> tuple[FrozenRootSupportRecord, ...]:
        entry = self._entries[entry_id]
        return tuple(self._root_support_records[item] for item in entry.supporting_root_record_ids)

    def supporting_records(
        self, entry_id: str
    ) -> tuple[ExecutionRecord | FrozenRootSupportRecord, ...]:
        return (*self.supporting_executions(entry_id), *self.supporting_root_records(entry_id))

    def parent_entry(self, *, seed_id: str, supporting_execution_record_id: str) -> CorpusEntry:
        matches = tuple(
            item
            for item in self._entries.values()
            if item.seed_id == seed_id
            and supporting_execution_record_id
            in (*item.execution_record_ids, *item.supporting_root_record_ids)
        )
        if len(matches) != 1:
            raise ValueError("parent selection does not identify one corpus entry")
        return matches[0]

    def snapshot(self) -> V2CorpusSnapshot:
        return seal_contract(
            V2CorpusSnapshot,
            {
                "execution_supports": tuple(
                    sorted(self._execution_supports.values(), key=lambda item: item.seed_id)
                ),
                "materialized_candidates": tuple(
                    sorted(
                        self._candidates.values(),
                        key=lambda item: item.materialized_candidate_id,
                    )
                ),
                "execution_records": tuple(
                    sorted(
                        self._executions.values(),
                        key=lambda item: item.execution_record_id,
                    )
                ),
                "root_support_records": tuple(
                    sorted(
                        self._root_support_records.values(),
                        key=lambda item: item.support_record_id,
                    )
                ),
                "entries": tuple(
                    sorted(self._entries.values(), key=lambda item: item.corpus_entry_id)
                ),
            },
            "snapshot_digest",
        )

    @classmethod
    def from_snapshot(cls, snapshot: V2CorpusSnapshot) -> V2Corpus:
        corpus = cls()
        for support in snapshot.execution_supports:
            corpus.add_execution_support(support)
        for candidate in snapshot.materialized_candidates:
            corpus.add_candidate(candidate)
        for execution in snapshot.execution_records:
            corpus.add_execution(execution)
        for record in snapshot.root_support_records:
            corpus.add_root_support(record)
        for entry in snapshot.entries:
            corpus.add_entry(entry)
        if corpus.snapshot() != snapshot:
            raise ValueError("corpus snapshot did not round-trip")
        return corpus

    def _view(self, predicate) -> tuple[CorpusEntry, ...]:
        return tuple(
            sorted(
                (item for item in self._entries.values() if predicate(item)),
                key=lambda item: item.entry_digest,
            )
        )


def derived_seed_id(*, parent_seed_id: str, plan_digest: str, candidate_digest: str) -> str:
    return (
        "seed."
        + sha256_digest(
            {
                "parent_seed_id": parent_seed_id,
                "plan_digest": plan_digest,
                "candidate_digest": candidate_digest,
            }
        ).removeprefix("sha256:")[:24]
    )


def derive_executable_seed_support(
    *,
    parent: ExecutableSeedSupport,
    generated_content_by_payload_spec: dict[str, str],
    selected_operator_families: tuple[str, ...],
    plan_digest: str,
    candidate_digest: str,
    seed_id: str | None = None,
) -> ExecutableSeedSupport:
    unknown = set(generated_content_by_payload_spec) - {
        item.payload_spec_id for item in parent.payload_specs
    }
    if unknown:
        raise ValueError("derived seed content refers to an unknown payload spec")
    payload_specs = tuple(
        PayloadSpec(
            payload_spec_id=item.payload_spec_id,
            content=generated_content_by_payload_spec.get(item.payload_spec_id, item.content),
            carrier_kind=item.carrier_kind,
            field_path=item.field_path,
            placement_round=item.placement_round + 1,
            content_digest=sha256_digest(
                {
                    "content": generated_content_by_payload_spec.get(
                        item.payload_spec_id, item.content
                    )
                }
            ),
        )
        for item in parent.payload_specs
    )
    seed_id = seed_id or derived_seed_id(
        parent_seed_id=parent.seed_id,
        plan_digest=plan_digest,
        candidate_digest=candidate_digest,
    )
    return seal_contract(
        ExecutableSeedSupport,
        {
            "seed_id": seed_id,
            "payload_specs": payload_specs,
            "operator_history": (
                *parent.operator_history,
                *selected_operator_families,
            ),
            "parent_seed_id": parent.seed_id,
            "root_seed_id": parent.root_seed_id,
            "generation_depth": parent.generation_depth + 1,
        },
        "seed_content_digest",
    )


def record_parent_result(
    corpus: V2Corpus,
    *,
    parent_seed_id: str,
    supporting_execution_record_id: str,
    child_created: bool,
    productive: bool,
    cost_microunits: int,
) -> CorpusEntry:
    entry = corpus.parent_entry(
        seed_id=parent_seed_id,
        supporting_execution_record_id=supporting_execution_record_id,
    )
    statistics = entry.statistics.model_copy(
        update={
            "selection_count": entry.statistics.selection_count + 1,
            "child_count": entry.statistics.child_count + int(child_created),
            "productive_child_count": (entry.statistics.productive_child_count + int(productive)),
            "consecutive_no_gain": (0 if productive else entry.statistics.consecutive_no_gain + 1),
            "total_cost_microunits": (entry.statistics.total_cost_microunits + cost_microunits),
        }
    )
    payload = entry.model_dump(mode="python", exclude={"entry_digest"})
    payload["statistics"] = statistics
    updated = seal_contract(CorpusEntry, payload, "entry_digest")
    corpus.replace_entry(updated)
    return updated


def validate_parent_result_transition(
    *,
    before: V2CorpusSnapshot,
    after: V2CorpusSnapshot,
    parent_seed_id: str,
    supporting_execution_record_id: str,
    child_created: bool,
    productive: bool,
    cost_microunits: int,
) -> None:
    expected = V2Corpus.from_snapshot(before)
    record_parent_result(
        expected,
        parent_seed_id=parent_seed_id,
        supporting_execution_record_id=supporting_execution_record_id,
        child_created=child_created,
        productive=productive,
        cost_microunits=cost_microunits,
    )
    if expected.snapshot() != after:
        raise ValueError("corpus transition is not the expected parent result update")


def validate_episode_corpus_transition(
    *,
    before: V2CorpusSnapshot,
    after: V2CorpusSnapshot,
    parent_seed_id: str,
    supporting_execution_record_id: str,
    episode_execution_record_id: str,
    corpus_entry_id: str | None,
    cost_microunits: int,
) -> None:
    """Validate one guided Episode without admitting unpromoted candidates."""
    expected = V2Corpus.from_snapshot(before)
    if corpus_entry_id is not None:
        entries = {item.corpus_entry_id: item for item in after.entries}
        entry = entries.get(corpus_entry_id)
        if entry is None:
            raise ValueError("promoted CorpusEntry is missing from next snapshot")
        if entry.execution_record_ids != (episode_execution_record_id,):
            raise ValueError("promoted CorpusEntry does not use the Episode execution")
        executions = {item.execution_record_id: item for item in after.execution_records}
        execution = executions.get(episode_execution_record_id)
        if execution is None:
            raise ValueError("promoted Episode execution is missing from next snapshot")
        candidates = {
            item.materialized_candidate_id: item for item in after.materialized_candidates
        }
        candidate = candidates.get(execution.materialized_candidate_id)
        if candidate is None:
            raise ValueError("promoted Episode candidate is missing from next snapshot")
        seeds = {item.seed_id: item for item in after.execution_supports}
        seed = seeds.get(entry.seed_id)
        if seed is None:
            raise ValueError("promoted Episode seed is missing from next snapshot")
        if candidate.seed_id != seed.seed_id or execution.seed_id != seed.seed_id:
            raise ValueError("promoted Episode lineage does not close on one seed")
        expected.add_execution_support(seed)
        expected.add_candidate(candidate)
        expected.add_execution(execution)
        expected.add_entry(entry)
    record_parent_result(
        expected,
        parent_seed_id=parent_seed_id,
        supporting_execution_record_id=supporting_execution_record_id,
        child_created=corpus_entry_id is not None,
        productive=corpus_entry_id is not None,
        cost_microunits=cost_microunits,
    )
    if expected.snapshot() != after:
        raise ValueError("guided Episode changed Corpus beyond parent statistics and one promotion")


__all__ = [
    "ExecutableSeedSupport",
    "CorpusEntry",
    "CorpusEntryState",
    "CorpusStatistics",
    "DeliveredPayload",
    "ExecutionCosts",
    "ExecutionRecord",
    "FrozenRootSupportRecord",
    "MaterializedCandidate",
    "PayloadExecutionRef",
    "PayloadSpec",
    "SeedKind",
    "V2Corpus",
    "V2CorpusSnapshot",
    "derive_executable_seed_support",
    "derived_seed_id",
    "record_parent_result",
    "validate_parent_result_transition",
    "validate_episode_corpus_transition",
    "seal_contract",
]
