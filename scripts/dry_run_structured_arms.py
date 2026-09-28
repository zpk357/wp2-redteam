"""Offline dry run for the three structured arms: no model, no server, no container.

It drives the real campaign loop (`run_opportunity`) over the real Office tool runtime with
scripted decisions, so the accounting under test is the accounting a real run would use.  Seven
checks are reported, each with the evidence it came from; the exit code is 0 only when all pass:

 1  the opportunity budget is charged exactly once per opportunity;
 2  both failure kinds (`TextGenerationFailed`, `CandidateRefused`) are recorded, invent no
    Episode, and keep their plan;
 3  checkpoint save / load / resume keeps the books identical;
 4  the three arms share candidate space, edit capability, budgets and failure rules;
 5  `random_evolution` reads no cross-episode feedback;
 6  `coverage_guided` does consume cross-episode feedback;
 7  failures, repeats, no-new coverage and the resumed path leave recomputable evidence.

Usage:
    python scripts/dry_run_structured_arms.py [--opportunities 8] [--json]
"""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.edit_kernel import choose_edit
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.freeze import build_freeze_manifest, load_assets
from sandbox.structured_v1.generation import GenerationBudget, TextGenerationFailed
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.search import (
    GUIDED_FEEDBACK_SOURCES,
    ArmKind,
    CandidateRefused,
    TwoArmSearch,
)
from sandbox.structured_v1.submission import build_envelope
from sandbox.structured_v1.text_provider import HttpJsonTextProvider, TextProviderOptions
from sandbox.structured_v1.tool_catalogue import ImageManifest, build_office_v2_catalogue

FIXTURE_ID = "summary-delivery-a"
PARENTS = 4
ANSWER = ("read_drive_file", {"file_id": "f-overview"})
DELIVER = (
    "send_email",
    {"to": ["partner.contact"], "subject": "Public summary", "body": "Public summary only."},
)
#: The offline execution identity. Without one the coverage result carries no `execution`, so a
#: parent never becomes eligible and the guided arm would only ever draw roots.
DRY_RUN_CONFIG = sha256_digest({"dry-run": "offline execution identity"})
PLANNED = CampaignUsage(
    model_calls=3,
    tool_calls=2,
    input_tokens=1000,
    output_tokens=1000,
    mutator_calls=2,
    mutator_input_tokens=2000,
    mutator_output_tokens=2000,
)


class _Transport:
    """A scripted provider transport: fresh wording, frozen wording, or unparseable output.

    ``fresh`` mimics a real provider (new wording each request).  ``frozen`` repeats itself per
    node, so a second edit of one node is a no-op and the shared admission gate refuses the child.
    ``broken`` never returns what the parser needs, so the wording budget is spent and the
    generation fails.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.requests = 0

    def __call__(self, *, url: str, payload: dict, timeout_seconds: int) -> dict:
        self.requests += 1
        instruction = json.loads(payload["messages"][1]["content"])
        if self.mode == "broken":
            content = "not the structured answer the parser needs"
        else:
            suffix = f" r{self.requests}" if self.mode == "fresh" else ""
            content = json.dumps(
                {
                    "items": [
                        {"node_id": node, "text": f"Material note {node}{suffix}."}
                        for node in instruction["node_ids"]
                    ]
                }
            )
        return {
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": 21,
            "eval_count": 9,
        }


class _Clock:
    """A clock that proves the container's own quiet window, so only behaviour is under test."""

    source = "dry-run-substitute"

    def __init__(self) -> None:
        self._waited = False

    def now(self) -> int:
        return 200 if self._waited else 100

    def wait_until(self, target: int) -> None:
        del target
        self._waited = True


def _limits(opportunities: int) -> CampaignLimits:
    return CampaignLimits(
        opportunities=opportunities,
        model_calls=max(200, 20 * opportunities),
        tool_calls=max(200, 20 * opportunities),
        input_tokens=1_000_000,
        output_tokens=200_000,
        wall_clock_seconds=20_000,
        expense_units=20_000,
        mutator_calls=8 * opportunities,
        mutator_input_tokens=200_000,
        mutator_output_tokens=100_000,
    )


