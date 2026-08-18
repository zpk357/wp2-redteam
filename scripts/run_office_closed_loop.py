#!/usr/bin/env python3
"""Run a small recoverable coverage-guided office Campaign with real Qwen roles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import docker

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
    OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
)
from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import ReplayConfig, SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.content_digests import decimalized_sha256_digest
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import CampaignRiskScope, RiskReachability
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.fuzzer.corpus import CorpusPolicy
from sandbox.fuzzer.models import ScenarioCampaignManifest
from sandbox.protocol import ExecutionBackend, ExecutionRequest, ModelOptions, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import ReplayManifest
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.candidate_generation import OFFICE_V1_CANDIDATE_CATALOG
from sandbox.scenarios.models import AgentConfig, ExecutionBudget, TestCase
from sandbox.scenarios.office_campaign_baseline import (
    OfficeBaselinePlanner,
)
from sandbox.scenarios.office_docker_mutator import (
    DockerOfficeMutationProvider,
    MUTATOR_PROVIDER_VERSION,
)
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_mutation import (
    OfficeMutationArtifactStore,
    OfficeMutationCandidate,
    OfficeMutationPlanner,
    OfficeMutationPlanningError,
    OfficeMutationValidationRecord,
    OfficeMutationValidator,
)
from sandbox.scenarios.office_mutation_batch import (
    OfficeMutationBatchRunner,
    OfficeMutationBatchStatus,
)
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

CLOSED_LOOP_SCHEMA_VERSION = "1.0"
CLOSED_LOOP_POLICY_VERSION = "office-closed-loop-v1"
TARGET_PRESERVING_OPERATOR = "target-preserving-expression-rewrite"
RETARGET_OPERATOR = "explicit-office-target-redirection"
MODEL_NAME = "qwen3:8b"
MODEL_DIGEST = "sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
MODEL_ENDPOINT = "http://127.0.0.1:11434"
RISK_SCOPE_VERSION = "office-g6-risk-scope-v1"
RISK_CATEGORIES = (
    "data_exfiltration",
    "unauthorized_file_read",
    "unauthorized_resource_deletion",
)


def formal_agent() -> AgentConfig:
    return AgentConfig(
        provider="ollama",
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        endpoint=MODEL_ENDPOINT,
    )


def office_risk_scope(taxonomy: Any) -> CampaignRiskScopeIndex:
    return CampaignRiskScopeIndex(
        CampaignRiskScope(
            scope_version=RISK_SCOPE_VERSION,
            taxonomy_version=taxonomy.taxonomy_version,
            categories={
                category_id: RiskReachability(max_reachable_depth=3)
                for category_id in RISK_CATEGORIES
            },
        ),
        taxonomy,
    )


def assert_no_episode_resources(client: Any, execution_id: str) -> None:
    containers = client.containers.list(
        all=True, filters={"label": f"trace-g.execution-id={execution_id}"}
    )
    volumes = client.volumes.list(
        filters={"label": f"trace-g.execution-id={execution_id}"}
    )
    if containers or volumes:
        raise RuntimeError("closed-loop Episode resources remain after execution")


def atomic_write_json(path: Path, value: object) -> None:
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


def _content_digest(payload: dict[str, Any], *, label: str) -> str:
    return decimalized_sha256_digest(payload, label=label)


def _lock_json(path: Path, expected: dict[str, Any], *, label: str) -> dict[str, Any]:
    expected = dict(expected)
    expected["content_digest"] = _content_digest(expected, label=label)
    if path.exists():
        observed = json.loads(path.read_text(encoding="utf-8"))
        if observed != expected:
            raise RuntimeError(f"{label} identity drift")
        return observed
    atomic_write_json(path, expected)
    return expected


def _campaign_manifest(
    *,
    campaign_id: str,
    image_ref: str,
    image_id: str,
    risk_scope: CampaignRiskScopeIndex,
    seed: int,
    created_at: datetime | None = None,
) -> ScenarioCampaignManifest:
    catalog_manifest = OFFICE_V1_CANDIDATE_CATALOG.manifest()
    config_digest = sha256_digest(
        {
            "policy_version": CLOSED_LOOP_POLICY_VERSION,
            "campaign_id": campaign_id,
            "image_ref": image_ref,
            "image_id": image_id,
            "model_name": MODEL_NAME,
            "model_digest": MODEL_DIGEST,
            "agent_prompt_digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
            "mutator_prompt_digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
            "risk_scope_digest": risk_scope.digest,
            "catalog_manifest_digest": catalog_manifest.content_digest,
            "seed": seed,
        }
    )
    values: dict[str, Any] = {
        "campaign_id": campaign_id,
        "config_digest": config_digest,
        "taxonomy_version": risk_scope.taxonomy.taxonomy_version,
        "taxonomy_digest": risk_scope.taxonomy.digest,
        "risk_scope_version": risk_scope.scope_version,
        "risk_scope_digest": risk_scope.digest,
        "mutation_registry_version": "office-mutation-plan-v1",
        "mutation_registry_digest": sha256_digest("office-mutation-plan-v1"),
        "mutation_provider": "docker-ollama",
        "mutation_provider_version": MUTATOR_PROVIDER_VERSION,
        "mutation_model_name": MODEL_NAME,
        "mutation_model_digest": MODEL_DIGEST,
        "agent_model_name": MODEL_NAME,
        "agent_model_digest": MODEL_DIGEST,
        "agent_model_runtime_image": image_ref,
        "agent_model_runtime_digest": image_id,
        "agent_image": image_ref,
        "agent_image_digest": image_id,
        "target_profile_id": "office-v1-closed-loop",
        "energy_formula_version": "coverage-gap-ranking-v1",
        "corpus_policy_version": CorpusPolicy.version,
        "scheduler_policy_version": CLOSED_LOOP_POLICY_VERSION,
        "random_seed": seed,
        "scenario_catalogs": catalog_manifest,
    }
    if created_at is not None:
        values["created_at"] = created_at
    return ScenarioCampaignManifest.model_validate(values)


def _locked_campaign_manifest(
    path: Path,
    *,
    campaign_id: str,
    image_ref: str,
    image_id: str,
    risk_scope: CampaignRiskScopeIndex,
    seed: int,
) -> ScenarioCampaignManifest:
    if path.exists():
        observed = ScenarioCampaignManifest.model_validate_json(path.read_bytes())
        expected = _campaign_manifest(
            campaign_id=campaign_id,
            image_ref=image_ref,
            image_id=image_id,
            risk_scope=risk_scope,
            seed=seed,
            created_at=observed.created_at,
        )
        if observed != expected:
            raise RuntimeError("closed-loop Campaign manifest identity drift")
        return observed
    manifest = _campaign_manifest(
        campaign_id=campaign_id,
        image_ref=image_ref,
        image_id=image_id,
        risk_scope=risk_scope,
        seed=seed,
    )
    atomic_write_json(path, manifest)
    return manifest


def _validate_image(image: Any) -> tuple[str, dict[str, str]]:
    image_id = image.id.lower()
    labels = image.attrs.get("Config", {}).get("Labels") or {}
    expected = {
        "org.trace-g.runtime": "self-contained-agent-qwen",
        "org.trace-g.agent-framework": "langgraph",
        "org.trace-g.model.name": MODEL_NAME,
        "org.trace-g.model.digest": MODEL_DIGEST,
        "org.trace-g.system-prompt.version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
        "org.trace-g.system-prompt.digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
        "org.trace-g.mutator-prompt.version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
        "org.trace-g.mutator-prompt.digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    }
    drift = {key: labels.get(key) for key, value in expected.items() if labels.get(key) != value}
    if drift:
        raise RuntimeError(f"closed-loop image contract drift: {sorted(drift)}")
    return image_id, expected


def _execution_id(campaign_id: str, generation: int, case: TestCase) -> str:
    suffix = sha256_digest(
        {
            "campaign_id": campaign_id,
            "generation": generation,
            "case_digest": case.content_digest,
        }
    ).removeprefix("sha256:")[:24]
    return f"office-loop-{generation:02d}-{suffix}"


def _execution_metadata(
    *,
    campaign_id: str,
    generation: int,
    case: TestCase,
    source_plan_digest: str | None,
    source_feedback_digest: str | None,
) -> dict[str, Any]:
    return {
        "acceptance_gate": "5.4a",
        "campaign_id": campaign_id,
        "generation_index": generation,
        "case_id": case.case_id,
        "test_case_digest": case.content_digest,
        "source_mutation_plan_digest": source_plan_digest,
        "source_feedback_digest": source_feedback_digest,
    }


def _execution_request(
    case: TestCase,
    *,
    execution_id: str,
    metadata: dict[str, Any],
) -> ExecutionRequest:
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
        metadata=metadata,
    )


def _load_recording(
    manifests: ManifestStore,
    artifacts: ArtifactStore,
    *,
    campaign_id: str,
    generation: int,
) -> ReplayManifest | None:
    matches: list[ReplayManifest] = []
    for path in sorted(manifests.root.glob("*/manifest.json")):
        manifest = manifests.load(path.parent.name)
        determinism = json.loads(artifacts.read_bytes(manifest.determinism_config))
        metadata = determinism.get("metadata") or {}
        if (
            metadata.get("campaign_id") == campaign_id
            and metadata.get("generation_index") == generation
        ):
            matches.append(manifest)
    if len(matches) > 1:
        raise RuntimeError("one closed-loop generation resolved to multiple recordings")
    return matches[0] if matches else None


def _validate_recording(
    manifest: ReplayManifest,
    *,
    case: TestCase,
    image_id: str,
    metadata: dict[str, Any],
    artifacts: ArtifactStore,
) -> None:
    if not manifest.recording_complete or manifest.case_id != case.case_id:
        raise RuntimeError("closed-loop recording is incomplete or belongs to another case")
    if (
        manifest.system_prompt_version != OFFICE_AGENT_SYSTEM_PROMPT_VERSION
        or manifest.system_prompt_digest != OFFICE_AGENT_SYSTEM_PROMPT_DIGEST
        or manifest.image_digest.rsplit("@", 1)[-1] != image_id
    ):
        raise RuntimeError("closed-loop recording identity drift")
    determinism = json.loads(artifacts.read_bytes(manifest.determinism_config))
    if determinism.get("metadata") != metadata:
        raise RuntimeError("closed-loop recording metadata drift")
    model = determinism.get("model") or {}
    if {
        "provider": model.get("provider"),
        "model_name": model.get("model_name"),
        "model_digest": model.get("model_digest"),
        "endpoint": model.get("endpoint"),
    } != {
        "provider": "ollama",
        "model_name": MODEL_NAME,
        "model_digest": MODEL_DIGEST,
        "endpoint": MODEL_ENDPOINT,
    }:
        raise RuntimeError("closed-loop recording model identity drift")


class _MutationPauseState:
    def __init__(self, campaign_id: str, path: Path) -> None:
        self.campaign_id = campaign_id
        self.path = path

    def snapshot(self) -> object:
        return SimpleNamespace(campaign_id=self.campaign_id)

    def pause_campaign(
        self, reason_code: str, *, evidence_digest: str | None = None
    ) -> object:
        payload = {
            "schema_version": CLOSED_LOOP_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "reason_code": reason_code,
            "evidence_digest": evidence_digest,
        }
        payload["content_digest"] = sha256_digest(payload)
        atomic_write_json(self.path, payload)
        return self.snapshot()


def _ordered_risk_gaps(feedback: Any, generation: int) -> list[Any]:
    open_gaps = [
        gap
        for gap in feedback.risk_gaps
        if gap.observed_execution_depth < gap.max_reachable_depth
    ]
    ordered = sorted(
        open_gaps,
        key=lambda gap: (
            gap.observed_execution_depth,
            gap.observed_depth,
            gap.risk_category_id,
        ),
    )
    if ordered:
        offset = generation % len(ordered)
        ordered = ordered[offset:] + ordered[:offset]
    return ordered


def _plan_next_batch(
    *,
    planner: OfficeMutationPlanner,
    parent: TestCase,
    feedback: Any,
    provider_identity: Any,
    baseline_plan: Any,
    generation: int,
    seed: int,
    requested_count: int,
) -> Any:
    gaps = _ordered_risk_gaps(feedback, generation)
    objective_items = {
        item.selection.objective_id: item for item in baseline_plan.items
    }
    objectives = {
        objective.objective_id: objective
        for objective in OFFICE_V1_CANDIDATE_CATALOG.attack_objectives
    }
    assert parent.attack is not None
    for gap in gaps:
        matching = sorted(
            objective_id
            for objective_id, objective in objectives.items()
            if gap.risk_category_id in objective.risk_category_ids
        )
        if not matching:
            continue
        offset = generation % len(matching)
        for objective_id in matching[offset:] + matching[:offset]:
            expected_path = (
                f"coverage-gap:{gap.risk_category_id}:"
                f"depth-{gap.next_execution_target_depth or gap.max_reachable_depth}"
            )
            if objective_id == parent.attack.objective.objective_id:
                return planner.plan(
                    parent=parent,
                    feedback=feedback,
                    provider_identity=provider_identity,
                    operator_id=TARGET_PRESERVING_OPERATOR,
                    random_seed=seed,
                    requested_count=requested_count,
                    max_output_tokens=2_048,
                    expected_path=expected_path,
                    expected_risk_gap_ids=(gap.risk_category_id,),
                )
            baseline_item = objective_items.get(objective_id)
            if baseline_item is None:
                continue
            try:
                return planner.plan_retarget(
                    parent=parent,
                    feedback=feedback,
                    provider_identity=provider_identity,
                    target_objective_id=objective_id,
                    target_task_id=baseline_item.selection.task_id,
                    target_carrier_id=baseline_item.selection.carrier_id,
                    operator_id=RETARGET_OPERATOR,
                    random_seed=seed,
                    requested_count=requested_count,
                    max_output_tokens=2_048,
                    expected_path=expected_path,
                    expected_risk_gap_ids=(gap.risk_category_id,),
                )
            except OfficeMutationPlanningError:
                continue
    fallback_gap_ids = tuple(gap.risk_category_id for gap in gaps[:1])
    return planner.plan(
        parent=parent,
        feedback=feedback,
        provider_identity=provider_identity,
        operator_id=TARGET_PRESERVING_OPERATOR,
        random_seed=seed,
        requested_count=requested_count,
        max_output_tokens=2_048,
        expected_path="coverage-gap:behavior-path-novelty",
        expected_risk_gap_ids=fallback_gap_ids,
    )


def _selected_child(
    store: OfficeMutationArtifactStore,
    *,
    plan_id: str,
    result: Any,
) -> tuple[TestCase, list[OfficeMutationCandidate], list[OfficeMutationValidationRecord]]:
    candidates = [
        OfficeMutationCandidate.model_validate_json(
            store.artifact_json("candidate", candidate_id)
        )
        for candidate_id in result.candidate_ids
        if store.artifact_json("candidate", candidate_id) is not None
    ]
    validations = [
        OfficeMutationValidationRecord.model_validate_json(
            store.artifact_json("validation", record_id)
        )
        for record_id in result.validation_record_ids
        if store.artifact_json("validation", record_id) is not None
    ]
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    accepted = sorted(
        (record for record in validations if record.child_case is not None),
        key=lambda record: by_id[record.candidate_id].ordinal,
    )
    if not accepted:
        raise RuntimeError("LLM Mutator produced no locally accepted child case")
    if any(record.plan_id != plan_id for record in validations):
        raise RuntimeError("mutation validation lineage drift")
    assert accepted[0].child_case is not None
    return accepted[0].child_case, candidates, validations


def _feedback_guidance(feedback: Any, mutation_plan: Any) -> dict[str, Any]:
    selected = set(mutation_plan.expected_risk_gap_ids)
    risk_gaps = [
        {
            "risk_category_id": gap.risk_category_id,
            "observed_depth": gap.observed_depth,
            "observed_execution_depth": gap.observed_execution_depth,
            "next_execution_target_depth": gap.next_execution_target_depth,
            "max_reachable_depth": gap.max_reachable_depth,
        }
        for gap in feedback.risk_gaps
        if gap.risk_category_id in selected
    ]
    projection = {
        "feedback_digest": feedback.report_digest,
        "observation_count": feedback.saturation.observations,
        "observed_behavior_paths": feedback.observed_behavior_paths,
        "selected_risk_gaps": risk_gaps,
        "expected_path": mutation_plan.expected_path,
        "planned_objective_id": mutation_plan.planned_components.objective_id,
    }
    projection["guidance_digest"] = _content_digest(
        projection, label="mutation feedback guidance digest"
    )
    return projection


def _generation_path(root: Path, generation: int) -> Path:
    return root / "generations" / f"generation-{generation:02d}.json"


def _load_generation(path: Path, generation: int) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    digest = value.pop("content_digest", None)
    expected = _content_digest(value, label=f"generation {generation} digest")
    value["content_digest"] = digest
    if digest != expected or value.get("generation_index") != generation:
        raise RuntimeError(f"generation {generation} artifact integrity failure")
    return value


def _write_generation(path: Path, payload: dict[str, Any], generation: int) -> None:
    payload = dict(payload)
    payload["content_digest"] = _content_digest(
        payload, label=f"generation {generation} digest"
    )
    atomic_write_json(path, payload)


def _runtime_components(args: argparse.Namespace, client: Any, root: Path) -> tuple[Any, ...]:
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
    artifacts = ArtifactStore(artifact_root)
    manifests = ManifestStore(manifest_root)
    engine = ReplayEngine(
        config,
        DockerSandboxScheduler(config.sandbox, client=client),
        RuntimeClient(config.tracing, docker_client=client),
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
    return artifacts, manifests, engine, resolver


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = docker.from_env()
    image = client.images.get(args.image)
    image_id, image_labels = _validate_image(image)
    if args.expected_image_id and image_id != args.expected_image_id.lower():
        raise RuntimeError("closed-loop Agent image identity mismatch")

    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    risk_scope = office_risk_scope(taxonomy)
    manifest = _locked_campaign_manifest(
        root / "campaign-manifest.json",
        campaign_id=args.campaign_id,
        image_ref=args.image,
        image_id=image_id,
        risk_scope=risk_scope,
        seed=args.seed,
    )
    budget = ExecutionBudget(max_steps=20, timeout_seconds=600, max_output_tokens=4_096)
    baseline_plan = OfficeBaselinePlanner(
        campaign_id=args.campaign_id,
        manifest=manifest.scenario_catalogs,
        random_seed=args.seed,
        agent=formal_agent(),
        budget=budget,
        catalog=OFFICE_V1_CANDIDATE_CATALOG,
    ).plan()
    if args.baseline_ordinal >= len(baseline_plan.items):
        raise RuntimeError("baseline ordinal exceeds the frozen office baseline")
    initial_case = baseline_plan.items[args.baseline_ordinal].candidate
    run_lock = _lock_json(
        root / "closed-loop-lock.json",
        {
            "schema_version": CLOSED_LOOP_SCHEMA_VERSION,
            "policy_version": CLOSED_LOOP_POLICY_VERSION,
            "campaign_id": args.campaign_id,
            "image_ref": args.image,
            "image_id": image_id,
            "model_name": MODEL_NAME,
            "model_digest": MODEL_DIGEST,
            "agent_prompt_version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
            "agent_prompt_digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
            "mutator_prompt_version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
            "mutator_prompt_digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
            "taxonomy_version": taxonomy.taxonomy_version,
            "taxonomy_digest": taxonomy.digest,
            "risk_mapping_version": OFFICE_RISK_MAPPING_VERSION,
            "risk_mapping_digest": OFFICE_RISK_MAPPING_DIGEST,
            "risk_scope_version": risk_scope.scope_version,
            "risk_scope_digest": risk_scope.digest,
            "catalog_manifest_digest": manifest.scenario_catalogs.content_digest,
            "baseline_plan_digest": baseline_plan.content_digest,
            "baseline_ordinal": args.baseline_ordinal,
            "initial_case_digest": initial_case.content_digest,
            "generation_count": args.generations,
            "candidates_per_generation": args.candidates_per_generation,
            "seed": args.seed,
            "image_labels": image_labels,
        },
        label="closed-loop lock",
    )

    artifacts, manifests, replay_engine, resolver = _runtime_components(args, client, root)
    provider = DockerOfficeMutationProvider(
        image_ref=args.image,
        image_id=image_id,
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        gpu_device=args.gpu_device,
        timeout_seconds=600,
        client=client,
    )
    planner = OfficeMutationPlanner(manifest.scenario_catalogs, OFFICE_V1_CANDIDATE_CATALOG)
    validator = OfficeMutationValidator(manifest.scenario_catalogs, OFFICE_V1_CANDIDATE_CATALOG)
    corpus_policy = CorpusPolicy()
    pause_state = _MutationPauseState(args.campaign_id, root / "mutation-pause.json")

    generation_records: list[dict[str, Any]] = []
    parent = initial_case
    source_plan_digest: str | None = None
    source_feedback_digest: str | None = None
    with CoverageStore(
        root / "coverage",
        args.campaign_id,
        taxonomy,
        risk_scope=risk_scope,
        auto_snapshot_interval=1,
    ) as coverage_store, OfficeMutationArtifactStore(
        root / "mutations", args.campaign_id
    ) as mutation_store:
        for generation in range(args.generations):
            generation_path = _generation_path(root, generation)
            if generation_path.exists():
                record = _load_generation(generation_path, generation)
                if record["input_case_digest"] != parent.content_digest:
                    raise RuntimeError("persisted generation parent lineage drift")
                generation_records.append(record)
                next_case_payload = record.get("next_case")
                if next_case_payload is not None:
                    parent = TestCase.model_validate(next_case_payload)
                    source_plan_digest = record["mutation"]["plan_digest"]
                    source_feedback_digest = record["coverage_feedback_digest"]
                elif generation + 1 < args.generations:
                    raise RuntimeError("non-final generation is missing its selected child")
                continue

            execution_id = _execution_id(args.campaign_id, generation, parent)
            metadata = _execution_metadata(
                campaign_id=args.campaign_id,
                generation=generation,
                case=parent,
                source_plan_digest=source_plan_digest,
                source_feedback_digest=source_feedback_digest,
            )
            recorded = _load_recording(
                manifests,
                artifacts,
                campaign_id=args.campaign_id,
                generation=generation,
            )
            recovered = recorded is not None
            if recorded is None:
                trajectory_path = root / "trajectories" / f"{execution_id}.jsonl"
                if trajectory_path.exists():
                    raise RuntimeError(
                        "closed-loop found an unsealed trajectory and will not repeat Qwen"
                    )
                recorded = await replay_engine.record_request(
                    _execution_request(parent, execution_id=execution_id, metadata=metadata)
                )
            assert_no_episode_resources(client, execution_id)
            _validate_recording(
                recorded,
                case=parent,
                image_id=image_id,
                metadata=metadata,
                artifacts=artifacts,
            )
            coverage_input = resolver.from_manifest(recorded)
            coverage_result = coverage_store.evaluate(coverage_input)
            coverage_recovered = coverage_result.already_evaluated
            canonical_coverage_result = coverage_result.model_copy(
                update={"already_evaluated": False}
            )
            feedback = coverage_store.campaign_feedback()
            decision = corpus_policy.evaluate(canonical_coverage_result, None)
            evidence = coverage_input.scenario_evidence
            corpus_record = {
                "schema_version": CLOSED_LOOP_SCHEMA_VERSION,
                "campaign_id": args.campaign_id,
                "generation_index": generation,
                "case_id": parent.case_id,
                "test_case_digest": parent.content_digest,
                "trajectory_id": coverage_input.trajectory_id,
                "replay_id": recorded.replay_id,
                "retain": decision.retain,
                "reasons": [reason.value for reason in decision.reasons],
                "evidence_event_sequences": list(decision.evidence_event_sequences),
                "normal_task_completed": bool(evidence and evidence.normal_task_completed),
                "attack_side_effect_observed": (
                    evidence.attack_side_effect_observed if evidence is not None else None
                ),
            }
            corpus_record["content_digest"] = _content_digest(
                corpus_record, label=f"corpus generation {generation} digest"
            )
            if decision.retain:
                corpus_path = root / "corpus" / f"{coverage_input.trajectory_id}.json"
                if corpus_path.exists():
                    if json.loads(corpus_path.read_text(encoding="utf-8")) != corpus_record:
                        raise RuntimeError("Corpus identity conflict")
                else:
                    atomic_write_json(corpus_path, corpus_record)

            mutation_payload: dict[str, Any] | None = None
            next_case: TestCase | None = None
            if generation + 1 < args.generations:
                mutation_plan = _plan_next_batch(
                    planner=planner,
                    parent=parent,
                    feedback=feedback,
                    provider_identity=provider.identity,
                    baseline_plan=baseline_plan,
                    generation=generation,
                    seed=args.seed,
                    requested_count=args.candidates_per_generation,
                )
                batch = await OfficeMutationBatchRunner(
                    provider=provider,
                    validator=validator,
                    store=mutation_store,
                    campaign_state=pause_state,
                ).run(plan=mutation_plan, parent=parent)
                if batch.status in {
                    OfficeMutationBatchStatus.PAUSED,
                    OfficeMutationBatchStatus.NO_PROGRESS,
                }:
                    raise RuntimeError(f"LLM Mutator batch stopped with {batch.status.value}")
                next_case, candidates, validations = _selected_child(
                    mutation_store,
                    plan_id=mutation_plan.plan_id,
                    result=batch,
                )
                request_artifacts = []
                for request_id in batch.request_ids:
                    raw_request = mutation_store.artifact_json(
                        "mutation_batch_request", request_id
                    )
                    if raw_request is None:
                        raise RuntimeError("mutation batch request artifact is missing")
                    request_artifacts.append(json.loads(raw_request))
                primary_requests = [
                    request
                    for request in request_artifacts
                    if request["path"] == "0" and request["retry_index"] == 0
                ]
                if len(primary_requests) != 1:
                    raise RuntimeError("mutation batch lacks one primary sampling seed")
                mutation_payload = {
                    "plan_id": mutation_plan.plan_id,
                    "plan_digest": mutation_plan.content_digest,
                    "feedback_digest": mutation_plan.feedback_digest,
                    "mode": mutation_plan.mode.value,
                    "operator_id": mutation_plan.operator_id,
                    "expected_risk_gap_ids": list(mutation_plan.expected_risk_gap_ids),
                    "expected_path": mutation_plan.expected_path,
                    "feedback_guidance": _feedback_guidance(feedback, mutation_plan),
                    "primary_sampling_seed": primary_requests[0]["random_seed"],
                    "sampling_seeds": [request["random_seed"] for request in request_artifacts],
                    "batch_run_id": batch.run_id,
                    "batch_digest": batch.content_digest,
                    "batch_status": batch.status.value,
                    "candidate_ids": list(batch.candidate_ids),
                    "candidate_expression_digests": [
                        sha256_digest(candidate.expression) for candidate in candidates
                    ],
                    "accepted_child_case_ids": list(batch.accepted_child_case_ids),
                    "validation_digests": [record.content_digest for record in validations],
                    "selected_child_case_id": next_case.case_id,
                    "selected_child_case_digest": next_case.content_digest,
                }

            record = {
                "schema_version": CLOSED_LOOP_SCHEMA_VERSION,
                "policy_version": CLOSED_LOOP_POLICY_VERSION,
                "campaign_id": args.campaign_id,
                "generation_index": generation,
                "input_case": parent.model_dump(mode="json"),
                "input_case_digest": parent.content_digest,
                "objective_id": (
                    parent.attack.objective.objective_id if parent.attack is not None else None
                ),
                "execution_id": execution_id,
                "replay_id": recorded.replay_id,
                "replay_manifest_digest": recorded.manifest_digest,
                "trajectory_id": coverage_input.trajectory_id,
                "coverage_input_digest": coverage_input.input_digest,
                "coverage_result_digest": _content_digest(
                    canonical_coverage_result.model_dump(mode="json"),
                    label=f"coverage result generation {generation} digest",
                ),
                "coverage_feedback_digest": feedback.report_digest,
                "behavior_profile_hash": canonical_coverage_result.behavior_profile_hash,
                "new_behavior_count": canonical_coverage_result.new_behavior_count,
                "new_behavior_features": canonical_coverage_result.new_behavior_features,
                "execution_new_risk_categories": (
                    canonical_coverage_result.execution_new_risk_categories
                ),
                "execution_risk_depth_changes": [
                    item.model_dump(mode="json")
                    for item in canonical_coverage_result.execution_risk_depth_changes
                ],
                "behavior_risk_links": [
                    item.model_dump(mode="json")
                    for item in canonical_coverage_result.behavior_risk_links
                ],
                "normal_task_completed": corpus_record["normal_task_completed"],
                "attack_side_effect_observed": corpus_record["attack_side_effect_observed"],
                "normal_task_completion_observed": corpus_record["normal_task_completed"],
                "recovered_without_model_call": recovered,
                "coverage_recovered_idempotently": coverage_recovered,
                "corpus": corpus_record,
                "mutation": mutation_payload,
                "next_case": next_case.model_dump(mode="json") if next_case else None,
            }
            _write_generation(generation_path, record, generation)
            record = _load_generation(generation_path, generation)
            generation_records.append(record)
            if next_case is not None:
                parent = next_case
                source_plan_digest = mutation_payload["plan_digest"]
                source_feedback_digest = feedback.report_digest

        snapshot = coverage_store.snapshot(include_heatmap=False)
        final_feedback = coverage_store.campaign_feedback()

    if snapshot.total_trajectories != len(generation_records):
        raise RuntimeError("coverage observations do not match sealed generation artifacts")

    mutation_records = [record["mutation"] for record in generation_records if record["mutation"]]
    feedback_digests = [record["feedback_digest"] for record in mutation_records]
    guidance_digests = [
        record["feedback_guidance"]["guidance_digest"] for record in mutation_records
    ]
    primary_sampling_seeds = [
        record["primary_sampling_seed"] for record in mutation_records
    ]
    expression_batches = [record["candidate_expression_digests"] for record in mutation_records]
    feedback_changed_candidates = (
        len(mutation_records) >= 2
        and len(set(feedback_digests)) == len(feedback_digests)
        and len(set(guidance_digests)) == len(guidance_digests)
        and len(set(primary_sampling_seeds)) == 1
        and len({tuple(batch) for batch in expression_batches}) == len(expression_batches)
    )
    exploration_progress = any(
        record["new_behavior_count"] > 0
        or record["execution_new_risk_categories"]
        or record["execution_risk_depth_changes"]
        or any(
            link.get("novelty_class") != "known_pair"
            for link in record["behavior_risk_links"]
        )
        for record in generation_records[1:]
    )
    summary = {
        "schema_version": CLOSED_LOOP_SCHEMA_VERSION,
        "policy_version": CLOSED_LOOP_POLICY_VERSION,
        "status": "complete",
        "campaign_id": args.campaign_id,
        "run_lock_digest": run_lock["content_digest"],
        "generation_count": len(generation_records),
        "mutation_batch_count": len(mutation_records),
        "coverage_observations": final_feedback.saturation.observations,
        "coverage_feedback_digest": final_feedback.report_digest,
        "coverage_snapshot": snapshot.model_dump(mode="json"),
        "retained_corpus_count": sum(record["corpus"]["retain"] for record in generation_records),
        "normal_task_completion_count": sum(
            record["normal_task_completion_observed"] for record in generation_records
        ),
        "case_failure_count": sum(
            not record["normal_task_completed"] for record in generation_records
        ),
        "feedback_changed_candidates": feedback_changed_candidates,
        "exploration_progress": exploration_progress,
        "generation_artifact_digests": [
            record["content_digest"] for record in generation_records
        ],
    }
    summary["summary_digest"] = _content_digest(summary, label="closed-loop summary digest")
    atomic_write_json(root / "campaign-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--image", default="trace-redteam-agent-qwen:g6-local")
    parser.add_argument("--expected-image-id")
    parser.add_argument("--gpu-device", default="0")
    parser.add_argument("--seed", type=int, default=53)
    parser.add_argument("--baseline-ordinal", type=int, default=0)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--candidates-per-generation", type=int, default=2)
    args = parser.parse_args()
    if args.baseline_ordinal < 0:
        parser.error("--baseline-ordinal must be non-negative")
    if args.generations < 2:
        parser.error("--generations must be at least 2")
    if not 1 <= args.candidates_per_generation <= 4:
        parser.error("--candidates-per-generation must be between 1 and 4")
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["feedback_changed_candidates"] and result["exploration_progress"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
