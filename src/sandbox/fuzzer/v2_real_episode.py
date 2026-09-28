"""Official Docker Agent adapter for one materialized Office V2 generation."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import model_validator

from sandbox.coverage.v2_input import (
    V2CoverageInput,
    v2_coverage_input_from_recording,
)
from sandbox.fuzzer.models import SandboxRunContext
from sandbox.fuzzer.v2_target_oracle import build_formal_target_scenario_supports
from sandbox.protocol import ModelInferenceOptions, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import (
    RECORDED_MODEL_TOKEN_USAGE_KEY,
    RecordedModelDecision,
    ReplayManifest,
)
from sandbox.scenarios.office_v2.agent_context import (
    TASK_EXECUTION_SUPPORT_METADATA_KEY,
)
from sandbox.scenarios.office_v2.attack_models import (
    CompatibilityPurpose,
    DirectTaskCondition,
    MaterializedScenarioCase,
)
from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.execution_request import (
    IN_CONTAINER_OLLAMA_ENDPOINT,
    build_office_v2_execution_request,
)
from sandbox.scenarios.office_v2.fork import (
    rematerialize_office_v2_direct_task_text,
)
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
)
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceBundle
from sandbox.scenarios.office_v2.oracle_models import CompleteScenarioOracleResult

if TYPE_CHECKING:
    from sandbox.replay.replay_engine import ReplayEngine


class OfficeV2RecordedOracleArtifact(OfficeV2Contract):
    artifact_version: Literal["office-v2-live-oracle-artifact-v1"]
    execution_id: Identifier
    trace_digest: Sha256Digest
    trusted_facts_digest: Sha256Digest
    evidence_bundle: OracleEvidenceBundle
    oracle_result: CompleteScenarioOracleResult
    artifact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"artifact_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if self.oracle_result.input_bundle_digest != self.evidence_bundle.bundle_digest:
            raise ValueError("recorded Oracle result does not match evidence bundle")
        if self.artifact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("recorded Oracle artifact digest does not match")
        return self


class OfficeV2EpisodeResult(OfficeV2Contract):
    scenario_case: MaterializedScenarioCase
    manifest: ReplayManifest
    oracle: OfficeV2RecordedOracleArtifact
    coverage_input: V2CoverageInput
    agent_tokens: int
    elapsed_ms: int
    # Cost completeness travels outside the integer cost contract: a recording
    # whose decisions carry no token usage still yields a usable Episode, and the
    # consumer marks the cost as incomplete instead of discarding the evidence.
    agent_tokens_incomplete: bool = False
    agent_token_decisions: int = 0
    agent_token_missing_decisions: int = 0


class DockerOfficeV2EpisodeRunner:
    """Rematerialize one accepted payload and record one fresh Agent Episode."""

    def __init__(
        self,
        *,
        replay_engine: ReplayEngine,
        artifact_store: ArtifactStore,
        model_name: str,
        model_provider: ModelProvider = ModelProvider.OLLAMA,
        model_endpoint: str | None = IN_CONTAINER_OLLAMA_ENDPOINT,
        model_inference: ModelInferenceOptions | None = None,
        use_frozen_interaction_response: bool = False,
        max_steps: int = 40,
        max_tool_calls: int = 24,
        timeout_seconds: int = 600,
        diagnostic_dir: Path | None = None,
    ) -> None:
        self.replay_engine = replay_engine
        self.artifact_store = artifact_store
        self.model_name = model_name
        self.model_provider = model_provider
        self.model_endpoint = model_endpoint
        self.model_inference = model_inference
        self.use_frozen_interaction_response = use_frozen_interaction_response
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.diagnostic_dir = diagnostic_dir
        self.recover_recordings = False

    def record_failure(
        self,
        *,
        execution_id: str,
        source_scenario_case_id: str,
        work_id: str,
        attempt_number: int,
        error: Exception,
        retryable: bool,
    ) -> Path | None:
        if self.diagnostic_dir is None:
            return None
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "office-v2-episode-failure-v1",
            "execution_id": execution_id,
            "source_scenario_case_id": source_scenario_case_id,
            "work_id": work_id,
            "attempt_number": attempt_number,
            "retryable": retryable,
            "error_type": type(error).__name__,
            "error_message": str(error)[:2_000],
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        destination = self.diagnostic_dir / f"failure-{execution_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
        return destination

    def cleanup_interrupted(self, campaign_id: str) -> None:
        import asyncio

        report = asyncio.run(
            self.replay_engine.scheduler.cleanup_campaign_orphans(
                campaign_id,
                active_execution_ids=set(),
                max_age_seconds=0,
            )
        )
        if report.failures:
            raise RuntimeError(f"Unable to clean interrupted Episode resources: {report.failures}")
        self.recover_recordings = True

    async def execute(
        self,
        *,
        source_scenario_case_id: str,
        generated_content: str,
        execution_id: str,
        seed: int,
        run_context: SandboxRunContext,
    ) -> OfficeV2EpisodeResult:
        canonical_world = load_canonical_world()
        source, purpose = source_attack_materialization_context(
            source_scenario_case_id,
            canonical_world,
        )
        materialized = rematerialize_office_v2_direct_task_text(
            source_case=source,
            canonical_world=canonical_world,
            generated_content=generated_content,
            purpose=purpose,
            seed=seed,
        )
        formal_support = next(
            item
            for item in build_formal_target_scenario_supports()
            if item.scenario_case.case_id == source_scenario_case_id
        )
        task_execution_support = formal_support.agent_task_execution_support()
        request = build_office_v2_execution_request(
            materialized.scenario_case,
            initial_state=materialized.initial_state,
            initialization_transition=materialized.initialization_transition,
            execution_id=execution_id,
            model_name=self.model_name,
            seed=seed,
            max_steps=self.max_steps,
            max_tool_calls=self.max_tool_calls,
            timeout_seconds=self.timeout_seconds,
            use_frozen_response=self.use_frozen_interaction_response,
            model_provider=self.model_provider,
            model_endpoint=self.model_endpoint,
            model_inference=self.model_inference,
            metadata={
                "campaign_scenario_entry": "selected-risk-seed",
                "selected_case_id": materialized.scenario_case.case_id,
                TASK_EXECUTION_SUPPORT_METADATA_KEY: (
                    task_execution_support.model_dump(
                        mode="json", exclude_none=False
                    )
                ),
            },
        )
        started = time.monotonic()
        manifest = None
        if self.recover_recordings:
            manifest = self._saved_recording(execution_id)
        if manifest is None:
            manifest = await self.replay_engine.record_request(request, run_context=run_context)
        elapsed_ms = max(1, round((time.monotonic() - started) * 1000))
        if manifest.office_v2_oracle is None:
            raise ValueError("recorded Office V2 Episode has no Oracle artifact")
        if manifest.office_v2_recording_state is None:
            raise ValueError("recorded Office V2 Episode has no recording state artifact")
        oracle_payload = self.artifact_store.read_bytes(manifest.office_v2_oracle)
        oracle = OfficeV2RecordedOracleArtifact.model_validate_json(oracle_payload)
        coverage_input = v2_coverage_input_from_recording(
            manifest,
            oracle_artifact_payload=oracle_payload,
            recording_state_payload=self.artifact_store.read_bytes(
                manifest.office_v2_recording_state
            ),
            container_removed=True,
        )
        usage = _recorded_agent_tokens(
            self.artifact_store.read_bytes(manifest.model_decisions)
        )
        return OfficeV2EpisodeResult(
            scenario_case=materialized.scenario_case,
            manifest=manifest,
            oracle=oracle,
            coverage_input=coverage_input,
            agent_tokens=usage.total,
            elapsed_ms=elapsed_ms,
            agent_tokens_incomplete=not usage.complete,
            agent_token_decisions=usage.decisions,
            agent_token_missing_decisions=usage.missing_decisions,
        )

    def has_saved_recording(self, execution_id: str) -> bool:
        return self._saved_recording(execution_id) is not None

    def _saved_recording(self, execution_id: str):
        for path in self.replay_engine.manifest_store.root.glob("*/manifest.json"):
            manifest = self.replay_engine.manifest_store.load(path.parent.name)
            if manifest.office_v2_oracle is not None:
                oracle = json.loads(self.artifact_store.read_bytes(manifest.office_v2_oracle))
                if oracle.get("execution_id") == execution_id:
                    return manifest
        return None


TOKEN_USAGE_INCOMPLETE_MARKER = "agent-token-usage-incomplete"


@dataclass(frozen=True)
class RecordedTokenUsage:
    """Token usage read from a recording, carrying its own completeness.

    ``total`` is the sum of the usage that *was* recorded. A decision without the
    usage key is counted in ``missing_decisions`` instead of failing the Episode:
    the behaviour evidence is intact and only the cost is partial. Invalid values
    and an empty decision stream stay hard errors, because those are corruption
    rather than absence.
    """

    total: int
    decisions: int
    missing_decisions: int

    @property
    def complete(self) -> bool:
        return self.missing_decisions == 0

    @property
    def unbounded(self) -> bool:
        """No decision carried usage, so the Episode cost cannot be bounded."""

        return self.decisions > 0 and self.missing_decisions == self.decisions


def _recorded_agent_tokens(payload: bytes) -> RecordedTokenUsage:
    decisions = tuple(
        RecordedModelDecision.model_validate_json(line)
        for line in payload.splitlines()
        if line.strip()
    )
    if not decisions:
        raise ValueError("recorded Agent Episode has no model decisions")
    total = 0
    missing = 0
    for decision in decisions:
        if not isinstance(decision.action, dict):
            raise ValueError("recorded Agent decision action is not an object")
        usage = decision.action.get(RECORDED_MODEL_TOKEN_USAGE_KEY)
        if usage is None:
            missing += 1
            continue
        if not isinstance(usage, dict):
            raise ValueError("recorded Agent token usage is invalid")
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
    return RecordedTokenUsage(
        total=total, decisions=len(decisions), missing_decisions=missing
    )


def source_attack_case(scenario_case_id: str) -> MaterializedScenarioCase:
    matches = tuple(
        item.scenario_case
        for item in build_formal_target_scenario_supports()
        if item.scenario_case.case_id == scenario_case_id
    )
    if len(matches) != 1:
        raise ValueError("supporting execution does not identify one frozen attack case")
    return matches[0]


def source_attack_materialization_context(
    scenario_case_id: str,
    canonical_world: CanonicalOfficeWorld,
) -> tuple[MaterializedScenarioCase, CompatibilityPurpose]:
    """Resolve a source case without re-inferring frozen formal support metadata."""

    formal_matches = tuple(
        item
        for item in build_formal_target_scenario_supports()
        if item.scenario_case.case_id == scenario_case_id
    )
    if len(formal_matches) != 1:
        raise ValueError("supporting execution does not identify one direct-task case")
    support = formal_matches[0]
    if not isinstance(support.scenario_case.adversarial_condition, DirectTaskCondition):
        raise ValueError("selected Campaign support must be a direct task")
    return support.scenario_case, support.compatibility_decision.purpose


__all__ = [
    "TOKEN_USAGE_INCOMPLETE_MARKER",
    "DockerOfficeV2EpisodeRunner",
    "OfficeV2EpisodeResult",
    "OfficeV2RecordedOracleArtifact",
    "RecordedTokenUsage",
    "_recorded_agent_tokens",
    "source_attack_case",
    "source_attack_materialization_context",
]