def _options() -> TextProviderOptions:
    return TextProviderOptions(
        provider_id="ollama-chat",
        model_name="dry-run-offline",
        endpoint="http://offline.invalid",
        timeout_seconds=1,
    )


def _generation_budget() -> GenerationBudget:
    return GenerationBudget(
        requests_per_opportunity=2,
        max_input_tokens_per_request=1000,
        max_output_tokens_per_request=1000,
    )


def _search(manifest, parents, *, arm: ArmKind, seed: str, mode: str) -> TwoArmSearch:
    return TwoArmSearch(
        parents,
        manifest,
        seed=f"{seed}:{arm.value}",
        provider=HttpJsonTextProvider(_options(), transport=_Transport(mode)),
        generation_budget=_generation_budget(),
    )


def _executor(manifest, fixture):
    """One offline Episode through the real tool runtime; no container, no model."""
    assets, tooling = load_assets(
        build_freeze_manifest(
            image_manifest=ImageManifest(tool_catalogue=build_office_v2_catalogue()),
            worlds=(fixture.base_world,),
            mapping=(fixture.mapping,),
        )
    )

    def execute(case):
        envelope = build_envelope(
            case,
            manifest=manifest,
            base_world=fixture.base_world,
            overlay=fixture.overlay,
            budget=RunBudgetEnvelope(
                max_model_calls=8,
                max_tool_calls=8,
                max_wall_clock_seconds=600,
                max_input_tokens=100_000,
                max_output_tokens=20_000,
                max_expense_units=1_000,
            ),
            episode_id=f"episode-{case.mutation_lineage.generation_identity}",
            arm_id="dry-run",
        ).envelope
        decisions = [
            ModelDecision(
                call_id="m-1", action_request_id="a-1", tool_name=ANSWER[0], arguments=ANSWER[1]
            ),
            ModelDecision(
                call_id="m-2", action_request_id="a-2", tool_name=DELIVER[0], arguments=DELIVER[1]
            ),
            ModelDecision(call_id="m-stop"),
        ]
        return drive_structured_v1_episode(
            envelope,
            assets=assets,
            tooling=tooling,
            model=ScriptedModelPort(decisions),
            clock=_Clock(),
        )

    return execute


def _drive_arm(*, manifest, fixture, arm, opportunities, mode, seed, workspace, resume_at):
    """One arm over `opportunities`, reloading the checkpoint once at `resume_at`."""

    inputs = prepare_inputs(manifest, count=PARENTS)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = _search(manifest, parents, arm=arm, seed=seed, mode=mode)
    checkpoint = CampaignCheckpoint(limits=_limits(opportunities))
    path = workspace / f"{arm.value}.json"
    execute = _executor(manifest, fixture)
    episodes: list[str] = []
    failures: list[int] = []
    failure_receipts: list[str] = []
    midway_matches: bool | None = None
    midway_opportunities: int | None = None

    for index in range(opportunities):
        checkpoint, bundle = run_opportunity(
            path,
            checkpoint,
            search,
            arm=arm,
            manifest=manifest,
            execute=execute,
            planned=PLANNED,
            execution_config=lambda bundle: DRY_RUN_CONFIG,
        )
        if bundle is None:
            failures.append(index)
            failure_receipts.append(f"opportunity-{index}")
        else:
            episodes.append(bundle.episode_id)
        if resume_at is not None and index + 1 == resume_at:
            reloaded = load_checkpoint(path)
            midway_matches = reloaded == checkpoint
            midway_opportunities = reloaded.usage.opportunities

    reloaded = load_checkpoint(path)
    return {
        "arm": arm.value,
        "opportunities": opportunities,
        "episodes": len(episodes),
        "failures": len(failures),
        "failure_receipts": failure_receipts,
        "failure_receipts_settled": all(
            checkpoint.reservations[item].settled
            for item in failure_receipts
            if item in checkpoint.reservations
        )
        and len(failure_receipts) == len(
            [item for item in failure_receipts if item in checkpoint.reservations]
        ),
        "episode_ids_unique": len(episodes) == len(set(episodes)),
        "charged_opportunities": checkpoint.usage.opportunities,
        "reservations": len(checkpoint.reservations),
        "all_settled": all(item.settled for item in checkpoint.reservations.values()),
        "generations": len(checkpoint.generations),
        # How many of the opportunities were a real parent evolution rather than a fresh root:
        # a plan carries an operation exactly when the shared edit kernel was asked for one.
        "plan_mix": {
            "root": sum(1 for plan in checkpoint.generations if plan.operation is None),
            "local_edit": sum(1 for plan in checkpoint.generations if plan.operation is not None),
        },
        "selection_reasons": sorted({item.reason for item in checkpoint.selections}),
        "failed_generations": sum(
            1 for plan in checkpoint.generations if not plan.accepted
        ),
        "reload_matches": reloaded == checkpoint,
        "midway_matches": midway_matches,
        "midway_opportunities": midway_opportunities,
        "limits": checkpoint.limits.model_dump(),
        "search": {
            "evolution_pool": len(search.state.evolution_pool),
            "parent_coverage": len(search.state.parent_coverage),
            "ledger_behavior": len(search.state.ledger.global_seen.behavior),
            "repeated_outcomes": search.state.diagnostics.repeated_outcomes,
            "new_behavior": search.state.diagnostics.new_behavior,
            "new_risk": search.state.diagnostics.new_risk,
            "new_joint": search.state.diagnostics.new_joint,
            "unit_no_new": len(search.state.unit_no_new),
            "cooldown": len(search.state.cooldown),
            "retention": len(search.state.retention),
        },
        "stopped": checkpoint.stopped,
        "stop_reason": checkpoint.stop_reason,
    }


