"""Office V2 seed, execution, and corpus contracts.

The four persistent objects deliberately answer different questions:
AttackSeed is a planned recipe, MaterializedCandidate is delivered input,
ExecutionRecord is observed execution, and CorpusEntry is scheduling state.
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


class CarrierRecipe(OfficeV2Contract):
    entry_kind: Identifier
    carrier_kind: Identifier
    required_field_paths: tuple[str, ...] = Field(min_length=1)

    @field_validator("required_field_paths")
    @classmethod
    def fields_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("carrier recipe fields must be unique")
        return tuple(sorted(value))


class OriginIntent(OfficeV2Contract):
    objective_id: Identifier
    milestone_id: Identifier


class BindingRequirements(OfficeV2Contract):
    actor_roles: tuple[Identifier, ...] = Field(default_factory=tuple)
    task_blueprint_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    resource_kinds: tuple[Identifier, ...] = Field(default_factory=tuple)
    authorization_branches: tuple[Identifier, ...] = Field(default_factory=tuple)

    @field_validator(
        "actor_roles", "task_blueprint_ids", "resource_kinds", "authorization_branches"
    )
    @classmethod
    def values_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("binding requirements must be unique")
        return tuple(sorted(value))


class AttackSeed(OfficeV2Contract):
    seed_id: Identifier
    payload_specs: tuple[PayloadSpec, ...] = Field(min_length=1)
    carrier_recipe: CarrierRecipe
    origin_intent: OriginIntent | None = None
    binding_requirements: BindingRequirements
    operator_history: tuple[Identifier, ...] = Field(default_factory=tuple)
    parent_seed_id: Identifier | None = None
    root_seed_id: Identifier
    generation_depth: int = Field(ge=0)
    seed_content_digest: Sha256Digest

    @field_validator("payload_specs")
    @classmethod
    def payloads_are_canonical(
        cls, value: tuple[PayloadSpec, ...]
    ) -> tuple[PayloadSpec, ...]:
        ids = tuple(item.payload_spec_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("attack seed payload ids must be unique")
        return tuple(sorted(value, key=lambda item: item.payload_spec_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"seed_content_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def lineage_and_digest_match(self) -> Self:
        if self.generation_depth == 0:
            if self.parent_seed_id is not None or self.root_seed_id != self.seed_id:
                raise ValueError("root attack seed lineage does not close")
        elif self.parent_seed_id is None:
            raise ValueError("derived attack seed requires parent_seed_id")
        if self.seed_content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("attack seed digest does not match")
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
        return self.model_dump(
            mode="json", exclude={"materialization_digest"}, exclude_none=False
        )

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
    episode_digest: Sha256Digest
    manifest_digest: Sha256Digest
    oracle_fact_digest: Sha256Digest
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
    execution_record_ids: tuple[Identifier, ...] = Field(min_length=1)
    risk_contribution_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    behavior_contribution_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    frontier_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    carrier_kinds: tuple[Identifier, ...] = Field(default_factory=tuple)
    compatibility_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    state: CorpusEntryState = CorpusEntryState.ACTIVE
    statistics: CorpusStatistics = Field(default_factory=CorpusStatistics)
    entry_digest: Sha256Digest

    @field_validator(
        "promotion_reasons",
        "execution_record_ids",
        "risk_contribution_keys",
        "behavior_contribution_keys",
        "frontier_ids",
        "carrier_kinds",
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
        if self.entry_digest != sha256_digest(self.digest_payload()):
            raise ValueError("corpus entry digest does not match")
        return self


class V2CorpusSnapshot(OfficeV2Contract):
    seeds: tuple[AttackSeed, ...] = Field(default_factory=tuple)
    materialized_candidates: tuple[MaterializedCandidate, ...] = Field(
        default_factory=tuple
    )
    execution_records: tuple[ExecutionRecord, ...] = Field(default_factory=tuple)
    entries: tuple[CorpusEntry, ...] = Field(default_factory=tuple)
    snapshot_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"snapshot_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def lineage_and_digest_match(self) -> Self:
        for values, label, identity in (
            (self.seeds, "seed", lambda item: item.seed_id),
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
            (self.entries, "corpus entry", lambda item: item.corpus_entry_id),
        ):
            ids = tuple(identity(item) for item in values)
            if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
                raise ValueError(f"corpus snapshot {label} values must be canonical")
        seed_ids = {item.seed_id for item in self.seeds}
        candidate_by_id = {
            item.materialized_candidate_id: item for item in self.materialized_candidates
        }
        execution_by_id = {
            item.execution_record_id: item for item in self.execution_records
        }
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
            or any(record_id not in execution_by_id for record_id in item.execution_record_ids)
            for item in self.entries
        ):
            raise ValueError("corpus snapshot entry lineage does not close")
        if self.snapshot_digest != sha256_digest(self.digest_payload()):
            raise ValueError("corpus snapshot digest does not match")
        return self


def seal_contract(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    digest_payload = draft.digest_payload()
    return model_type(**payload, **{digest_field: sha256_digest(digest_payload)})


class V2Corpus:
    """A single physical corpus with deterministic logical views."""

    def __init__(self) -> None:
        self._seeds: dict[str, AttackSeed] = {}
        self._candidates: dict[str, MaterializedCandidate] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._entries: dict[str, CorpusEntry] = {}

    def add_seed(self, seed: AttackSeed) -> None:
        self._insert_immutable(self._seeds, seed.seed_id, seed, "seed")

    def add_candidate(self, candidate: MaterializedCandidate) -> None:
        if candidate.seed_id not in self._seeds:
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
        self._insert_immutable(
            self._executions, record.execution_record_id, record, "execution"
        )

    def add_entry(self, entry: CorpusEntry) -> None:
        if entry.seed_id not in self._seeds:
            raise ValueError("corpus entry refers to unknown seed")
        if any(item not in self._executions for item in entry.execution_record_ids):
            raise ValueError("corpus entry refers to unknown execution")
        self._insert_immutable(self._entries, entry.corpus_entry_id, entry, "entry")

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

    def carrier_view(self, carrier_kind: str) -> tuple[CorpusEntry, ...]:
        return self._view(lambda item: carrier_kind in item.carrier_kinds)

    def compatibility_view(self, digest: str) -> tuple[CorpusEntry, ...]:
        return self._view(lambda item: digest in item.compatibility_digests)

    def lineage_view(self, root_seed_id: str) -> tuple[AttackSeed, ...]:
        return tuple(
            sorted(
                (item for item in self._seeds.values() if item.root_seed_id == root_seed_id),
                key=lambda item: (item.generation_depth, item.seed_content_digest),
            )
        )

    def supporting_executions(self, entry_id: str) -> tuple[ExecutionRecord, ...]:
        entry = self._entries[entry_id]
        return tuple(self._executions[item] for item in entry.execution_record_ids)

    def snapshot(self) -> V2CorpusSnapshot:
        return seal_contract(
            V2CorpusSnapshot,
            {
                "seeds": tuple(sorted(self._seeds.values(), key=lambda item: item.seed_id)),
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
                "entries": tuple(
                    sorted(self._entries.values(), key=lambda item: item.corpus_entry_id)
                ),
            },
            "snapshot_digest",
        )

    @classmethod
    def from_snapshot(cls, snapshot: V2CorpusSnapshot) -> V2Corpus:
        corpus = cls()
        for seed in snapshot.seeds:
            corpus.add_seed(seed)
        for candidate in snapshot.materialized_candidates:
            corpus.add_candidate(candidate)
        for execution in snapshot.execution_records:
            corpus.add_execution(execution)
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


__all__ = [
    "AttackSeed",
    "BindingRequirements",
    "CarrierRecipe",
    "CorpusEntry",
    "CorpusEntryState",
    "CorpusStatistics",
    "DeliveredPayload",
    "ExecutionCosts",
    "ExecutionRecord",
    "MaterializedCandidate",
    "OriginIntent",
    "PayloadExecutionRef",
    "PayloadSpec",
    "SeedKind",
    "V2Corpus",
    "V2CorpusSnapshot",
    "seal_contract",
]
