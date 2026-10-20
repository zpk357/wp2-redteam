"""Capture the old fixture's freeze identity and a few representative Episode digests.

This is the T01 baseline for the substantive-office fixture work (`SOF-AC-01`): it records what the
current `error-capable-multipath-02` fixture materialises before anything is added, so a later
comparison can show that the old fixture was not moved.  It runs without an adapter or a model.

Run:
    python scripts/capture_substantive_baseline.py --out docs/reports/<file>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "agent_image"))

from sandbox.replay.digests import sha256_digest  # noqa: E402
from sandbox.scenarios.error_capable import (  # noqa: E402
    TASK_FAMILY_SPECS,
    AttackMode,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import FirstInput, office_tool_specs  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_world import LAYOUT_COUNTS, planned_file_ids  # noqa: E402

SAMPLES = (
    # family, path index, attack mode, carrier, layout
    (TaskFamily.SUMMARY_DELIVERY, 0, AttackMode.CROSS_FILE_SPLICE, "message_body", "balanced-9"),
    (
        TaskFamily.CALENDAR_COORDINATION,
        1,
        AttackMode.RECIPIENT_SUBSTITUTION,
        "review_comment",
        "balanced-9",
    ),
    (TaskFamily.ACCESS_REVIEW, 2, AttackMode.LEDGER_REWRITE, "ledger_column", "balanced-9"),
)


def _request() -> SelectorRequest:
    return SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=7,
        agent_model_name="model.baseline",
        selector_model_name="model.baseline",
        available_task_families=tuple(TaskFamily),
        available_attacks=tuple(AttackMode),
        feedback=CoverageFeedback(),
    )


def _plan(family: TaskFamily, path_index: int, attack: AttackMode, carrier: str, layout: str):
    spec = next(item for item in TASK_FAMILY_SPECS if item.task_family is family)
    decision = SelectorDecision(
        task_family=family,
        path_id=spec.path_ids[path_index],
        attack_mode=attack,
        attack_carrier=carrier,
        layout_id=layout,
        episode_kind=EpisodeKind.ATTACK,
        rationale="baseline sample",
    )
    episode_id = f"baseline.{family.value}"
    ids = planned_file_ids(episode_id, seed=7, layout_id=layout)
    return build_plan(
        _request(),
        decision,
        episode_id=episode_id,
        task_file_id=ids[0],
        file_ids=ids,
        attack_carrier=carrier,
        model_name="model.baseline",
        layout_id=layout,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    specs = office_tool_specs()
    payload: dict[str, object] = {
        "fixture_id": fixture.fixture_id,
        "fixture_version": fixture.fixture_version,
        "manifest_digest": fixture.manifest.manifest_digest,
        "freeze_digest": fixture.freeze_digest,
        "world_id": fixture.manifest.world_id,
        "world_version": fixture.manifest.world_version,
        "world_digest": fixture.manifest.world_digest,
        "tool_catalogue_digest": fixture.manifest.tool_catalogue_digest,
        "tool_names": [spec.name for spec in specs],
        "layouts": dict(LAYOUT_COUNTS),
        "samples": [],
    }
    samples = payload["samples"]
    assert isinstance(samples, list)
    for family, path_index, attack, carrier, layout in SAMPLES:
        plan = _plan(family, path_index, attack, carrier, layout)
        material = materialize_scenario(plan)
        first_input = FirstInput.build(plan=plan, material=material, specs=specs)
        samples.append(
            {
                "task_family": family.value,
                "path_id": plan.path_id,
                "attack_mode": attack.value,
                "attack_carrier": carrier,
                "layout_id": layout,
                "episode_id": plan.episode_id,
                "plan_digest": sha256_digest(plan.model_dump(mode="json")),
                "materialization_digest": material.materialization_digest,
                "first_input_digest": first_input.payload_digest,
                "file_ids": list(plan.file_ids),
                "task_file_id": plan.task_file_id,
            }
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "freeze": payload["freeze_digest"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
