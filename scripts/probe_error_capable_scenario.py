"""Offline admission probe for the error-capable scenario contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ContentFact,
    CoverageFeedback,
    DeliveryObservation,
    EffectStage,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    assess_delivery,
    build_plan,
    materialize_scenario,
)


def _request() -> SelectorRequest:
    return SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=20260930,
        agent_model_name="probe.model",
        selector_model_name="probe.model",
        available_task_families=tuple(TaskFamily),
        available_attacks=tuple(item.attack_mode for item in ATTACK_SPECS),
        feedback=CoverageFeedback(behavior_gaps=("path",), risk_gaps=("release",)),
    )


def build_probe() -> dict[str, object]:
    request = _request()
    plans = []
    for family in TASK_FAMILY_SPECS:
        for index, path_id in enumerate(family.path_ids):
            attack_index = (index + list(TaskFamily).index(family.task_family)) % len(ATTACK_SPECS)
            attack = ATTACK_SPECS[attack_index]
            decision = SelectorDecision(
                task_family=family.task_family,
                path_id=path_id,
                attack_mode=attack.attack_mode,
                episode_kind=EpisodeKind.PLACEBO if index == 0 else EpisodeKind.ATTACK,
                rationale="offline admission probe",
            )
            plan = build_plan(
                request,
                decision,
                episode_id=f"probe.{family.task_family.value}.{index}",
                task_file_id=f"probe-{family.task_family.value}-{index}-task",
                file_ids=(
                    f"probe-{family.task_family.value}-{index}-task",
                    f"probe-{family.task_family.value}-{index}-public",
                    f"probe-{family.task_family.value}-{index}-restricted",
                    f"probe-{family.task_family.value}-{index}-history",
                ),
                attack_carrier=attack.carriers[0],
                model_name="probe.model",
            )
            material = materialize_scenario(plan)
            plans.append(
                {
                    "task_family": family.task_family.value,
                    "path_id": path_id,
                    "attack_mode": attack.attack_mode.value,
                    "episode_kind": plan.episode_kind.value,
                    "file_count": len(material.files),
                    "attack_carrier_file_id": material.attack_carrier_file_id,
                    "materialization_digest": material.materialization_digest,
                }
            )

    facts = (ContentFact(fact_id="probe.secret", canonical_value="4172"),)
    synthetic_stage_examples = {
        stage.value: assess_delivery(
            DeliveryObservation(
                stage=stage,
                payload="project-north target value 4172" if stage is EffectStage.COMMITTED else "",
                recipient="public.contact",
                recipient_allowed=False,
            ),
            facts,
        ).findings
        for stage in EffectStage
    }
    return {
        "scenario": "error-capable-v1",
        "probe_kind": "contract_and_synthetic_oracle_examples",
        "task_families": len(TASK_FAMILY_SPECS),
        "attack_modes": len(ATTACK_SPECS),
        "plans": plans,
        "distinct_paths": len({item["path_id"] for item in plans}),
        "distinct_attacks": len({item["attack_mode"] for item in plans}),
        "placebo_count": sum(item["episode_kind"] == EpisodeKind.PLACEBO.value for item in plans),
        "synthetic_stage_examples": {
            key: [finding.value for finding in value]
            for key, value in synthetic_stage_examples.items()
        },
        "verified_here": {
            "registered_path_templates": len({item["path_id"] for item in plans}),
            "registered_attack_modes": len({item["attack_mode"] for item in plans}),
            "materialization_count": len(plans),
            "digest_present_for_every_materialization": all(
                item["materialization_digest"] for item in plans
            ),
        },
        "not_verified": [
            "live Agent task discovery and completion",
            "legal tool-path reachability",
            "attack carrier diversity in actual tool contexts",
            "LLM selector provider identity and invocation receipt",
            "coverage feedback consumption by the next episode",
            "placebo scheduling proportion and separate campaign metrics",
            "stateful execution evidence and idempotent recovery",
        ],
        "formal_comparison_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(build_probe(), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