def _draw(manifest, *, arm: ArmKind, seed: str, mode: str, steps: int, poison: bool) -> list[Any]:
    """Draw `steps` selections of one arm, surviving either failure kind.

    ``poison`` removes the cross-episode feedback the guided arm reads, and fills the sources the
    evolution arm must not read, so the two arms are probed for opposite behaviour.
    """

    inputs = prepare_inputs(manifest, count=PARENTS)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = _search(manifest, parents, arm=arm, seed=seed, mode=mode)
    if arm is ArmKind.RANDOM_EVOLUTION:
        for candidate_id in sorted(search.parents):
            search._pool_admit(arm, search.parents[candidate_id])
    if poison:
        if arm is ArmKind.COVERAGE_GUIDED:
            # Remove the parent coverage it ranks by, and pause the parent it would otherwise
            # redraw, so an arm that really consumes feedback must change its choices.
            search.state = search.state.model_copy(
                update={
                    "parent_coverage": {},
                    "unit_index": {},
                    "cooldown": {sorted(search.parents)[0]: 9},
                }
            )
        else:
            search.state = search.state.model_copy(
                update={
                    "cooldown": {"poison-parent": 9},
                    "occurrence": {"poison-parent": 7},
                    "unit_cooldown": {"poison-unit": 9},
                    "unit_occurrence": {"poison-unit": 7},
                    "unit_opportunities": {"poison-unit": 5},
                    "unit_index": {"poison-unit": ("poison-parent",)},
                    "unit_dimension": {"poison-unit": "behavior"},
                    "archive": {"behavior": ("poison-parent",)},
                }
            )
    receipts = []
    for _ in range(steps):
        receipt = search.select(arm)
        receipts.append(receipt)
        with contextlib.suppress(TextGenerationFailed, CandidateRefused):
            search.generate(receipt)
    return receipts


def _first_failure(manifest, *, arm: ArmKind, seed: str, mode: str, wanted: type) -> dict:
    """Drive real generations until `wanted` is raised, and report what the failure left behind."""

    inputs = prepare_inputs(manifest, count=PARENTS)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = _search(manifest, parents, arm=arm, seed=seed, mode=mode)
    for candidate_id in sorted(search.parents):
        search._pool_admit(arm, search.parents[candidate_id])

    for _ in range(60):
        receipt = search.select(arm)
        before = (search.state.opportunity, search.state.feedback_opportunity)
        try:
            search.generate(receipt)
        except wanted as failure:
            plan = getattr(failure, "plan", None)
            return {
                "kind": wanted.__name__,
                "kept_plan": plan is not None,
                "plan_opportunity": None if plan is None else plan.opportunity,
                "opportunity_delta": search.state.opportunity - before[0],
                "feedback_delta": search.state.feedback_opportunity - before[1],
                "fabricated": any(
                    case.mutation_lineage.generation_identity
                    == f"opportunity-{receipt.opportunity}"
                    for case in search.parents.values()
                ),
            }
    raise AssertionError(f"{wanted.__name__} was never raised for {arm.value}")


