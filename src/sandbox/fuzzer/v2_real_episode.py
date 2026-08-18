"""Official Docker Agent adapter for one materialized Office V2 generation."""

from __future__ import annotations

import time
from typing import Literal, Self

from pydantic import model_validator

from sandbox.coverage.v2_input import (
    V2CoverageInput,
    v2_coverage_input_from_recording,
)
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import (
    RECORDED_MODEL_TOKEN_USAGE_KEY,
    RecordedModelDecision,
    ReplayManifest,
)
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.cli_entry import (
    OfficeV2PublicCase,
    build_office_v2_public_request,
    office_v2_public_cases,
)
from sandbox.scenarios.office_v2.fork import (
    infer_office_v2_compatibility_purpose,
    rematerialize_office_v2_scenario_text,
)
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
)
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceBundle
from sandbox.scenarios.office_v2.oracle_models import CompleteScenarioOracleResult


class OfficeV2RecordedOracleArtifact(OfficeV2Contract):
    artifact_version: Literal["office-v2-live-oracle-artifact-v1"]
    execution_id: Identifier
    trace_digest: Sha256Digest
    trusted_facts_digest: Sha256Digest
    evidence_bundle: OracleEvidenceBundle
    oracle_result: CompleteScenarioOracleResult
    artifact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"artifact_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if self.oracle_result.input_bundle_digest != self.evidence_bundle.bundle_digest:
            raise ValueError("recorded Oracle result does not match evidence bundle")
        if self.artifact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("recorded Oracle artifact digest does not match")
        return self


class OfficeV2RealEpisodeResult(OfficeV2Contract):
    scenario_case: MaterializedScenarioCase
    manifest: ReplayManifest
    oracle: OfficeV2RecordedOracleArtifact
    coverage_input: V2CoverageInput
    agent_tokens: int
    elapsed_ms: int


class DockerOfficeV2EpisodeRunner:
    """Rematerialize one accepted payload and record one fresh Agent Episode."""

    def __init__(
        self,
        *,
        replay_engine: ReplayEngine,
        artifact_store: ArtifactStore,
        model_name: str,
        model_digest: str,
        max_steps: int = 40,
        timeout_seconds: int = 900,
    ) -> None:
        self.replay_engine = replay_engine
        self.artifact_store = artifact_store
        self.model_name = model_name
        self.model_digest = model_digest
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        *,
        source_scenario_case_id: str,
        generated_content: str,
        execution_id: str,
        seed: int,
    ) -> OfficeV2RealEpisodeResult:
        source = source_attack_case(source_scenario_case_id)
        canonical_world = load_canonical_world()
        materialized = rematerialize_office_v2_scenario_text(
            source_case=source,
            canonical_world=canonical_world,
            generated_content=generated_content,
            purpose=infer_office_v2_compatibility_purpose(source, canonical_world),
            seed=seed,
        )
        selected = OfficeV2PublicCase(
            public_id=materialized.scenario_case.case_id,
            case=materialized.scenario_case,
            initial_state=materialized.initial_state,
            initialization_transition=materialized.initialization_transition,
        )
        request = build_office_v2_public_request(
            selected,
            execution_id=execution_id,
            model_name=self.model_name,
            model_digest=self.model_digest,
            seed=seed,
            max_steps=self.max_steps,
            timeout_seconds=self.timeout_seconds,
        )
        started = time.monotonic()
        manifest = await self.replay_engine.record_request(request)
        elapsed_ms = max(1, round((time.monotonic() - started) * 1000))
        if manifest.office_v2_oracle is None:
            raise ValueError("recorded Office V2 Episode has no Oracle artifact")
        oracle = OfficeV2RecordedOracleArtifact.model_validate_json(
            self.artifact_store.read_bytes(manifest.office_v2_oracle)
        )
        coverage_input = v2_coverage_input_from_recording(
            manifest, oracle, container_removed=True
        )
        agent_tokens = _recorded_agent_tokens(
            self.artifact_store.read_bytes(manifest.model_decisions)
        )
        return OfficeV2RealEpisodeResult(
            scenario_case=materialized.scenario_case,
            manifest=manifest,
            oracle=oracle,
            coverage_input=coverage_input,
            agent_tokens=agent_tokens,
            elapsed_ms=elapsed_ms,
        )


def _recorded_agent_tokens(payload: bytes) -> int:
    decisions = tuple(
        RecordedModelDecision.model_validate_json(line)
        for line in payload.splitlines()
        if line.strip()
    )
    total = 0
    for decision in decisions:
        if not isinstance(decision.action, dict):
            raise ValueError("recorded Agent decision action is not an object")
        usage = decision.action.get(RECORDED_MODEL_TOKEN_USAGE_KEY)
        if not isinstance(usage, dict):
            raise ValueError("recorded Agent decision has no token usage")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if (
            not isinstance(prompt_tokens, int)
            or isinstance(prompt_tokens, bool)
            or prompt_tokens < 0
            or not isinstance(completion_tokens, int)
            or isinstance(completion_tokens, bool)
            or completion_tokens < 0
        ):
            raise ValueError("recorded Agent token usage is invalid")
        total += prompt_tokens + completion_tokens
    if not decisions:
        raise ValueError("recorded Agent Episode has no model decisions")
    return total


def source_attack_case(scenario_case_id: str) -> MaterializedScenarioCase:
    matches = tuple(
        item.case
        for item in office_v2_public_cases()
        if isinstance(item.case, MaterializedScenarioCase)
        and item.case.case_id == scenario_case_id
    )
    if len(matches) != 1:
        raise ValueError("supporting execution does not identify one frozen attack case")
    return matches[0]


__all__ = [
    "DockerOfficeV2EpisodeRunner",
    "OfficeV2RealEpisodeResult",
    "OfficeV2RecordedOracleArtifact",
    "_recorded_agent_tokens",
    "source_attack_case",
]
