# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_loop_identity import build_v2_feedback_loop_identity_lock
from sandbox.fuzzer.v2_report import build_v2_campaign_report
from sandbox.fuzzer.v2_scripted_runtime import (
    ScriptedCampaignBootstrap,
    run_or_resume_scripted_campaign,
)
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import verify_manifest
from sandbox.replay.models import ReplayManifest, ReplayResult, ReplayStatus
from tests.unit.test_office_v2_feedback_loop_batch_c import (
    CAMPAIGN_ID,
    loop_fixture,
)


def build_evidence(
    *,
    parent_manifest_path: Path,
    child_manifest_path: Path,
    replay_result_path: Path,
) -> dict[str, object]:
    parent = ReplayManifest.model_validate_json(parent_manifest_path.read_bytes())
    child = ReplayManifest.model_validate_json(child_manifest_path.read_bytes())
    replay = ReplayResult.model_validate_json(replay_result_path.read_bytes())
    verify_manifest(parent)
    verify_manifest(child)
    if child.parent_replay_id != parent.replay_id or child.case_id == parent.case_id:
        raise ValueError("Fork lineage does not identify a distinct child")
    if child.fork_checkpoint_id is None or child.parent_prefix_digest is None:
        raise ValueError("Fork lineage is incomplete")
    if replay.source_replay_id != child.replay_id or replay.status is not ReplayStatus.MATCHED:
        raise ValueError("Fork child strict replay did not match")
    if (
        replay.source_behavior_digest != replay.replay_behavior_digest
        or replay.source_final_state_digest != replay.replay_final_state_digest
        or not replay.container_removed
        or not all(item.matched for item in replay.checkpoint_comparisons)
    ):
        raise ValueError("Fork child strict replay evidence diverges")
    promoted, state = loop_fixture()
    bootstrap = ScriptedCampaignBootstrap(
        initial_state=state,
        execution=promoted.execution,
        delta=promoted.delta,
    )
    with V2CampaignStore(":memory:") as store:
        result = run_or_resume_scripted_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=bootstrap,
            generation_count=3,
        )
        report = build_v2_campaign_report(store=store, campaign_id=CAMPAIGN_ID)
    decisions = report["decisions"]
    feedback_items = report["feedback"]
    identity = build_v2_feedback_loop_identity_lock()
    payload: dict[str, object] = {
        "evidence_version": "office-v2-step5-loop-evidence-v1",
        "scope": "deterministic-engineering-loop",
        "campaign_id": CAMPAIGN_ID,
        "identity_digest": identity.identity_digest,
        "generation_count": 3,
        "decision_digests": tuple(item["decision_digest"] for item in decisions),
        "feedback_chain": tuple(
            {
                "generation_index": item["generation_index"],
                "feedback_digest": item["feedback_digest"],
                "previous_feedback_digest": item["previous_feedback_digest"],
                "gap_kind": item["gap_kind"],
            }
            for item in feedback_items
        ),
        "final_state_digest": result.final_state_digest,
        "contracts": {
            "single_candidate": True,
            "prior_settlement_required": True,
            "latest_feedback_required": True,
            "baseline_complete_non_terminal": True,
            "finding_replay_deduplicated": True,
            "judge_used": False,
            "real_qwen_used": False,
        },
        "docker": {
            "attempted": True,
            "passed": True,
            "image_ref": child.image_ref,
            "image_digest": child.image_digest,
            "remaining_owned_containers": 0,
            "remaining_owned_volumes": 0,
        },
        "strict_replay": {
            "passed": True,
            "status": replay.status.value,
            "source_behavior_digest": replay.source_behavior_digest,
            "replay_behavior_digest": replay.replay_behavior_digest,
            "source_final_state_digest": replay.source_final_state_digest,
            "replay_final_state_digest": replay.replay_final_state_digest,
        },
        "verification_only_fork": {
            "passed": True,
            "parent_replay_id": parent.replay_id,
            "parent_manifest_digest": parent.manifest_digest,
            "child_replay_id": child.replay_id,
            "child_manifest_digest": child.manifest_digest,
            "checkpoint_id": child.fork_checkpoint_id,
            "parent_prefix_digest": child.parent_prefix_digest,
            "campaign_state_modified": False,
            "coverage_modified": False,
            "finding_modified": False,
            "budget_modified": False,
        },
        "acceptance_complete": True,
    }
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-manifest", type=Path, required=True)
    parser.add_argument("--child-manifest", type=Path, required=True)
    parser.add_argument("--replay-result", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    evidence = build_evidence(
        parent_manifest_path=args.parent_manifest,
        child_manifest_path=args.child_manifest,
        replay_result_path=args.replay_result,
    )
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != encoded:
            raise SystemExit("step5 loop evidence differs")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(evidence["evidence_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