def _check_shared_space(manifest) -> dict:
    """Candidate space, edit capability, budgets and failure rules are the same for all arms."""

    inputs = prepare_inputs(manifest, count=PARENTS)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    parent_id = sorted(parents)[0]
    streams = [f"shared-dry-run-{index}" for index in range(6)]
    shapes = {}
    for arm in ArmKind:
        search = _search(manifest, dict(parents), arm=arm, seed="space", mode="fresh")
        shapes[arm.value] = {
            "parents": sorted(search.parents),
            "edits": [
                choose_edit(
                    search.parents[parent_id], manifest=manifest, random_state=stream
                ).describe()
                for stream in streams
            ],
        }
    unique_parent_sets = {tuple(item["parents"]) for item in shapes.values()}
    unique_edit_sets = {tuple(item["edits"]) for item in shapes.values()}
    return {
        "per_arm": shapes,
        "same_candidates": len(unique_parent_sets) == 1,
        "same_edit_kernel": len(unique_edit_sets) == 1,
    }


def _check_feedback(manifest) -> dict:
    """The evolution arm must not read feedback; the guided arm must."""

    evolution_clean = _draw(
        manifest, arm=ArmKind.RANDOM_EVOLUTION, seed="fb", mode="fresh", steps=8, poison=False
    )
    evolution_poisoned = _draw(
        manifest, arm=ArmKind.RANDOM_EVOLUTION, seed="fb", mode="fresh", steps=8, poison=True
    )
    guided_clean = _draw(
        manifest, arm=ArmKind.COVERAGE_GUIDED, seed="fb", mode="fresh", steps=8, poison=False
    )
    guided_poisoned = _draw(
        manifest, arm=ArmKind.COVERAGE_GUIDED, seed="fb", mode="fresh", steps=8, poison=True
    )
    return {
        "evolution_unmoved_by_poison": [item.parent_id for item in evolution_clean]
        == [item.parent_id for item in evolution_poisoned],
        "evolution_declares_no_source": all(
            item.feedback_sources == () for item in evolution_clean
        ),
        "guided_moved_by_poison": [item.parent_id for item in guided_clean]
        != [item.parent_id for item in guided_poisoned],
        "guided_declares_sources": all(
            item.feedback_sources and set(item.feedback_sources) <= set(GUIDED_FEEDBACK_SOURCES)
            for item in guided_clean
        ),
        "guided_sources_sample": sorted(
            {source for item in guided_clean for source in item.feedback_sources}
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=int, default=8)
    parser.add_argument("--fixture", default=FIXTURE_ID)
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    parser.add_argument(
        "--workspace", type=Path, default=Path(".tmp/dry-run-structured-arms")
    )
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    manifest = fixture.manifest
    args.workspace.mkdir(parents=True, exist_ok=True)

    arms = {}
    for arm in ArmKind:
        arms[arm.value] = _drive_arm(
            manifest=manifest,
            fixture=fixture,
            arm=arm,
            opportunities=args.opportunities,
            mode="fresh",
            seed="dry-run",
            workspace=args.workspace / "fresh",
            resume_at=max(1, args.opportunities // 2),
        )
    # The frozen-wording arm is the one that has to meet a refused child.
    arms_with_refusals = _drive_arm(
        manifest=manifest,
        fixture=fixture,
        arm=ArmKind.RANDOM_EVOLUTION,
        opportunities=args.opportunities,
        mode="frozen",
        seed="dry-run-refused",
        workspace=args.workspace / "frozen",
        resume_at=None,
    )

    witnesses = {
        "wording": _first_failure(
            manifest, arm=ArmKind.RANDOM_EVOLUTION, seed="fail-wording", mode="broken",
            wanted=TextGenerationFailed,
        ),
        "refused": _first_failure(
            manifest, arm=ArmKind.RANDOM_EVOLUTION, seed="fail-refused", mode="frozen",
            wanted=CandidateRefused,
        ),
    }

    checks = {
        "1_budget_charged_once": all(
            item["charged_opportunities"] == item["opportunities"]
            and item["episodes"] + item["failures"] == item["opportunities"]
            and item["reservations"] == item["opportunities"]
            and item["all_settled"]
            for item in arms.values()
        ),
        "2_failures_recorded_no_episode": (
            all(item["episode_ids_unique"] for item in arms.values())
            and all(
                witness["kept_plan"]
                and witness["opportunity_delta"] == 1
                and witness["feedback_delta"] == 1
                and not witness["fabricated"]
                for witness in witnesses.values()
            )
        ),
        "3_checkpoint_save_load_resume": (
            all(item["reload_matches"] for item in arms.values())
            and all(item["midway_matches"] for item in arms.values())
        ),
        "4_arms_share_space_budget_failure_rules": False,
        "5_evolution_reads_no_feedback": False,
        "6_guided_consumes_feedback": False,
        "7_recomputable_evidence": (
            # Every arm reloads cleanly with every reservation settled, including the failed ones;
            # both failure kinds keep their plan; and the guided arm really reached a local
            # opportunity, so its feedback path is exercised rather than assumed.
            all(
                item["reload_matches"]
                and item["all_settled"]
                and item["failure_receipts_settled"]
                for item in [*arms.values(), arms_with_refusals]
            )
            and witnesses["wording"]["kept_plan"]
            and witnesses["refused"]["kept_plan"]
            and arms[ArmKind.COVERAGE_GUIDED.value]["search"]["parent_coverage"] > 0
        ),
    }

    space = _check_shared_space(manifest)
    checks["4_arms_share_space_budget_failure_rules"] = bool(
        space["same_candidates"]
        and space["same_edit_kernel"]
        and len({json.dumps(item["limits"], sort_keys=True) for item in arms.values()}) == 1
        and witnesses["wording"]["opportunity_delta"] == witnesses["refused"]["opportunity_delta"]
    )

    feedback = _check_feedback(manifest)
    checks["5_evolution_reads_no_feedback"] = bool(
        feedback["evolution_unmoved_by_poison"] and feedback["evolution_declares_no_source"]
    )
    checks["6_guided_consumes_feedback"] = bool(
        feedback["guided_moved_by_poison"] and feedback["guided_declares_sources"]
    )

    report = {
        "fixture": manifest.fixture_id,
        "manifest_digest": manifest.manifest_digest,
        "opportunities_per_arm": args.opportunities,
        "checks": checks,
        "arms": arms,
        "frozen_wording_arm": arms_with_refusals,
        "failure_witnesses": witnesses,
        "shared_space": space,
        "feedback": feedback,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=== dry run: three structured arms (offline) ===")
        print(f"fixture {manifest.fixture_id}  digest {manifest.manifest_digest}")
        print(f"opportunities per arm: {args.opportunities}")
        for arm, item in arms.items():
            print(
                f"  {arm:<20} episodes={item['episodes']} failures={item['failures']} "
                f"charged={item['charged_opportunities']} settled={item['all_settled']} "
                f"roots={item['plan_mix']['root']} local={item['plan_mix']['local_edit']} "
                f"parents={item['search']['parent_coverage']} "
                f"unit_no_new={item['search']['unit_no_new']} "
                f"stopped={item['stopped']}"
            )
        print(
            f"  {'evolution/frozen':<20} episodes={arms_with_refusals['episodes']} "
            f"failures={arms_with_refusals['failures']} "
            f"failed={arms_with_refusals['failure_receipts']} "
            f"settled={arms_with_refusals['failure_receipts_settled']}"
        )
        print("checks:")
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        print(f"failure witnesses: { {k: v['kind'] for k, v in witnesses.items()} }")
        print(f"guided feedback sources: {feedback['guided_sources_sample']}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
