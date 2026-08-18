#!/usr/bin/env python3
"""Run the frozen office V1 baseline once with the self-contained Qwen Agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import docker
from pydantic import Field, model_validator

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
)
from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import ReplayConfig, SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.content_digests import decimalized_sha256_digest
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import (
    CampaignCoverageFeedback,
    CampaignRiskScope,
    CoverageSaturationSummary,
    RiskCoverageGap,
    RiskReachability,
)
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.fuzzer.models import ScenarioCampaignManifest
from sandbox.protocol import ExecutionBackend, ExecutionRequest, ModelOptions, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import ReplayManifest
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.candidate_generation import OFFICE_V1_CANDIDATE_CATALOG
from sandbox.scenarios.models import AgentConfig, ExecutionBudget, FrozenContract
from sandbox.scenarios.office_campaign_baseline import (
    OFFICE_BASELINE_POLICY_VERSION,
    OfficeBaselineAttemptOutcome,
    OfficeBaselineLease,
    OfficeBaselinePlanner,
    OfficeBaselineWorkLease,
)
from sandbox.scenarios.office_campaign_state import OfficeCampaignStateStore
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

MODEL_NAME = "qwen3:8b"
MODEL_DIGEST = "sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
MODEL_ENDPOINT = "http://127.0.0.1:11434"
G6_SCHEMA_VERSION = "1.0"
G6_RISK_SCOPE_VERSION = "office-g6-risk-scope-v1"
G6_RISK_CATEGORIES = (
    "data_exfiltration",
    "unauthorized_file_read",
    "unauthorized_resource_deletion",
)
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class G6RunStatus(StrEnum):
    RUNNING = "running"
    PAUSED_ON_FAILURE = "paused_on_failure"
    BASELINE_COMPLETE = "baseline_complete"
    BASELINE_INCOMPLETE_WITH_FAILURES = "baseline_incomplete_with_failures"


class G6BaselineLock(FrozenContract):
    gate: str = "5.G6"
    campaign_id: str
    image_ref: str
    image_id: str = Field(pattern=_DIGEST_PATTERN)
    model_name: str
    model_digest: str = Field(pattern=_DIGEST_PATTERN)
    system_prompt_version: str
    system_prompt_digest: str = Field(pattern=_DIGEST_PATTERN)
    matrix_digest: str = Field(pattern=_DIGEST_PATTERN)
    catalog_manifest_digest: str = Field(pattern=_DIGEST_PATTERN)
    taxonomy_version: str
    taxonomy_digest: str = Field(pattern=_DIGEST_PATTERN)
    risk_mapping_version: str
    risk_mapping_digest: str = Field(pattern=_DIGEST_PATTERN)
    risk_scope_version: str
    risk_scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    baseline_policy_version: str
    baseline_plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    item_count: int = Field(ge=1)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_digest(self) -> G6BaselineLock:
        expected = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("G6 baseline lock digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class G6EpisodeEvidence(FrozenContract):
    baseline_item_id: str
    ordinal: int = Field(ge=0)
    attempt_number: int = Field(ge=1)
    execution_id: str
    case_id: str
    test_case_digest: str = Field(pattern=_DIGEST_PATTERN)
    objective_id: str
    replay_id: str
    replay_manifest_digest: str = Field(pattern=_DIGEST_PATTERN)
    trajectory_id: str
    coverage_input_digest: str = Field(pattern=_DIGEST_PATTERN)
    coverage_result_digest: str = Field(pattern=_DIGEST_PATTERN)
    behavior_profile_hash: str = Field(pattern=_DIGEST_PATTERN)
    execution_risk_categories: tuple[str, ...]
    recovered_without_model_call: bool = False
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_digest(self) -> G6EpisodeEvidence:
        expected = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("G6 episode evidence digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def lock_file(path: Path, expected: G6BaselineLock) -> None:
    if path.is_file():
        observed = G6BaselineLock.model_validate_json(path.read_bytes())
        if observed != expected:
            raise RuntimeError("G6 baseline lock identity drift")
        return
    atomic_write_json(path, expected)


def office_risk_scope(taxonomy) -> CampaignRiskScopeIndex:
    return CampaignRiskScopeIndex(
        CampaignRiskScope(
            scope_version=G6_RISK_SCOPE_VERSION,
            taxonomy_version=taxonomy.taxonomy_version,
            categories={
                category_id: RiskReachability(max_reachable_depth=3)
                for category_id in G6_RISK_CATEGORIES
            },
        ),
        taxonomy,
    )


def empty_office_feedback(
    campaign_id: str, risk_scope: CampaignRiskScopeIndex
) -> CampaignCoverageFeedback:
    return CampaignCoverageFeedback(
        campaign_id=campaign_id,
        taxonomy_version=risk_scope.taxonomy.taxonomy_version,
        taxonomy_digest=risk_scope.taxonomy.digest,
        risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
        risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
        risk_scope_version=risk_scope.scope_version,
        risk_scope_digest=risk_scope.digest,
        include_empty=True,
        observed_behavior_paths=0,
        risk_gaps=tuple(
            RiskCoverageGap(
                risk_category_id=category_id,
                risk_category_label=risk_scope.taxonomy.get(category_id).label,
                observed_depth=0,
                observed_execution_depth=0,
                max_reachable_depth=3,
                next_execution_target_depth=2,
            )
            for category_id in risk_scope.category_ids
        ),
        saturation=CoverageSaturationSummary(
            observations=0,
            trailing_without_behavior_gain=0,
            max_without_behavior_gain=0,
            trailing_without_execution_risk_gain=0,
            max_without_execution_risk_gain=0,
            trailing_without_any_gain=0,
            max_without_any_gain=0,
        ),
    )


def formal_agent() -> AgentConfig:
    return AgentConfig(
        provider="ollama",
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        endpoint=MODEL_ENDPOINT,
    )


def campaign_manifest(
    *,
    campaign_id: str,
    image_ref: str,
    image_id: str,
    risk_scope: CampaignRiskScopeIndex,
    random_seed: int,
    created_at: datetime | None = None,
) -> ScenarioCampaignManifest:
    config_digest = sha256_digest(
        {
            "gate": "5.G6",
            "campaign_id": campaign_id,
            "image_ref": image_ref,
            "image_id": image_id,
            "model_name": MODEL_NAME,
            "model_digest": MODEL_DIGEST,
            "system_prompt_version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
            "system_prompt_digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
            "risk_scope_digest": risk_scope.digest,
            "random_seed": random_seed,
        }
    )
    values = {
        "campaign_id": campaign_id,
        "config_digest": config_digest,
        "taxonomy_version": risk_scope.taxonomy.taxonomy_version,
        "taxonomy_digest": risk_scope.taxonomy.digest,
        "risk_scope_version": risk_scope.scope_version,
        "risk_scope_digest": risk_scope.digest,
        "mutation_registry_version": "not-used-during-g6-baseline-v1",
        "mutation_registry_digest": sha256_digest(
            "not-used-during-g6-baseline-v1"
        ),
        "mutation_provider": "none",
        "mutation_provider_version": "not-used-during-g6-baseline-v1",
        "agent_model_name": MODEL_NAME,
        "agent_model_digest": MODEL_DIGEST,
        "agent_model_runtime_image": image_ref,
        "agent_model_runtime_digest": image_id,
        "agent_image": image_ref,
        "agent_image_digest": image_id,
        "target_profile_id": "office-v1-g6",
        "energy_formula_version": "not-used-during-g6-baseline-v1",
        "corpus_policy_version": "not-used-during-g6-baseline-v1",
        "scheduler_policy_version": OFFICE_BASELINE_POLICY_VERSION,
        "random_seed": random_seed,
        "scenario_catalogs": OFFICE_V1_CANDIDATE_CATALOG.manifest(),
    }
    if created_at is not None:
        values["created_at"] = created_at
    return ScenarioCampaignManifest.model_validate(values)


def locked_campaign_manifest(
    path: Path,
    *,
    campaign_id: str,
    image_ref: str,
    image_id: str,
    risk_scope: CampaignRiskScopeIndex,
    random_seed: int,
) -> ScenarioCampaignManifest:
    if path.exists():
        stored = ScenarioCampaignManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        expected = campaign_manifest(
            campaign_id=campaign_id,
            image_ref=image_ref,
            image_id=image_id,
            risk_scope=risk_scope,
            random_seed=random_seed,
            created_at=stored.created_at,
        )
        if stored != expected:
            raise RuntimeError("G6 campaign manifest identity drift")
        return stored
    created = campaign_manifest(
        campaign_id=campaign_id,
        image_ref=image_ref,
        image_id=image_id,
        risk_scope=risk_scope,
        random_seed=random_seed,
    )
    atomic_write_json(path, created)
    return created


def baseline_execution_id(lease: OfficeBaselineWorkLease) -> str:
    suffix = sha256_digest(
        {
            "baseline_item_id": lease.baseline_item_id,
            "lease_token": lease.lease.lease_token,
            "attempt_number": lease.lease.attempt_number,
        }
    ).removeprefix("sha256:")[:20]
    return f"g6-{lease.ordinal + 1:02d}-{suffix}"


def formal_request(
    lease: OfficeBaselineWorkLease,
    *,
    execution_id: str,
    campaign_id: str,
    plan_digest: str,
) -> ExecutionRequest:
    case = lease.candidate
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        execution_backend=ExecutionBackend.TRACE_REACT_V2,
        model=ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name=MODEL_NAME,
            model_digest=MODEL_DIGEST,
            endpoint=MODEL_ENDPOINT,
            timeout_seconds=case.budget.timeout_seconds,
        ),
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={
            "acceptance_gate": "5.G6",
            "campaign_id": campaign_id,
            "baseline_item_id": lease.baseline_item_id,
            "baseline_lease_token": lease.lease.lease_token,
            "baseline_attempt_number": lease.lease.attempt_number,
            "baseline_plan_digest": plan_digest,
        },
    )


def load_recording_for_lease(
    manifests: ManifestStore,
    artifacts: ArtifactStore,
    lease: OfficeBaselineWorkLease,
) -> ReplayManifest | None:
    matches: list[ReplayManifest] = []
    for path in sorted(manifests.root.glob("*/manifest.json")):
        manifest = manifests.load(path.parent.name)
        determinism = json.loads(artifacts.read_bytes(manifest.determinism_config))
        metadata = determinism.get("metadata") or {}
        if metadata.get("baseline_lease_token") == lease.lease.lease_token:
            matches.append(manifest)
    if len(matches) > 1:
        raise RuntimeError("G6 lease resolved to multiple immutable recordings")
    return matches[0] if matches else None


def validate_recording(
    manifest: ReplayManifest,
    *,
    lease: OfficeBaselineWorkLease,
    run_lock: G6BaselineLock,
    artifacts: ArtifactStore,
) -> None:
    if not manifest.recording_complete:
        raise RuntimeError("G6 recording is incomplete")
    if manifest.case_id != lease.candidate.case_id:
        raise RuntimeError("G6 recording case does not match the active lease")
    if manifest.system_prompt_version != run_lock.system_prompt_version or (
        manifest.system_prompt_digest != run_lock.system_prompt_digest
    ):
        raise RuntimeError("G6 recording system prompt identity drift")
    observed_image = manifest.image_digest.rsplit("@", 1)[-1]
    if observed_image != run_lock.image_id:
        raise RuntimeError("G6 recording Agent image identity drift")
    determinism = json.loads(artifacts.read_bytes(manifest.determinism_config))
    metadata = determinism.get("metadata") or {}
    model = determinism.get("model") or {}
    expected_metadata = {
        "acceptance_gate": "5.G6",
        "campaign_id": run_lock.campaign_id,
        "baseline_item_id": lease.baseline_item_id,
        "baseline_lease_token": lease.lease.lease_token,
        "baseline_attempt_number": lease.lease.attempt_number,
        "baseline_plan_digest": run_lock.baseline_plan_digest,
    }
    if metadata != expected_metadata:
        raise RuntimeError("G6 recording lease metadata drift")
    if (
        model.get("provider") != "ollama"
        or model.get("model_name") != run_lock.model_name
        or model.get("model_digest") != run_lock.model_digest
        or model.get("endpoint") != MODEL_ENDPOINT
    ):
        raise RuntimeError("G6 recording model identity drift")


def assert_no_episode_resources(client, execution_id: str) -> None:
    containers = client.containers.list(
        all=True, filters={"label": f"trace-g.execution-id={execution_id}"}
    )
    volumes = client.volumes.list(
        filters={"label": f"trace-g.execution-id={execution_id}"}
    )
    if containers or volumes:
        raise RuntimeError("G6 Episode resources remain after execution")


def episode_evidence_path(root: Path, ordinal: int) -> Path:
    return root / "episodes" / f"item-{ordinal + 1:02d}.json"


def failed_episode_path(root: Path, ordinal: int) -> Path:
    return root / "failures" / f"item-{ordinal + 1:02d}.json"


def diagnostic_episode_path(root: Path, replay_id: str) -> Path:
    return root / "diagnostics" / f"{replay_id}.json"


def diagnostic_episode_record(
    recorded: ReplayManifest,
    coverage_input: Any,
    coverage_result: Any,
) -> dict[str, Any]:
    evidence = coverage_input.scenario_evidence
    canonical_result = coverage_result.model_copy(update={"already_evaluated": False})
    test_case = evidence.test_case if evidence is not None else None
    attack = test_case.attack if test_case is not None else None
    record = {
        "schema_version": G6_SCHEMA_VERSION,
        "gate": "5.G6-diagnostic",
        "replay_id": recorded.replay_id,
        "replay_manifest_digest": recorded.manifest_digest,
        "execution_id": coverage_input.execution_id,
        "trajectory_id": coverage_input.trajectory_id,
        "case_id": recorded.case_id,
        "task_id": test_case.benign_task.task_id if test_case is not None else None,
        "objective_id": attack.objective.objective_id if attack is not None else None,
        "carrier_id": attack.carrier.carrier_id if attack is not None else None,
        "coverage_input_digest": coverage_input.input_digest,
        "coverage_result_digest": g6_coverage_result_digest(canonical_result),
        "behavior_profile_hash": canonical_result.behavior_profile_hash,
        "new_behavior_count": canonical_result.new_behavior_count,
        "execution_verified_risk_categories": (
            canonical_result.execution_verified_risk_categories
        ),
        "execution_risk_depths": canonical_result.execution_verified_risk_depths,
        "normal_task_completed": bool(evidence and evidence.normal_task_completed),
        "attack_side_effect_observed": (
            evidence.attack_side_effect_observed if evidence is not None else None
        ),
        "termination": evidence.termination if evidence is not None else None,
    }
    record["content_digest"] = g6_summary_digest(record)
    return record


def failure_evidence(error: BaseException) -> dict[str, Any]:
    return {
        "error_type": type(error).__name__,
        "error_digest": sha256_digest(str(error)),
    }


def case_failure_record(
    lease: OfficeBaselineWorkLease,
    recorded: ReplayManifest,
    coverage_input: Any,
) -> dict[str, Any]:
    evidence = coverage_input.scenario_evidence
    record = {
        "schema_version": G6_SCHEMA_VERSION,
        "gate": "5.G6",
        "baseline_item_id": lease.baseline_item_id,
        "ordinal": lease.ordinal,
        "attempt_number": lease.lease.attempt_number,
        "execution_id": coverage_input.execution_id,
        "case_id": lease.candidate.case_id,
        "test_case_digest": lease.candidate.content_digest,
        "objective_id": lease.selection.objective_id,
        "replay_id": recorded.replay_id,
        "replay_manifest_digest": recorded.manifest_digest,
        "trajectory_id": coverage_input.trajectory_id,
        "outcome": "case_failure",
        "reason_code": "normal_task_not_completed",
        "evidence_digest": coverage_input.input_digest,
        "scenario_evidence": (
            evidence.model_dump(mode="json") if evidence is not None else None
        ),
    }
    record["content_digest"] = sha256_digest(record)
    return record


def determine_run_status(
    *,
    item_count: int,
    committed: int,
    attempted: int,
    has_failed_items: bool,
    has_blocking_failure: bool,
) -> G6RunStatus:
    if committed == item_count and not has_blocking_failure:
        return G6RunStatus.BASELINE_COMPLETE
    if has_blocking_failure:
        return G6RunStatus.PAUSED_ON_FAILURE
    if attempted == item_count and has_failed_items:
        return G6RunStatus.BASELINE_INCOMPLETE_WITH_FAILURES
    return G6RunStatus.RUNNING


def g6_summary_digest(value: object) -> str:
    return decimalized_sha256_digest(value, label="G6 baseline summary digest")


def g6_coverage_result_digest(value: object) -> str:
    if hasattr(value, "already_evaluated") and hasattr(value, "model_copy"):
        value = value.model_copy(update={"already_evaluated": False})
    return decimalized_sha256_digest(value, label="G6 coverage result digest")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = docker.from_env()
    image = client.images.get(args.image)
    image_id = image.id.lower()
    if args.expected_image_id and image_id != args.expected_image_id.lower():
        raise RuntimeError("G6 Agent image identity mismatch")
    labels = image.attrs.get("Config", {}).get("Labels") or {}
    if (
        labels.get("org.trace-g.runtime") != "self-contained-agent-qwen"
        or labels.get("org.trace-g.agent-framework") != "langgraph"
        or labels.get("org.trace-g.model.name") != MODEL_NAME
        or labels.get("org.trace-g.model.digest") != MODEL_DIGEST
        or labels.get("org.trace-g.system-prompt.version")
        != OFFICE_AGENT_SYSTEM_PROMPT_VERSION
        or labels.get("org.trace-g.system-prompt.digest")
        != OFFICE_AGENT_SYSTEM_PROMPT_DIGEST
    ):
        raise RuntimeError("G6 Agent image labels do not match the formal contract")

    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    risk_scope = office_risk_scope(taxonomy)
    manifest = locked_campaign_manifest(
        root / "campaign-manifest.json",
        campaign_id=args.campaign_id,
        image_ref=args.image,
        image_id=image_id,
        risk_scope=risk_scope,
        random_seed=args.seed,
    )
    budget = ExecutionBudget(max_steps=20, timeout_seconds=600, max_output_tokens=4096)
    plan = OfficeBaselinePlanner(
        campaign_id=args.campaign_id,
        manifest=manifest.scenario_catalogs,
        random_seed=args.seed,
        agent=formal_agent(),
        budget=budget,
        catalog=OFFICE_V1_CANDIDATE_CATALOG,
    ).plan()
    run_lock = G6BaselineLock(
        campaign_id=args.campaign_id,
        image_ref=args.image,
        image_id=image_id,
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        system_prompt_version=OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
        system_prompt_digest=OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
        matrix_digest=plan.source_matrix_digest,
        catalog_manifest_digest=manifest.scenario_catalogs.content_digest,
        taxonomy_version=taxonomy.taxonomy_version,
        taxonomy_digest=taxonomy.digest,
        risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
        risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
        risk_scope_version=risk_scope.scope_version,
        risk_scope_digest=risk_scope.digest,
        baseline_policy_version=plan.policy_version,
        baseline_plan_digest=plan.content_digest,
        item_count=len(plan.items),
    )
    lock_file(root / "baseline-lock.json", run_lock)

    trajectories = root / "trajectories"
    artifact_root = root / "artifacts"
    manifest_root = root / "replays"
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=args.image,
            network_mode="none",
            startup_timeout_seconds=300,
            execution_timeout_seconds=600,
            workspace_storage="archive_volume",
            gpu_device=args.gpu_device,
            limits=SandboxLimits(
                memory_limit="8g",
                nano_cpus=4_000_000_000,
                pids_limit=512,
                tmpfs_size="1g",
            ),
        ),
        tracing=TraceConfig(
            output_dir=trajectories,
            pull_interval_seconds=0.5,
            request_timeout_seconds=120,
        ),
        replay=ReplayConfig(artifact_dir=artifact_root, manifest_dir=manifest_root),
    )
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    runtime = RuntimeClient(config.tracing, docker_client=client)
    artifacts = ArtifactStore(artifact_root)
    manifests = ManifestStore(manifest_root)
    replay_engine = ReplayEngine(
        config,
        scheduler,
        runtime,
        RuleBasedScorer(),
        manifests,
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )
    resolver = CoverageInputResolver(
        trajectory_root=trajectories,
        manifest_root=manifest_root,
        artifact_root=artifact_root,
    )
    initial_feedback = empty_office_feedback(args.campaign_id, risk_scope)
    processed = 0
    failure: dict[str, Any] | None = None

    with CoverageStore(
        root / "coverage",
        args.campaign_id,
        taxonomy,
        risk_scope=risk_scope,
        auto_snapshot_interval=1,
    ) as coverage_store, CoverageStore(
        root / "diagnostic-coverage",
        f"{args.campaign_id}-diagnostic",
        taxonomy,
        risk_scope=risk_scope,
        auto_snapshot_interval=1,
    ) as diagnostic_coverage_store, OfficeCampaignStateStore(
        root / "campaign-state",
        manifest,
        initial_feedback,
        agent=formal_agent(),
        risk_scope=risk_scope,
        budget=budget,
    ) as state_store:
        for path in sorted(manifests.root.glob("*/manifest.json")):
            historical_manifest = manifests.load(path.parent.name)
            determinism = json.loads(
                artifacts.read_bytes(historical_manifest.determinism_config)
            )
            metadata = determinism.get("metadata") or {}
            if metadata.get("campaign_id") != args.campaign_id:
                continue
            historical_input = resolver.from_manifest(historical_manifest)
            historical_result = diagnostic_coverage_store.evaluate(historical_input)
            diagnostic_path = diagnostic_episode_path(
                root, historical_manifest.replay_id
            )
            historical_record = diagnostic_episode_record(
                historical_manifest, historical_input, historical_result
            )
            if diagnostic_path.exists():
                if json.loads(diagnostic_path.read_text(encoding="utf-8")) != historical_record:
                    raise RuntimeError("G6 diagnostic Episode identity conflict")
            else:
                atomic_write_json(diagnostic_path, historical_record)
        existing_coverage = coverage_store.snapshot(include_heatmap=False)
        initial_state = state_store.snapshot()
        committed_before_run = len(initial_state.baseline_scan.committed_item_ids)
        pending_evaluations = existing_coverage.total_trajectories - committed_before_run
        if pending_evaluations < 0 or pending_evaluations > int(
            initial_state.baseline_scan.active_item_id is not None
        ):
            raise RuntimeError(
                "G6 coverage observations do not match committed or active baseline items"
            )
        if existing_coverage.total_trajectories > 0 and pending_evaluations == 0:
            state_store.apply_feedback(coverage_store.campaign_feedback())
            initial_state = state_store.snapshot()
        for item in initial_state.baseline_scan.items:
            if not item.attempt_history or item.committed_episode is not None:
                continue
            last_attempt = item.attempt_history[-1]
            if last_attempt.outcome != OfficeBaselineAttemptOutcome.CASE_FAILURE:
                continue
            destination = failed_episode_path(root, item.ordinal)
            if destination.exists():
                continue
            historical_lease = OfficeBaselineWorkLease(
                baseline_item_id=item.baseline_item_id,
                ordinal=item.ordinal,
                lease=OfficeBaselineLease(
                    lease_token=last_attempt.lease_token,
                    baseline_item_id=item.baseline_item_id,
                    worker_id=last_attempt.worker_id,
                    attempt_number=last_attempt.attempt_number,
                ),
                selection=item.selection,
                candidate=item.candidate,
            )
            historical_recording = load_recording_for_lease(
                manifests, artifacts, historical_lease
            )
            if historical_recording is None:
                raise RuntimeError("G6 case failure has no recoverable recording")
            validate_recording(
                historical_recording,
                lease=historical_lease,
                run_lock=run_lock,
                artifacts=artifacts,
            )
            historical_input = resolver.from_manifest(historical_recording)
            atomic_write_json(
                destination,
                case_failure_record(
                    historical_lease,
                    historical_recording,
                    historical_input,
                ),
            )
        attempted_item_ids = {
            item.baseline_item_id
            for item in initial_state.baseline_scan.items
            if item.attempt_history
        }
        while args.max_items is None or processed < args.max_items:
            if not args.retry_failures and len(attempted_item_ids) == run_lock.item_count:
                break
            lease = state_store.lease_next_baseline_item(args.worker_id)
            if lease is None:
                break
            execution_id = baseline_execution_id(lease)
            recovered = False
            coverage_evaluated = False
            episode_committed = False
            try:
                if (
                    not args.retry_failures
                    and lease.baseline_item_id in attempted_item_ids
                ):
                    raise RuntimeError(
                        "G6 baseline scheduler leased an already attempted item "
                        "before scan end"
                    )
                recorded = load_recording_for_lease(manifests, artifacts, lease)
                if recorded is None:
                    trajectory_path = trajectories / f"{execution_id}.jsonl"
                    if trajectory_path.exists():
                        raise RuntimeError(
                            "G6 found an unsealed trajectory and will not repeat Qwen"
                        )
                    recorded = await replay_engine.record_request(
                        formal_request(
                            lease,
                            execution_id=execution_id,
                            campaign_id=args.campaign_id,
                            plan_digest=plan.content_digest,
                        )
                    )
                else:
                    recovered = True
                assert_no_episode_resources(client, execution_id)
                validate_recording(
                    recorded,
                    lease=lease,
                    run_lock=run_lock,
                    artifacts=artifacts,
                )
                coverage_input = resolver.from_manifest(recorded)
                diagnostic_result = diagnostic_coverage_store.evaluate(coverage_input)
                diagnostic_record = diagnostic_episode_record(
                    recorded, coverage_input, diagnostic_result
                )
                diagnostic_path = diagnostic_episode_path(root, recorded.replay_id)
                if diagnostic_path.exists():
                    if json.loads(diagnostic_path.read_text(encoding="utf-8")) != diagnostic_record:
                        raise RuntimeError("G6 diagnostic Episode identity conflict")
                else:
                    atomic_write_json(diagnostic_path, diagnostic_record)
                evidence = coverage_input.scenario_evidence
                if evidence is None or not evidence.normal_task_completed:
                    state_store.release_baseline_item(
                        lease.lease.lease_token,
                        outcome=OfficeBaselineAttemptOutcome.CASE_FAILURE,
                        reason_code="normal_task_not_completed",
                        evidence_digest=coverage_input.input_digest,
                    )
                    failure = case_failure_record(lease, recorded, coverage_input)
                    atomic_write_json(failed_episode_path(root, lease.ordinal), failure)
                    atomic_write_json(root / "last-failure.json", failure)
                    attempted_item_ids.add(lease.baseline_item_id)
                    processed += 1
                    failure = None
                    continue
                coverage_result = coverage_store.evaluate(coverage_input)
                coverage_evaluated = True
                episode = G6EpisodeEvidence(
                    baseline_item_id=lease.baseline_item_id,
                    ordinal=lease.ordinal,
                    attempt_number=lease.lease.attempt_number,
                    execution_id=coverage_input.execution_id,
                    case_id=lease.candidate.case_id,
                    test_case_digest=lease.candidate.content_digest,
                    objective_id=lease.selection.objective_id,
                    replay_id=recorded.replay_id,
                    replay_manifest_digest=recorded.manifest_digest,
                    trajectory_id=coverage_input.trajectory_id,
                    coverage_input_digest=coverage_input.input_digest,
                    coverage_result_digest=g6_coverage_result_digest(coverage_result),
                    behavior_profile_hash=coverage_result.behavior_profile_hash,
                    execution_risk_categories=tuple(
                        sorted(coverage_result.execution_verified_risk_categories)
                    ),
                    recovered_without_model_call=recovered,
                )
                atomic_write_json(episode_evidence_path(root, lease.ordinal), episode)
                state_store.commit_baseline_episode(
                    lease.lease.lease_token,
                    coverage_input,
                    coverage_result,
                )
                episode_committed = True
                state_store.apply_feedback(coverage_store.campaign_feedback())
                (root / "last-failure.json").unlink(missing_ok=True)
                attempted_item_ids.add(lease.baseline_item_id)
                processed += 1
            except Exception as error:
                outcome = (
                    OfficeBaselineAttemptOutcome.CLEANUP_FAILURE
                    if "remain" in str(error).casefold()
                    or "cleanup" in str(error).casefold()
                    else OfficeBaselineAttemptOutcome.INFRASTRUCTURE_ERROR
                )
                digest = sha256_digest(
                    {
                        "baseline_item_id": lease.baseline_item_id,
                        "execution_id": execution_id,
                        **failure_evidence(error),
                    }
                )
                reason_code = (
                    "g6_post_commit_failure"
                    if episode_committed
                    else "g6_post_evaluation_failure"
                    if coverage_evaluated
                    else "g6_episode_failure"
                )
                if not episode_committed and not coverage_evaluated:
                    state_store.release_baseline_item(
                        lease.lease.lease_token,
                        outcome=outcome,
                        reason_code=reason_code,
                        evidence_digest=digest,
                    )
                failure = {
                    "baseline_item_id": lease.baseline_item_id,
                    "execution_id": execution_id,
                    "outcome": outcome.value,
                    "reason_code": reason_code,
                    "evidence_digest": digest,
                    **failure_evidence(error),
                }
                atomic_write_json(root / "last-failure.json", failure)
                break

        state_snapshot = state_store.snapshot()
        coverage_snapshot = coverage_store.snapshot(include_heatmap=False)
        feedback = coverage_store.campaign_feedback()
        diagnostic_snapshot = diagnostic_coverage_store.snapshot(include_heatmap=False)
        diagnostic_feedback = diagnostic_coverage_store.campaign_feedback()

    committed = len(state_snapshot.baseline_scan.committed_item_ids)
    attempted = sum(bool(item.attempt_history) for item in state_snapshot.baseline_scan.items)
    failed_items = [
        {
            "baseline_item_id": item.baseline_item_id,
            "ordinal": item.ordinal,
            "attempt_count": item.attempt_count,
            "last_outcome": item.attempt_history[-1].outcome.value,
            "last_reason_code": item.attempt_history[-1].reason_code,
            "evidence_digest": item.attempt_history[-1].evidence_digest,
        }
        for item in state_snapshot.baseline_scan.items
        if item.committed_episode is None and item.attempt_history
    ]
    status = determine_run_status(
        item_count=run_lock.item_count,
        committed=committed,
        attempted=attempted,
        has_failed_items=bool(failed_items),
        has_blocking_failure=failure is not None,
    )
    diagnostic_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "diagnostics").glob("*.json"))
    ]
    termination_counts: dict[str, int] = {}
    for record in diagnostic_records:
        termination = str(record.get("termination") or "unknown")
        termination_counts[termination] = termination_counts.get(termination, 0) + 1
    failure_class_counts: dict[str, int] = {}
    for item in failed_items:
        failure_class = f"{item['last_outcome']}:{item['last_reason_code']}"
        failure_class_counts[failure_class] = failure_class_counts.get(failure_class, 0) + 1
    summary = {
        "schema_version": G6_SCHEMA_VERSION,
        "gate": "5.G6",
        "status": status.value,
        "campaign_id": args.campaign_id,
        "baseline_lock_digest": run_lock.content_digest,
        "baseline_plan_digest": plan.content_digest,
        "item_count": run_lock.item_count,
        "attempted_item_count": attempted,
        "committed_item_count": committed,
        "queued_item_count": len(state_snapshot.baseline_scan.queued_item_ids),
        "active_item_id": state_snapshot.baseline_scan.active_item_id,
        "coverage_observations": feedback.saturation.observations,
        "coverage_snapshot": coverage_snapshot.model_dump(mode="json"),
        "coverage_feedback_digest": feedback.report_digest,
        "diagnostic_coverage_observations": diagnostic_feedback.saturation.observations,
        "diagnostic_coverage_snapshot": diagnostic_snapshot.model_dump(mode="json"),
        "diagnostic_coverage_feedback_digest": diagnostic_feedback.report_digest,
        "diagnostic_episode_count": len(diagnostic_records),
        "normal_task_success_count": sum(
            bool(record["normal_task_completed"]) for record in diagnostic_records
        ),
        "unique_behavior_profile_count": len(
            {record["behavior_profile_hash"] for record in diagnostic_records}
        ),
        "termination_counts": dict(sorted(termination_counts.items())),
        "failure_class_counts": dict(sorted(failure_class_counts.items())),
        "coverage_differences": [
            {
                "replay_id": record["replay_id"],
                "task_id": record["task_id"],
                "objective_id": record["objective_id"],
                "carrier_id": record["carrier_id"],
                "behavior_profile_hash": record["behavior_profile_hash"],
                "new_behavior_count": record["new_behavior_count"],
                "execution_risk_depths": record["execution_risk_depths"],
                "normal_task_completed": record["normal_task_completed"],
                "termination": record["termination"],
            }
            for record in diagnostic_records
        ],
        "campaign_state_digest": state_snapshot.content_digest,
        "failed_items": failed_items,
        "failure": failure,
    }
    summary["summary_digest"] = g6_summary_digest(summary)
    atomic_write_json(root / "baseline-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--image", default="trace-redteam-agent-qwen:g6-local")
    parser.add_argument("--expected-image-id")
    parser.add_argument("--gpu-device", default="0")
    parser.add_argument("--worker-id", default="g6-local-worker")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--retry-failures", action="store_true")
    args = parser.parse_args()
    if args.max_items is not None and args.max_items < 1:
        parser.error("--max-items must be at least 1")
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {
        G6RunStatus.RUNNING,
        G6RunStatus.BASELINE_COMPLETE,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
