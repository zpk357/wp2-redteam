"""Trusted Office V2 execution facts prepared for coverage extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import Field, TypeAdapter, field_validator, model_validator

from sandbox.protocol import AgentRuntimeKind
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.replay.manifest import verify_manifest
from sandbox.replay.models import ReplayManifest, ReplayResult, ReplayStatus
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.oracle_evidence import (
    EpisodeTimelineEntry,
    InteractionEvidenceFact,
    OracleEvidenceBundle,
    OracleInputIdentity,
    TimelineEntryKind,
    ToolEvidenceExchange,
)
from sandbox.scenarios.office_v2.oracle_models import (
    CompleteScenarioOracleResult,
    EvidenceRef,
    SecurityFactSet,
    TaskInputEvidenceRef,
    TerminationEvidenceRef,
    UtilityResult,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    OfficeToolResult,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld, StateTransitionRecord


class V2CoverageInputError(ValueError):
    """The frozen execution closure cannot safely produce coverage facts."""


_PRODUCER_RUNTIME_FIELDS = (
    "producer_runtime_kind",
    "producer_runtime_version",
    "producer_runtime_composition_digest",
)


class V2AcquisitionKind(StrEnum):
    DIRECT = "direct"
    RECORDING = "recording"
    STRICT_REPLAY = "strict_replay"


class LiveOracleArtifact(Protocol):
    execution_id: str
    artifact_digest: str
    evidence_bundle: OracleEvidenceBundle
    oracle_result: CompleteScenarioOracleResult


@dataclass(frozen=True, slots=True)
class _RecordingStateIdentity:
    episode_id: str
    initial_state_digest: str
    final_state_digest: str


@dataclass(frozen=True, slots=True)
class _VerifiedOracleArtifact:
    execution_id: str
    evidence_bundle: OracleEvidenceBundle
    oracle_result: CompleteScenarioOracleResult


_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_DIGEST_ADAPTER = TypeAdapter(Sha256Digest)


class V2AcquisitionMetadata(OfficeV2Contract):
    source_kind: V2AcquisitionKind
    execution_id: Identifier
    source_digest: Sha256Digest
    evidence_complete: bool
    container_removed: bool
    manifest_digest: Sha256Digest | None = None
    source_oracle_artifact_digest: Sha256Digest | None = None
    replay_run_id: Identifier | None = None
    parent_replay_id: Identifier | None = None
    metadata_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"metadata_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def digest_and_completion_match(self) -> V2AcquisitionMetadata:
        if self.metadata_digest != sha256_digest(self.digest_payload()):
            raise ValueError("V2 acquisition metadata digest does not match")
        if not self.evidence_complete:
            raise ValueError("V2 coverage requires complete execution evidence")
        if not self.container_removed:
            raise ValueError("V2 coverage requires confirmed container cleanup")
        if self.source_kind is V2AcquisitionKind.DIRECT and self.manifest_digest is not None:
            raise ValueError("direct acquisition cannot claim a replay manifest")
        if self.source_kind is V2AcquisitionKind.STRICT_REPLAY and self.replay_run_id is None:
            raise ValueError("strict replay acquisition requires replay_run_id")
        return self


class V2BehaviorSourceFacts(OfficeV2Contract):
    identity: OracleInputIdentity
    task_ref: TaskInputEvidenceRef
    frozen_binding_evidence_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    initialization_materialization_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    final_state_digest: Sha256Digest
    tool_exchanges: tuple[ToolEvidenceExchange, ...] = Field(default_factory=tuple)
    interaction_facts: tuple[InteractionEvidenceFact, ...] = Field(default_factory=tuple)
    timeline: tuple[EpisodeTimelineEntry, ...] = Field(default_factory=tuple)
    agent_transition_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    termination_reason: Identifier
    submitted: bool
    termination_ref: TerminationEvidenceRef
    termination_digest: Sha256Digest
    behavior_source_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"behavior_source_digest"},
            exclude_none=False,
        )

    @field_validator("agent_transition_digests")
    @classmethod
    def transition_digests_keep_timeline_order(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Agent transition digests must be unique")
        return value

    @field_validator("frozen_binding_evidence_ids")
    @classmethod
    def frozen_binding_evidence_is_canonical(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)) or value != tuple(sorted(value)):
            raise ValueError("frozen binding evidence ids must be unique and sorted")
        return value

    @model_validator(mode="after")
    def state_identity_and_digest_match(self) -> V2BehaviorSourceFacts:
        if (
            self.initial_state_digest != self.identity.initial_state_digest
            or self.final_state_digest != self.identity.final_state_digest
        ):
            raise ValueError("behavior state digests do not match input identity")
        if not self.submitted or self.termination_reason != "submit":
            raise ValueError("coverage requires an explicit submit termination")
        if self.behavior_source_digest != sha256_digest(self.digest_payload()):
            raise ValueError("behavior source digest does not match facts")
        return self


class V2OracleFacts(OfficeV2Contract):
    oracle_contract_version: Identifier
    scenario_case_id: Identifier
    scenario_case_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    final_state_digest: Sha256Digest
    utility: UtilityResult
    security: SecurityFactSet
    evidence_closure: tuple[EvidenceRef, ...] = Field(min_length=1)
    planned_objective_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    unexpected_violation_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    oracle_fact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"oracle_fact_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def classifications_and_digest_match(self) -> V2OracleFacts:
        planned = tuple(item.objective_id for item in self.security.planned_objectives)
        unexpected = tuple(
            item.violation_id for item in self.security.violations if not item.planned
        )
        if self.planned_objective_ids != planned:
            raise ValueError("planned objective ids do not match Oracle facts")
        if self.unexpected_violation_ids != unexpected:
            raise ValueError("unexpected violation ids do not match Oracle facts")
        if self.oracle_fact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Oracle fact digest does not match facts")
        return self


class V2CoverageInput(OfficeV2Contract):
    acquisition: V2AcquisitionMetadata
    evidence_bundle_digest: Sha256Digest
    behavior_source_facts: V2BehaviorSourceFacts
    oracle_facts: V2OracleFacts
    episode_valid: bool
    canonical_fact_digest: Sha256Digest
    input_digest: Sha256Digest

    def canonical_fact_payload(self) -> dict[str, object]:
        return {
            "identity": self.behavior_source_facts.identity,
            "behavior_source_digest": self.behavior_source_facts.behavior_source_digest,
            "oracle_fact_digest": self.oracle_facts.oracle_fact_digest,
        }

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"input_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def closure_is_valid_and_canonical(self) -> V2CoverageInput:
        identity = self.behavior_source_facts.identity
        if not self.episode_valid:
            raise ValueError("invalid Episode cannot enter V2 coverage")
        if (
            self.oracle_facts.scenario_case_id != identity.scenario_case_id
            or self.oracle_facts.scenario_case_digest != identity.scenario_case_digest
            or self.oracle_facts.initial_state_digest != identity.initial_state_digest
            or self.oracle_facts.final_state_digest != identity.final_state_digest
        ):
            raise ValueError("behavior and Oracle fact identities disagree")
        if self.canonical_fact_digest != sha256_digest(self.canonical_fact_payload()):
            raise ValueError("canonical V2 coverage fact digest does not match")
        if self.input_digest != sha256_digest(self.digest_payload()):
            raise ValueError("V2 coverage input digest does not match")
        return self


def _sealed_acquisition(**payload: object) -> V2AcquisitionMetadata:
    draft = V2AcquisitionMetadata.model_construct(
        **payload,
        metadata_digest="sha256:" + "0" * 64,
    )
    try:
        return V2AcquisitionMetadata(
            **payload,
            metadata_digest=sha256_digest(draft.digest_payload()),
        )
    except ValueError as exc:
        raise V2CoverageInputError("invalid V2 acquisition metadata") from exc


def _agent_transition_digests(bundle: OracleEvidenceBundle) -> tuple[str, ...]:
    digests: list[str] = []
    for entry in bundle.timeline:
        if entry.entry_kind is TimelineEntryKind.TOOL:
            exchange = bundle.tool_exchanges[entry.item_sequence]
            if exchange.state_transition is not None:
                digests.append(exchange.state_transition.transition_digest)
        else:
            interaction = bundle.interaction_facts[entry.item_sequence]
            if interaction.transition_ref is not None:
                digests.append(interaction.transition_ref.evidence_digest)
    return tuple(digests)


def _behavior_facts(bundle: OracleEvidenceBundle) -> V2BehaviorSourceFacts:
    payload = {
        "identity": bundle.identity,
        "task_ref": bundle.task_ref,
        "frozen_binding_evidence_ids": bundle.frozen_binding_evidence_ids,
        "initialization_materialization_digest": bundle.materialization_ref.evidence_digest,
        "initial_state_digest": bundle.identity.initial_state_digest,
        "final_state_digest": bundle.identity.final_state_digest,
        "tool_exchanges": bundle.tool_exchanges,
        "interaction_facts": bundle.interaction_facts,
        "timeline": bundle.timeline,
        "agent_transition_digests": _agent_transition_digests(bundle),
        "termination_reason": bundle.termination.reason,
        "submitted": bundle.termination.submitted,
        "termination_ref": bundle.termination.evidence_ref(),
        "termination_digest": bundle.termination.fact_digest,
    }
    draft = V2BehaviorSourceFacts.model_construct(
        **payload,
        behavior_source_digest="sha256:" + "0" * 64,
    )
    return V2BehaviorSourceFacts(
        **payload,
        behavior_source_digest=sha256_digest(draft.digest_payload()),
    )


def _oracle_facts(result: CompleteScenarioOracleResult) -> V2OracleFacts:
    payload = {
        "oracle_contract_version": result.oracle_contract_version,
        "scenario_case_id": result.scenario_case_id,
        "scenario_case_digest": result.scenario_case_digest,
        "initial_state_digest": result.initial_state_digest,
        "final_state_digest": result.final_state_digest,
        "utility": result.utility,
        "security": result.security,
        "evidence_closure": result.evidence_closure,
        "planned_objective_ids": tuple(
            item.objective_id for item in result.security.planned_objectives
        ),
        "unexpected_violation_ids": tuple(
            item.violation_id for item in result.security.violations if not item.planned
        ),
    }
    draft = V2OracleFacts.model_construct(
        **payload,
        oracle_fact_digest="sha256:" + "0" * 64,
    )
    return V2OracleFacts(
        **payload,
        oracle_fact_digest=sha256_digest(draft.digest_payload()),
    )


def _build_v2_coverage_input(
    *,
    bundle: OracleEvidenceBundle,
    result: CompleteScenarioOracleResult,
    acquisition: V2AcquisitionMetadata,
) -> V2CoverageInput:
    try:
        trusted_bundle = OracleEvidenceBundle.model_validate(
            bundle.model_dump(mode="python", exclude_none=False)
        )
        trusted_result = CompleteScenarioOracleResult.model_validate(
            result.model_dump(mode="python", exclude_none=False)
        )
    except ValueError as exc:
        raise V2CoverageInputError("invalid V2 execution closure") from exc
    if trusted_result.input_bundle_digest != trusted_bundle.bundle_digest:
        raise V2CoverageInputError("Oracle result does not close over evidence bundle")

    behavior = _behavior_facts(trusted_bundle)
    oracle = _oracle_facts(trusted_result)
    canonical_payload = {
        "identity": behavior.identity,
        "behavior_source_digest": behavior.behavior_source_digest,
        "oracle_fact_digest": oracle.oracle_fact_digest,
    }
    payload = {
        "acquisition": acquisition,
        "evidence_bundle_digest": trusted_bundle.bundle_digest,
        "behavior_source_facts": behavior,
        "oracle_facts": oracle,
        "episode_valid": True,
        "canonical_fact_digest": sha256_digest(canonical_payload),
    }
    draft = V2CoverageInput.model_construct(
        **payload,
        input_digest="sha256:" + "0" * 64,
    )
    return V2CoverageInput(
        **payload,
        input_digest=sha256_digest(draft.digest_payload()),
    )


def v2_coverage_input_from_direct(
    artifact: LiveOracleArtifact,
    *,
    container_removed: bool,
) -> V2CoverageInput:
    acquisition = _sealed_acquisition(
        source_kind=V2AcquisitionKind.DIRECT,
        execution_id=artifact.execution_id,
        source_digest=artifact.artifact_digest,
        evidence_complete=True,
        container_removed=container_removed,
        manifest_digest=None,
        source_oracle_artifact_digest=artifact.artifact_digest,
        replay_run_id=None,
        parent_replay_id=None,
    )
    return _build_v2_coverage_input(
        bundle=artifact.evidence_bundle,
        result=artifact.oracle_result,
        acquisition=acquisition,
    )


def _verified_v2_manifest(manifest: ReplayManifest) -> ReplayManifest:
    try:
        trusted = ReplayManifest.model_validate(
            manifest.model_dump(mode="python", exclude_none=False)
        )
        verify_manifest(trusted)
    except ValueError as exc:
        raise V2CoverageInputError("invalid or unsealed replay manifest") from exc
    if not trusted.recording_complete:
        raise V2CoverageInputError("incomplete recording cannot enter coverage")
    if trusted.office_v2_oracle is None or trusted.office_v2_recording_state is None:
        raise V2CoverageInputError("manifest lacks trusted Office V2 artifacts")
    return trusted


def verify_v2_recording_runtime_identity(
    manifest: ReplayManifest,
    *,
    determinism_config_payload: bytes,
    expected_runtime_kind: AgentRuntimeKind | str,
    expected_runtime_version: str,
    expected_runtime_composition_digest: str,
) -> dict[str, str]:
    """Bind recording producer facts to the Campaign-selected Runtime."""

    trusted = _verified_v2_manifest(manifest)
    reference = trusted.determinism_config
    if (
        len(determinism_config_payload) != reference.size_bytes
        or sha256_bytes(determinism_config_payload) != reference.sha256
    ):
        raise V2CoverageInputError(
            "recording determinism config does not match manifest"
        )
    try:
        raw = json.loads(determinism_config_payload)
        if (
            not isinstance(raw, dict)
            or canonical_json_bytes(raw) != determinism_config_payload
        ):
            raise ValueError("determinism config is not canonical JSON")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise V2CoverageInputError("invalid recording determinism config") from exc
    if trusted.determinism_config_digest != sha256_digest(raw):
        raise V2CoverageInputError(
            "recording determinism config digest does not match"
        )

    try:
        runtime_kind = AgentRuntimeKind(expected_runtime_kind).value
    except ValueError as exc:
        raise V2CoverageInputError(
            "Campaign selected an unknown producer Runtime"
        ) from exc
    expected = {
        "producer_runtime_kind": runtime_kind,
        "producer_runtime_version": expected_runtime_version,
        "producer_runtime_composition_digest": (
            expected_runtime_composition_digest
        ),
    }
    if not expected_runtime_version:
        raise V2CoverageInputError("Campaign producer Runtime version is empty")
    if (
        not expected_runtime_composition_digest.startswith("sha256:")
        or len(expected_runtime_composition_digest) != 71
    ):
        raise V2CoverageInputError(
            "Campaign producer Runtime composition digest is invalid"
        )
    recorded = {field: raw.get(field) for field in _PRODUCER_RUNTIME_FIELDS}
    manifested = {
        field: trusted.metadata.get(field) for field in _PRODUCER_RUNTIME_FIELDS
    }
    if recorded != expected or manifested != expected:
        raise V2CoverageInputError(
            "recording producer Runtime identity differs from Campaign"
        )
    return expected


def _verified_oracle_artifact(
    manifest: ReplayManifest,
    payload: bytes,
) -> _VerifiedOracleArtifact:
    reference = manifest.office_v2_oracle
    if reference is None:
        raise V2CoverageInputError("manifest lacks trusted Office V2 Oracle facts")
    if len(payload) != reference.size_bytes or sha256_bytes(payload) != reference.sha256:
        raise V2CoverageInputError("Office V2 Oracle artifact does not match manifest")
    try:
        raw = json.loads(payload)
        expected_keys = {
            "schema_version",
            "artifact_version",
            "execution_id",
            "trace_digest",
            "trusted_facts_digest",
            "evidence_bundle",
            "oracle_result",
            "artifact_digest",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != expected_keys
            or canonical_json_bytes(raw) != payload
        ):
            raise ValueError("Oracle artifact is not canonical")
        if (
            raw.get("schema_version") != "office-v2.0"
            or raw.get("artifact_version") != "office-v2-live-oracle-artifact-v1"
        ):
            raise ValueError("Oracle artifact version is not supported")
        artifact_digest = raw.get("artifact_digest")
        artifact_payload = {
            key: value for key, value in raw.items() if key != "artifact_digest"
        }
        if artifact_digest != sha256_digest(artifact_payload):
            raise ValueError("Oracle artifact digest does not match payload")
        execution_id = _IDENTIFIER_ADAPTER.validate_python(raw.get("execution_id"))
        _DIGEST_ADAPTER.validate_python(raw.get("trace_digest"))
        _DIGEST_ADAPTER.validate_python(raw.get("trusted_facts_digest"))
        bundle = OracleEvidenceBundle.model_validate(raw.get("evidence_bundle"))
        result = CompleteScenarioOracleResult.model_validate(raw.get("oracle_result"))
        if result.input_bundle_digest != bundle.bundle_digest:
            raise ValueError("Oracle result does not close over evidence bundle")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise V2CoverageInputError("invalid Office V2 Oracle artifact") from exc
    return _VerifiedOracleArtifact(
        execution_id=execution_id,
        evidence_bundle=bundle,
        oracle_result=result,
    )


def _ordered_transition_digests(bundle: OracleEvidenceBundle) -> tuple[str, ...]:
    digests: list[str] = []
    for entry in bundle.timeline:
        digest = None
        if entry.entry_kind is TimelineEntryKind.TOOL:
            transition = bundle.tool_exchanges[entry.item_sequence].state_transition
            if transition is not None:
                digest = transition.transition_digest
        else:
            transition_ref = bundle.interaction_facts[entry.item_sequence].transition_ref
            if transition_ref is not None:
                digest = transition_ref.evidence_digest
        if digest is not None and digest not in digests:
            digests.append(digest)
    return tuple(digests)


def _verified_recording_state_identity(
    manifest: ReplayManifest,
    payload: bytes,
    bundle: OracleEvidenceBundle,
) -> _RecordingStateIdentity:
    reference = manifest.office_v2_recording_state
    if reference is None:
        raise V2CoverageInputError("manifest lacks trusted Office V2 recording state")
    if len(payload) != reference.size_bytes or sha256_bytes(payload) != reference.sha256:
        raise V2CoverageInputError("Office V2 recording state artifact does not match manifest")
    try:
        raw = json.loads(payload)
        expected_keys = {
            "schema_version",
            "recording_state_version",
            "session",
            "tool_invocations",
            "tool_results",
            "interaction_events",
            "pending_clarification_request_ids",
            "recording_state_digest",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != expected_keys
            or canonical_json_bytes(raw) != payload
        ):
            raise ValueError("recording state is not canonical JSON")
        if (
            raw.get("schema_version") != "office-v2.0"
            or raw.get("recording_state_version") != "office-v2-recording-state-v1"
        ):
            raise ValueError("recording state version is not supported")
        recording_digest = raw.get("recording_state_digest")
        recording_payload = {
            key: value for key, value in raw.items() if key != "recording_state_digest"
        }
        if recording_digest != sha256_digest(recording_payload):
            raise ValueError("recording state digest does not match payload")

        session = raw.get("session")
        if not isinstance(session, dict):
            raise ValueError("recording state session is missing")
        if session.get("snapshot_version") != "office-v2-session-snapshot-v1":
            raise ValueError("recording session version is not supported")
        snapshot_digest = session.get("snapshot_digest")
        snapshot_payload = {
            key: value for key, value in session.items() if key != "snapshot_digest"
        }
        if snapshot_digest != sha256_digest(snapshot_payload):
            raise ValueError("recording session digest does not match payload")

        state = OfficeWorldState.model_validate(session.get("state"))
        history_raw = session.get("history")
        if not isinstance(history_raw, list):
            raise ValueError("recording session history is invalid")
        history = tuple(StateTransitionRecord.model_validate(item) for item in history_raw)
        episode = EpisodeWorld.restore(
            episode_id=session.get("episode_id"),
            base_world_digest=session.get("base_world_digest"),
            state=state,
            history=history,
            initial_state_digest=session.get("initial_state_digest"),
        )
        if episode.state_digest != session.get("state_digest"):
            raise ValueError("recording session final state does not match payload")

        invocations_raw = raw.get("tool_invocations")
        results_raw = raw.get("tool_results")
        interactions_raw = raw.get("interaction_events")
        if (
            not isinstance(invocations_raw, list)
            or not isinstance(results_raw, list)
            or not isinstance(interactions_raw, list)
        ):
            raise ValueError("recording trusted facts are invalid")
        invocations = tuple(
            OfficeToolInvocation.model_validate(item) for item in invocations_raw
        )
        results = tuple(OfficeToolResult.model_validate(item) for item in results_raw)
        exchanges = bundle.tool_exchanges
        if len(invocations) != len(results) or len(invocations) != len(exchanges):
            raise ValueError("recording tool fact counts do not match Oracle")
        for invocation, result, exchange in zip(
            invocations,
            results,
            exchanges,
            strict=True,
        ):
            if (
                invocation.canonical_digest()
                != exchange.invocation_ref.evidence_digest
                or result.execution_fact_digest != exchange.result_ref.evidence_digest
                or invocation.invocation_id != exchange.invocation_ref.invocation_id
                or result.invocation_id != exchange.result_ref.invocation_id
            ):
                raise ValueError("recording tool facts do not match Oracle")

        if len(interactions_raw) != len(bundle.interaction_facts):
            raise ValueError("recording interaction fact counts do not match Oracle")
        interaction_keys = {
            "schema_version",
            "event_type",
            "data",
            "logical_time",
            "input_digest",
            "output_digest",
            "state_digest",
        }
        for event, fact in zip(interactions_raw, bundle.interaction_facts, strict=True):
            if (
                not isinstance(event, dict)
                or set(event) != interaction_keys
                or event.get("schema_version") != "office-v2.0"
                or not isinstance(event.get("data"), dict)
                or event.get("event_type") != fact.event_kind.value
                or event.get("logical_time") != fact.logical_time
                or event.get("input_digest") != fact.input_digest
                or event.get("output_digest") != fact.output_digest
                or event.get("state_digest") != fact.state_digest
                or sha256_digest(event["data"]) != fact.data_digest
            ):
                raise ValueError("recording interaction facts do not match Oracle")

        if tuple(item.transition_digest for item in history) != _ordered_transition_digests(
            bundle
        ):
            raise ValueError("recording state history does not match Oracle")
        if session.get("base_world_digest") != bundle.identity.world_digest:
            raise ValueError("recording base world does not match Oracle")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise V2CoverageInputError("invalid Office V2 recording state artifact") from exc
    return _RecordingStateIdentity(
        episode_id=episode.episode_id,
        initial_state_digest=session["initial_state_digest"],
        final_state_digest=episode.state_digest,
    )


def v2_coverage_input_from_recording(
    manifest: ReplayManifest,
    *,
    oracle_artifact_payload: bytes,
    recording_state_payload: bytes,
    container_removed: bool,
) -> V2CoverageInput:
    trusted_manifest = _verified_v2_manifest(manifest)
    artifact = _verified_oracle_artifact(trusted_manifest, oracle_artifact_payload)
    recording_identity = _verified_recording_state_identity(
        trusted_manifest,
        recording_state_payload,
        artifact.evidence_bundle,
    )
    identity = artifact.evidence_bundle.identity
    if (
        trusted_manifest.case_id != identity.scenario_case_id
        or recording_identity.episode_id != artifact.execution_id
        or recording_identity.initial_state_digest != identity.initial_state_digest
        or recording_identity.final_state_digest != identity.final_state_digest
    ):
        raise V2CoverageInputError("recording manifest identity does not match V2 facts")
    acquisition = _sealed_acquisition(
        source_kind=V2AcquisitionKind.RECORDING,
        execution_id=artifact.execution_id,
        source_digest=trusted_manifest.manifest_digest,
        evidence_complete=trusted_manifest.recording_complete,
        container_removed=container_removed,
        manifest_digest=trusted_manifest.manifest_digest,
        source_oracle_artifact_digest=trusted_manifest.office_v2_oracle.sha256,
        replay_run_id=None,
        parent_replay_id=trusted_manifest.parent_replay_id,
    )
    return _build_v2_coverage_input(
        bundle=artifact.evidence_bundle,
        result=artifact.oracle_result,
        acquisition=acquisition,
    )


def v2_coverage_input_from_strict_replay(
    source_manifest: ReplayManifest,
    replay_result: ReplayResult,
    *,
    source_oracle_artifact_payload: bytes,
    source_recording_state_payload: bytes,
) -> V2CoverageInput:
    manifest = _verified_v2_manifest(source_manifest)
    source_artifact = _verified_oracle_artifact(
        manifest,
        source_oracle_artifact_payload,
    )
    recording_identity = _verified_recording_state_identity(
        manifest,
        source_recording_state_payload,
        source_artifact.evidence_bundle,
    )
    identity = source_artifact.evidence_bundle.identity
    if (
        manifest.case_id != identity.scenario_case_id
        or recording_identity.episode_id != source_artifact.execution_id
        or recording_identity.initial_state_digest != identity.initial_state_digest
        or recording_identity.final_state_digest != identity.final_state_digest
        or replay_result.source_replay_id != manifest.replay_id
        or (
            replay_result.source_trajectory_id is not None
            and replay_result.source_trajectory_id != manifest.trajectory_id
        )
    ):
        raise V2CoverageInputError("strict replay lineage does not match V2 facts")
    if replay_result.status is not ReplayStatus.MATCHED:
        raise V2CoverageInputError("strict replay must be matched")
    if not replay_result.container_removed:
        raise V2CoverageInputError("strict replay container cleanup is not confirmed")
    if (
        replay_result.source_behavior_digest is None
        or replay_result.replay_behavior_digest is None
        or replay_result.source_behavior_digest != replay_result.replay_behavior_digest
        or replay_result.source_behavior_digest != manifest.normalized_behavior_trace_digest
    ):
        raise V2CoverageInputError("strict replay behavior digest does not match")
    final_digest = identity.final_state_digest
    if (
        replay_result.source_final_state_digest is None
        or replay_result.replay_final_state_digest is None
        or replay_result.source_final_state_digest != replay_result.replay_final_state_digest
        or replay_result.replay_final_state_digest != final_digest
    ):
        raise V2CoverageInputError("strict replay final state digest does not match")
    acquisition = _sealed_acquisition(
        source_kind=V2AcquisitionKind.STRICT_REPLAY,
        execution_id=replay_result.replay_run_id,
        source_digest=sha256_digest(
            replay_result.model_dump(mode="json", exclude_none=False)
        ),
        evidence_complete=manifest.recording_complete,
        container_removed=replay_result.container_removed,
        manifest_digest=manifest.manifest_digest,
        source_oracle_artifact_digest=manifest.office_v2_oracle.sha256,
        replay_run_id=replay_result.replay_run_id,
        parent_replay_id=manifest.replay_id,
    )
    return _build_v2_coverage_input(
        bundle=source_artifact.evidence_bundle,
        result=source_artifact.oracle_result,
        acquisition=acquisition,
    )


__all__ = [
    "V2AcquisitionKind",
    "V2AcquisitionMetadata",
    "V2BehaviorSourceFacts",
    "V2CoverageInput",
    "V2CoverageInputError",
    "V2OracleFacts",
    "verify_v2_recording_runtime_identity",
    "v2_coverage_input_from_direct",
    "v2_coverage_input_from_recording",
    "v2_coverage_input_from_strict_replay",
]
