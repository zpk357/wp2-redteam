"""Host entry for structured-scenario episodes (I1): freeze, normal control, G1, two arms.

Every stage goes through the same path: frozen fixture -> candidate -> envelope -> container
(`StructuredV1Adapter`) -> retrieved and re-verified bundle -> host finalization -> Oracle and
coverage -> search.  Nothing here calls a model by itself: the model identity and the endpoint
are command-line inputs, and the text generator is only wired up when one is passed.

Examples
--------
Freeze the fixture for the image build::

    python scripts/run_structured_v1_episode.py freeze \\
        --output data/structured-v1/freeze-manifest.json

Then build the image the freeze manifest belongs to (the build context needs the file)::

    docker build -f agent_image/Dockerfile \\
        --build-arg STRUCTURED_V1_FREEZE_MANIFEST=data/structured-v1/freeze-manifest.json \\
        -t structured-v1:local .

One normal-control episode with the scripted test model (no model server, real container and
real Office tools)::

    python scripts/run_structured_v1_episode.py normal-control \\
        --image structured-v1:local --model-provider fake --data-root data/structured-v1

The probe (k repeats per frozen candidate) and the two arms::

    python scripts/run_structured_v1_episode.py g1 --image structured-v1:local --repeats 5 ...
    python scripts/run_structured_v1_episode.py two-arm --image structured-v1:local \\
        --arm coverage_guided --opportunities 20 --model-provider ollama \\
        --model-name qwen3.5:27b-q4_K_M --endpoint http://127.0.0.1:11434 \\
        --text-provider ollama --text-model qwen3.5:27b-q4_K_M --text-endpoint http://127.0.0.1:11434
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig
from sandbox.protocol import (
    ModelOptions,
    ModelProvider,
    seal_model_inference_options,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.structured_v1.acceptance import judge_normal_control
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
    save_checkpoint,
)
from sandbox.structured_v1.candidates import build_g1_candidates
from sandbox.structured_v1.coverage import extract_coverage
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.generation import GenerationBudget
from sandbox.structured_v1.host_runner import StructuredHostRunner
from sandbox.structured_v1.mbr import MBRStep, levenshtein, project_mbr
from sandbox.structured_v1.obligations import ObligationId
from sandbox.structured_v1.oracle_io import judge_artifacts
from sandbox.structured_v1.search import ArmKind, TwoArmSearch
from sandbox.structured_v1.text_provider import HttpJsonTextProvider, TextProviderOptions

DEFAULT_FIXTURE = "summary-delivery-a"
SCRIPTED_MODEL = "structured-v1-scripted"
#: The normal control's calibration configuration (2026-09-22, user-approved scope): the first
#: Episode has to discover the material *and* deliver, and the campaign's per-episode budget of 8
#: was spent on discovery alone. This override applies to `normal-control` only - G1 and the two
#: arms keep the campaign budget, so a passed normal control does not silently widen them.
NORMAL_CONTROL_CALIBRATION_CALLS = 12
#: Exit code for "the episode ran, and it did not earn the gate": distinct from a crash.
NORMAL_CONTROL_NOT_ACCEPTED = 3


def _budget(args: argparse.Namespace) -> RunBudgetEnvelope:
    return RunBudgetEnvelope(
        max_model_calls=args.max_model_calls,
        max_tool_calls=args.max_tool_calls,
        max_wall_clock_seconds=args.wall_clock_seconds,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_expense_units=args.max_expense_units,
    )


def _model_options(args: argparse.Namespace) -> ModelOptions:
    if args.model_provider == "fake":
        return ModelOptions(provider=ModelProvider.FAKE, model_name=SCRIPTED_MODEL)
    return ModelOptions(
        provider=ModelProvider.OLLAMA,
        model_name=args.model_name,
        endpoint=args.endpoint,
        timeout_seconds=args.model_timeout_seconds,
        inference=seal_model_inference_options(
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
            thinking=False,
            source_config_digest="sha256:" + "0" * 64,
        ),
    )


def _runner(args: argparse.Namespace) -> StructuredHostRunner:
    fixture = load_fixture(args.fixture)
    config = SandboxConfig(
        image=args.image,
        network_mode="none" if args.model_provider == "fake" else args.network_mode,
        startup_timeout_seconds=args.startup_timeout_seconds,
        limits=SandboxLimits(),
    )
    return StructuredHostRunner(
        scheduler=DockerSandboxScheduler(config),
        runtime=RuntimeClient(TraceConfig(output_dir=args.data_root / "traces")),
        failure_directory=args.data_root / "diagnostics",
        manifest=fixture.manifest,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        image=args.image,
        model=_model_options(args),
        default_budget=_budget(args),
        execution_timeout_seconds=args.execution_timeout_seconds,
        arm_id=args.arm,
    )


def _text_provider(args: argparse.Namespace):
    if args.text_provider == "none":
        return None
    return HttpJsonTextProvider(
        TextProviderOptions(
            provider_id="ollama-chat",
            model_name=args.text_model,
            endpoint=args.text_endpoint,
            timeout_seconds=args.text_timeout_seconds,
            num_ctx=args.num_ctx,
            num_predict=args.text_num_predict,
            temperature=0.0,
            top_p=0.9,
            top_k=40,
        )
    )


def _generation_budget(args: argparse.Namespace) -> GenerationBudget:
    return GenerationBudget(
        requests_per_opportunity=args.mutation_requests,
        max_input_tokens_per_request=args.mutation_input_tokens,
        max_output_tokens_per_request=args.mutation_output_tokens,
    )


def _episode_record(finalized, manifest) -> dict:
    bundle = finalized.container_bundle
    judgment = judge_artifacts(finalized.artifacts(), manifest=manifest)
    coverage = extract_coverage(bundle, manifest=manifest, artifacts=finalized.artifacts())
    return {
        "episode_id": bundle.episode_id,
        "bundle_digest": bundle.bundle_digest,
        "final_bundle_digest": finalized.final_bundle_digest,
        "complete": finalized.complete,
        "missing": list(finalized.missing),
        "stop_reason": bundle.stop_reason.value,
        "model_calls": bundle.usage.model_calls,
        "tool_calls": bundle.usage.tool_calls,
        "usage_complete": bundle.usage.usage_complete,
        "outcomes": {
            ObligationId.DATA_RELEASE.value: judgment.outcome(
                ObligationId.DATA_RELEASE
            ).value,
            ObligationId.AUDIENCE_SCOPE.value: judgment.outcome(
                ObligationId.AUDIENCE_SCOPE
            ).value,
        },
        "findings": list(judgment.unique_violation_keys()),
        "behavior_atoms": len(coverage.behavior),
        "risk_atoms": len(coverage.risk),
        "joint_atoms": len(coverage.joint),
        "mbr": [step.model_dump(mode="json") for step in project_mbr(bundle, manifest)],
    }


def _write_record(data_root: Path, record: dict) -> None:
    directory = data_root / "records"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['episode_id']}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stage_normal_control(args: argparse.Namespace) -> int:
    fixture = load_fixture(args.fixture)
    runner = _runner(args)
    inputs = prepare_inputs(fixture.manifest, seed=args.seed, count=1)
    model_seed = int(sha256_digest({"normal_control_seed": args.seed})[7:15], 16)
    model_seed = model_seed % 2_147_483_647 + 1
    finalized = runner.run_case(
        inputs.normal_control.case, episode_id="episode-0001", seed=model_seed
    )
    path = runner.write(finalized, args.data_root / "finalized")
    verdict = judge_normal_control(
        finalized, manifest=fixture.manifest, overlay=fixture.overlay
    )
    record = _episode_record(finalized, fixture.manifest)
    record["paired_seed"] = args.seed
    record["model_seed"] = model_seed
    record["acceptance"] = verdict.as_dict()
    _write_record(args.data_root, record)
    print(
        json.dumps(
            {"finalized_bundle": str(path), "record": record}, ensure_ascii=False, indent=2
        )
    )
    if not verdict.accepted:
        # The gate, not a warning: an episode that only got blocked is a failure, and a campaign
        # that starts from it would be measuring nothing.
        failed = ", ".join(item.criterion_id for item in verdict.failed())
        print(f"normal control NOT ACCEPTED: {failed}", file=sys.stderr)
        return NORMAL_CONTROL_NOT_ACCEPTED
    return 0


def _stage_g1(args: argparse.Namespace) -> int:
    fixture = load_fixture(args.fixture)
    runner = _runner(args)
    candidates = build_g1_candidates(fixture.manifest, seed=args.candidate_seed)
    records: list[dict] = []
    directory = args.data_root / "finalized"
    for candidate_id, case in sorted(candidates.cases.items()):
        for repeat in range(args.repeats):
            episode_id = f"{candidate_id}-k{repeat + 1:02d}"
            finalized = runner.run_case(case, episode_id=episode_id)
            runner.write(finalized, directory)
            record = _episode_record(finalized, fixture.manifest)
            record.update({"candidate_id": candidate_id, "repeat": repeat + 1})
            records.append(record)
            print(f"{episode_id}: {record['outcomes']}", file=sys.stderr)
    _write_g1_summary(args.data_root, candidates, records)
    return 0


def _write_g1_summary(data_root: Path, candidates, records: Sequence[dict]) -> None:
    """The probe's descriptive separation: distances within a candidate vs across children."""

    by_candidate: dict[str, list[tuple]] = {}
    for record in records:
        by_candidate.setdefault(record["candidate_id"], []).append(
            tuple(MBRStep.model_validate(item) for item in record["mbr"])
        )

    def _pairs(values: list[tuple]) -> list[int]:
        return [
            levenshtein(values[i], values[j])
            for i in range(len(values))
            for j in range(i + 1, len(values))
        ]

    summary = {
        "fixture_id": DEFAULT_FIXTURE,
        "candidate_seed": candidates.frozen.seed,
        "candidates": {},
        "parents": {},
    }
    for candidate_id, runs in sorted(by_candidate.items()):
        summary["candidates"][candidate_id] = {
            "repeats": len(runs),
            "within": _pairs(runs),
        }
    for parent, children in candidates.frozen.parents().items():
        across: list[int] = []
        for index, child in enumerate(children):
            for other in children[index + 1 :]:
                for left in by_candidate.get(child, []):
                    for right in by_candidate.get(other, []):
                        across.append(levenshtein(left, right))
        within = [
            value
            for child in children
            for value in summary["candidates"].get(child, {}).get("within", [])
        ]
        summary["parents"][parent] = {
            "children": list(children),
            "between": sorted(across),
            "within": sorted(within),
            "min_between": min(across) if across else None,
            "max_within": max(within) if within else None,
        }
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "g1-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stage_two_arm(args: argparse.Namespace) -> int:
    fixture = load_fixture(args.fixture)
    if fixture.manifest.session_protocol is not None:
        fixture.manifest.session_protocol.budgets(_budget(args))
        if (args.data_root / "checkpoint.json").exists() and not args.resume:
            raise ValueError("two-stage campaign refuses to overwrite an existing checkpoint")
    runner = _runner(args)
    runner.arm_id = args.arm
    provider = _text_provider(args)
    budget = _generation_budget(args)
    if provider is None and args.model_provider != "fake" and not args.allow_deterministic_text:
        print(
            "refusing: a real-model two-arm run needs a text provider "
            "(--text-provider ollama) or an explicit --allow-deterministic-text",
            file=sys.stderr,
        )
        return 2
    inputs = prepare_inputs(fixture.manifest, count=args.parents)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    checkpoint_path = args.data_root / "checkpoint.json"
    search = TwoArmSearch(
        parents,
        fixture.manifest,
        seed=f"{args.seed}:{args.arm}",
        provider=provider,
        generation_budget=budget if provider is not None else None,
    )
    checkpoint = (
        load_checkpoint(checkpoint_path)
        if args.resume and checkpoint_path.exists()
        else CampaignCheckpoint(limits=_limits(args))
    )
    if fixture.manifest.session_protocol is not None and checkpoint.limits != _limits(args):
        raise ValueError("two-stage resume requires the original campaign limits")
    if fixture.manifest.session_protocol is not None:
        runner.episode_counter = itertools.count(checkpoint.usage.opportunities + 1)
    planned = CampaignUsage(
        model_calls=args.max_model_calls,
        tool_calls=args.max_tool_calls,
        input_tokens=args.max_input_tokens,
        output_tokens=args.max_output_tokens,
        mutator_calls=budget.requests_per_opportunity if provider else 0,
        mutator_input_tokens=(
            budget.max_input_tokens_per_request * budget.requests_per_opportunity
            if provider
            else 0
        ),
        mutator_output_tokens=(
            budget.max_output_tokens_per_request * budget.requests_per_opportunity
            if provider
            else 0
        ),
    )
    arm = ArmKind(args.arm)
    directory = args.data_root / "finalized"
    remaining = (
        args.opportunities - checkpoint.usage.opportunities
        if fixture.manifest.session_protocol is not None else args.opportunities
    )
    for _ in range(remaining):
        if checkpoint.stopped:
            print(f"stopped: {checkpoint.stop_reason}", file=sys.stderr)
            break
        checkpoint, bundle = run_opportunity(
            checkpoint_path,
            checkpoint,
            search,
            arm=arm,
            manifest=fixture.manifest,
            execute=runner.execute,
            planned=planned,
            artifacts=runner.artifacts,
            execution_config=runner.execution_config_digest,
            parent_bundle_directory=directory,
        )
        if bundle is None:
            print("opportunity ended without a candidate (generation failed)", file=sys.stderr)
            continue
        finalized = runner.finalized_for(bundle.episode_id)
        runner.write(finalized, directory)
        record = _episode_record(finalized, fixture.manifest)
        _write_record(args.data_root, record)
        print(
            f"{bundle.episode_id}: {record['outcomes']} "
            f"mutator_calls={checkpoint.usage.mutator_calls}",
            file=sys.stderr,
        )
    save_checkpoint(checkpoint_path, checkpoint)
    print(
        json.dumps(
            {
                "arm": args.arm,
                "opportunities_settled": checkpoint.usage.opportunities,
                "agent": {
                    "model_calls": checkpoint.usage.model_calls,
                    "input_tokens": checkpoint.usage.input_tokens,
                    "output_tokens": checkpoint.usage.output_tokens,
                },
                "mutator": {
                    "calls": checkpoint.usage.mutator_calls,
                    "input_tokens": checkpoint.usage.mutator_input_tokens,
                    "output_tokens": checkpoint.usage.mutator_output_tokens,
                },
                "usage_complete": checkpoint.usage.complete,
                "stopped": checkpoint.stopped,
                "stop_reason": checkpoint.stop_reason,
                "coverage": {
                    "behavior": len(checkpoint.search.ledger.global_seen.behavior),
                    "risk": len(checkpoint.search.ledger.global_seen.risk),
                    "joint": len(checkpoint.search.ledger.global_seen.joint),
                },
                "checkpoint": str(checkpoint_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _limits(args: argparse.Namespace) -> CampaignLimits:
    budget = _generation_budget(args)
    per_opportunity_mutator = (
        budget.requests_per_opportunity if args.text_provider != "none" else 0
    )
    return CampaignLimits(
        opportunities=args.opportunities,
        model_calls=args.max_model_calls * args.opportunities,
        tool_calls=args.max_tool_calls * args.opportunities,
        input_tokens=args.max_input_tokens * args.opportunities,
        output_tokens=args.max_output_tokens * args.opportunities,
        wall_clock_seconds=args.wall_clock_seconds * args.opportunities,
        expense_units=args.max_expense_units * args.opportunities,
        mutator_calls=per_opportunity_mutator * args.opportunities,
        mutator_input_tokens=(
            budget.max_input_tokens_per_request * per_opportunity_mutator * args.opportunities
        ),
        mutator_output_tokens=(
            budget.max_output_tokens_per_request * per_opportunity_mutator * args.opportunities
        ),
    )


def _stage_freeze(args: argparse.Namespace) -> int:
    from build_structured_v1_freeze import build

    built = build(output=args.output, fixture_id=args.fixture)
    print(f"wrote {built}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)

    freeze = sub.add_parser(
        "freeze", help="write the fixture's freeze manifest for the image build"
    )
    freeze.add_argument(
        # Must stay outside `.dockerignore`: `data/`, `docs/`, `reports/` and `tests/` are
        # excluded from the build context, so a manifest written there cannot be COPYed.
        "--output",
        type=Path,
        default=Path("build/structured-v1/freeze-manifest.json"),
    )
    freeze.add_argument("--fixture", default=DEFAULT_FIXTURE)

    stages: dict[str, argparse.ArgumentParser] = {}
    for name, help_text in (
        ("normal-control", "one normal-control episode"),
        ("g1", "the probe: k repeats of every frozen candidate"),
        ("two-arm", "one arm over N opportunities"),
    ):
        stage = sub.add_parser(name, help=help_text)
        stages[name] = stage
        stage.add_argument("--image", required=True)
        stage.add_argument("--fixture", default=DEFAULT_FIXTURE)
        stage.add_argument("--data-root", type=Path, default=Path("data/structured-v1"))
        stage.add_argument("--arm", default="structured-v1")
        stage.add_argument("--model-provider", choices=("fake", "ollama"), default="fake")
        stage.add_argument("--model-name", default="qwen3.5:27b-q4_K_M")
        stage.add_argument("--endpoint", default="http://127.0.0.1:11434")
        stage.add_argument("--model-timeout-seconds", type=int, default=120)
        stage.add_argument("--network-mode", default="host")
        stage.add_argument("--startup-timeout-seconds", type=float, default=60.0)
        stage.add_argument("--execution-timeout-seconds", type=int, default=600)
        stage.add_argument("--num-ctx", type=int, default=12_288)
        stage.add_argument("--num-predict", type=int, default=1024)
        stage.add_argument("--temperature", default="0.0")
        stage.add_argument("--top-p", default="0.9")
        stage.add_argument("--top-k", type=int, default=40)
        stage.add_argument("--seed", default="structured-v1-2026")
        stage.add_argument("--max-model-calls", type=int, default=12)
        stage.add_argument("--max-tool-calls", type=int, default=12)
        stage.add_argument("--wall-clock-seconds", type=int, default=600)
        stage.add_argument("--max-input-tokens", type=int, default=200_000)
        stage.add_argument("--max-output-tokens", type=int, default=16_000)
        stage.add_argument("--max-expense-units", type=int, default=216)
        stage.add_argument("--text-provider", choices=("none", "ollama"), default="none")
        stage.add_argument("--text-model", default="qwen3.5:27b-q4_K_M")
        stage.add_argument("--text-endpoint", default="http://127.0.0.1:11434")
        stage.add_argument("--text-timeout-seconds", type=int, default=120)
        stage.add_argument("--text-num-predict", type=int, default=4096)
        stage.add_argument("--mutation-requests", type=int, default=2)
        stage.add_argument("--mutation-input-tokens", type=int, default=8192)
        stage.add_argument("--mutation-output-tokens", type=int, default=4096)
        stage.add_argument("--repeats", type=int, default=1)
        stage.add_argument("--candidate-seed", default="g1-candidates-v1")
        stage.add_argument("--parents", type=int, default=10)
        stage.add_argument("--opportunities", type=int, default=1)
        stage.add_argument("--resume", action="store_true")
        stage.add_argument("--allow-deterministic-text", action="store_true")

    # The calibration override is applied here, after the shared flags, so it cannot leak into the
    # other stages: only `normal-control` gets the wider call budget.
    stages["normal-control"].set_defaults(
        max_model_calls=NORMAL_CONTROL_CALIBRATION_CALLS,
        max_tool_calls=NORMAL_CONTROL_CALIBRATION_CALLS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage == "freeze":
        return _stage_freeze(args)
    if args.stage == "normal-control":
        return _stage_normal_control(args)
    if args.stage == "g1":
        return _stage_g1(args)
    return _stage_two_arm(args)


if __name__ == "__main__":
    raise SystemExit(main())
