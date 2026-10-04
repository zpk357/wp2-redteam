"""Read the sealed 12-episode archive; emit diagnostics, never run an episode.

Run with scripts/project_python.cmd and --output <derived-json>.
The input archive is never extracted or written. No material body or credential is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import build_fixture
from sandbox.structured_v1.bundle import FinalizedEpisodeBundle, verify_finalized_bundle
from sandbox.structured_v1.campaign import CampaignCheckpoint
from sandbox.structured_v1.coverage import extract_coverage
from sandbox.structured_v1.exposure import carried_content_digest
from sandbox.structured_v1.routes import legitimate_routes

ARCHIVE_SHA = "3526024f8dfd5dc3af7a6154431a5afcae68577777088b0d00f33f0ddca3a832"
DIMENSIONS = ("behavior", "risk", "joint")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def keys(coverage, dimension):
    return {item.key for item in getattr(coverage, dimension)}


def slot_texts(bundle):
    return {slot.slot_id: "\n".join(slot.contents) for slot in bundle.material.slots}


def exposure_window(bundle, fact):
    record = next(r for r in bundle.records if r.transaction_id == fact.tool_call_id)
    index = next(
        i
        for i, d in enumerate(bundle.model_decisions)
        if d.action_request_id == record.action_request_id
    )
    assert fact.material.carried_content_digest == carried_content_digest(
        slot_texts(bundle)[fact.material.slot_id]
    )
    tail = bundle.model_decisions[index + 1 :]
    return {
        "slot": fact.material.slot_id,
        "tool_call_id": fact.tool_call_id,
        "read_decision": index + 1,
        "content_digest": fact.material.carried_content_digest,
        "remaining_decisions": len(tail),
        "subsequent_actions": [d.tool_name or "model-stop" for d in tail],
    }


def analyze(archive):
    original = archive.read_bytes()
    assert digest(original) == ARCHIVE_SHA, "wrong archive identity"
    with tarfile.open(archive) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    fixture = build_fixture()
    result = {
        "run_id": "20260923-090758",
        "archive_sha256": ARCHIVE_SHA,
        "analysis_kind": "read-only-preflight-diagnostic-not-a-G1-or-G2-verdict",
        "member_sha256": {n: digest(b) for n, b in sorted(files.items())},
        "arms": {},
    }
    for arm in ("guided", "random"):
        prefix = f"repo/data/structured-v1/preflight-{arm}-20260923-090758/"
        cp = CampaignCheckpoint.model_validate_json(files[prefix + "checkpoint.json"])
        finals = {
            f.container_bundle.episode_id: f
            for name, data in sorted(files.items())
            if name.startswith(prefix + "finalized/") and name.endswith(".json")
            for f in [FinalizedEpisodeBundle.model_validate_json(data)]
        }
        assert len(finals) == len(cp.selections) == cp.usage.opportunities == 6
        by_episode = {
            r.episode_id: (candidate, r) for candidate, r in cp.search.parent_coverage.items()
        }
        episodes, child_edges, growth = [], [], []
        seen = {d: set() for d in DIMENSIONS}
        prior_candidates = set()
        findings = set()
        for selection in cp.selections:
            matches = [
                (ep, candidate, cov)
                for ep, (candidate, cov) in by_episode.items()
                if candidate
                == (
                    f"opportunity-{selection.opportunity}"
                    if selection.parent_baseline
                    else f"root-restart-{selection.opportunity}"
                    if arm == "guided"
                    else f"independent-root-{selection.opportunity}"
                )
            ]
            assert len(matches) == 1
            ep, candidate, stored = matches[0]
            finalized = finals[ep]
            verify_finalized_bundle(finalized)
            assert finalized.complete and not finalized.missing
            bundle = finalized.container_bundle
            identity = stored.execution
            assert identity.candidate_id == candidate
            assert identity.bundle_digest == bundle.bundle_digest
            assert identity.envelope_digest == bundle.envelope_digest
            assert identity.material_digest == bundle.material.material_digest
            assert identity.manifest_digest == fixture.manifest.manifest_digest
            fresh = extract_coverage(
                bundle, manifest=fixture.manifest, artifacts=finalized.artifacts()
            )
            assert fresh == stored.model_copy(update={"execution": None})
            assert stored.outcome_by_obligation == {
                "data-release": "no-observed-violation",
                "audience-scope": "no-observed-violation",
            }
            findings.update(stored.findings)
            novelty = {d: len(keys(stored, d) - seen[d]) for d in DIMENSIONS}
            growth.append(
                {
                    "opportunity": selection.opportunity,
                    "candidate": candidate,
                    "new_global": novelty,
                }
            )
            for dim in DIMENSIONS:
                seen[dim].update(keys(stored, dim))
            decision_by_action = {
                d.action_request_id: d for d in bundle.model_decisions if d.action_request_id
            }
            blocked = [
                {
                    "call": r.transaction_id,
                    "tool": decision_by_action[r.action_request_id].tool_name,
                    "arguments": decision_by_action[r.action_request_id].arguments,
                }
                for r in bundle.records
                if any(e.blocked for e in r.effects)
            ]
            episodes.append(
                {
                    "episode_id": ep,
                    "candidate": candidate,
                    "member": prefix + f"finalized/{ep}.final.json",
                    "bundle_digest": bundle.bundle_digest,
                    "model_calls": bundle.usage.model_calls,
                    "tool_calls": bundle.usage.tool_calls,
                    "stop_reason": bundle.stop_reason.value,
                    "exposures": [exposure_window(bundle, f) for f in bundle.exposures],
                    "blocked_calls": blocked,
                }
            )
            if selection.parent_baseline:
                parent = selection.parent_baseline
                assert selection.parent_id in prior_candidates
                assert parent == cp.search.parent_coverage[selection.parent_id]
                assert parent.execution.execution_config_digest == identity.execution_config_digest
                parent_bundle = finals[parent.episode_id].container_bundle
                assert parent.execution.bundle_digest == parent_bundle.bundle_digest
                old, new = slot_texts(parent_bundle), slot_texts(bundle)
                changed = sorted(s for s in old.keys() | new.keys() if old.get(s) != new.get(s))
                exposed = [
                    exposure_window(bundle, f)
                    for f in bundle.exposures
                    if f.material.slot_id in changed
                ]
                parent_delta = {d: len(keys(stored, d) - keys(parent, d)) for d in DIMENSIONS}
                retention = cp.search.retention[candidate]
                assert parent_delta == {d: getattr(retention, f"new_{d}") for d in DIMENSIONS}
                child_edges.append(
                    {
                        "opportunity": selection.opportunity,
                        "parent": selection.parent_id,
                        "child": candidate,
                        "parent_episode": parent.episode_id,
                        "child_episode": ep,
                        "operation": cp.generations[selection.opportunity].operation.value,
                        "changed_slots": changed,
                        "changed_exposures": exposed,
                        "selection_dimension": selection.selected_dimension,
                        "selection_key": selection.selected_key,
                        "parent_baseline_digest": parent.execution.bundle_digest,
                        "new_relative_parent": parent_delta,
                        "new_global": novelty,
                        "retention": retention.model_dump(mode="json"),
                    }
                )
            prior_candidates.add(candidate)
        for dim in DIMENSIONS:
            assert seen[dim] == getattr(cp.search.ledger.global_seen, dim)
        assert (
            sum(f.container_bundle.usage.model_calls for f in finals.values())
            == cp.usage.model_calls
        )
        assert (
            sum(f.container_bundle.usage.tool_calls for f in finals.values()) == cp.usage.tool_calls
        )
        routes = legitimate_routes(
            list(finals.values()), manifest=fixture.manifest, overlay=fixture.overlay
        )
        assert routes.route_count == 2 and not routes.excluded
        local = [s for s in cp.selections if s.parent_baseline]
        selected_blocked = sum("blocked" in json.dumps(s.selected_key) for s in local)
        result["arms"][arm] = {
            "usage": cp.usage.model_dump(mode="json"),
            "episodes": episodes,
            "coverage_counts": {d: len(seen[d]) for d in DIMENSIONS},
            "coverage_keys": {d: sorted(seen[d], key=repr) for d in DIMENSIONS},
            "findings": sorted(findings),
            "route_count": routes.route_count,
            "eligible_episodes": list(routes.eligible_episodes),
            "growth": growth,
            "child_edges": child_edges,
            "local_selections": len(local),
            "blocked_local_selections": selected_blocked,
            "cooldown_hit_receipts": sum(s.cooldown > 0 for s in cp.selections),
            "episodes_with_blocked_call": sum(bool(e["blocked_calls"]) for e in episodes),
            "selection_reasons": [s.reason for s in cp.selections],
        }
    assert archive.read_bytes() == original, "input archive changed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("D:/hxjh/runs/20260923-preflight/preflight-20260923-090758.tar.gz"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve() != args.archive.resolve()
    result = analyze(args.archive)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for arm, data in result["arms"].items():
        print(
            arm,
            data["coverage_counts"],
            "local",
            data["local_selections"],
            "selected-blocked",
            data["blocked_local_selections"],
            "routes",
            data["route_count"],
        )


if __name__ == "__main__":
    main()
