"""Three-question read of the sealed `summary-delivery-c` precheck archive (run package 23.7).

Read-only: it re-reads what the normal-control gate, the v4 coverage projection and the frozen
MBR v1 already established. It never runs an episode, never starts a model, never writes into
the archive, and never re-judges a route. It answers only:

  Q1  was the material change actually read - by the real tool return, per local child,
      separating "never read" / "read identical content" / "read the changed content";
  Q2  what happened after that read - first real divergence, the decision window that follows
      it, and the delivery outcome, separating tool-selection change, content-propagation
      change, permission change and budget truncation;
  Q3  did the feedback produce descendant gains - parent execution evidence -> selection
      receipt -> frozen baseline -> child execution -> settlement, reporting child-vs-parent
      and child-vs-arm-history new B/R/J separately from the independent U.

Root-candidate first coverage and local-child increments are reported separately; an instance
change, a repeat count or a missing witness is never counted as new coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import build_fixture
from sandbox.structured_v1.acceptance import judge_normal_control
from sandbox.structured_v1.bundle import FinalizedEpisodeBundle, verify_finalized_bundle
from sandbox.structured_v1.campaign import CampaignCheckpoint
from sandbox.structured_v1.coverage import extract_coverage
from sandbox.structured_v1.exposure import carried_content_digest
from sandbox.structured_v1.mbr import project_mbr
from sandbox.structured_v1.routes import legitimate_routes, route_key

ARCHIVE_SHA = "60258d38f505f0a56c22d05c672db86b54f7ef6ce66bf9171778f7f62769e9cf"
RUN_ID = "c01-20260925"
PAIR = "dev-precheck-c-01"
DIMENSIONS = ("behavior", "risk", "joint")
ARMS = (("guided", "coverage_guided"), ("evolution", "random_evolution"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keys(coverage, dimension) -> set[str]:
    return {item.key for item in getattr(coverage, dimension)}


def slot_texts(bundle) -> dict[str, str]:
    return {slot.slot_id: "\n".join(slot.contents) for slot in bundle.material.slots}


def candidate_of(selection) -> str:
    """The generation identity `TwoArmSearch._generate` gives this opportunity's candidate."""

    if selection.root_restart:
        return f"root-restart-{selection.opportunity}"
    return f"opportunity-{selection.opportunity}"


def decision_index(bundle) -> dict[str, tuple[int, object]]:
    return {
        decision.action_request_id: (index, decision)
        for index, decision in enumerate(bundle.model_decisions)
        if decision.action_request_id
    }


def read_rows(bundle, changed_slots: set[str], texts: dict[str, str]) -> list[dict[str, object]]:
    """Q1: every evidenced read, classified as other-slot / identical-content / changed-content."""

    index_of = decision_index(bundle)
    request_by_call = {record.transaction_id: record.action_request_id for record in bundle.records}
    rows = []
    for fact in bundle.exposures:
        slot = fact.material.slot_id
        request_id = request_by_call.get(fact.tool_call_id)
        position = index_of.get(request_id)
        if position is None:
            raise ValueError(f"exposure has no recorded model decision: {fact.tool_call_id}")
        index = position[0]
        tail = [
            decision.tool_name or "model-stop"
            for decision in bundle.model_decisions[index + 1:]
        ]
        is_changed = slot in changed_slots
        same_text = is_changed and (
            fact.material.carried_content_digest == carried_content_digest(texts[slot])
        )
        rows.append({
            "slot": slot,
            "tool": fact.tool_name,
            "call": fact.tool_call_id,
            "sequence": fact.sequence,
            "source_field": fact.source_field,
            "changed": is_changed,
            "verdict": (
                "read-changed-content" if same_text
                else "read-identical-content" if is_changed
                else "other-slot"
            ),
            "read_decision": index + 1,
            "remaining_decisions": len(tail),
            "subsequent_actions": tail,
        })
    return rows


def blocked_calls(bundle) -> list[dict[str, object]]:
    index_of = decision_index(bundle)
    rows = []
    for record in bundle.records:
        if not any(effect.blocked for effect in record.effects):
            continue
        position = index_of.get(record.action_request_id)
        decision = None if position is None else position[1]
        rows.append({
            "call": record.transaction_id,
            "tool": None if decision is None else decision.tool_name,
            "effects": [
                effect.model_dump(mode="json") for effect in record.effects if effect.blocked
            ],
        })
    return rows


def committed_deliveries(bundle) -> list[dict[str, object]]:
    """The committed-content witnesses the tool runtime captured (ANCHOR-05).

    They live on the tool return (`ToolReturn.delivery_content`), never on the effect: only the
    digest, the matched public facts and the source refs are retained.
    """

    rows = []
    for tool_return in bundle.tool_returns:
        witness = tool_return.delivery_content
        if witness is None:
            continue
        rows.append({
            "call": tool_return.tool_call_id,
            "tool": tool_return.tool_name,
            "matched_fact_indices": list(witness.matched_fact_indices),
            "source_refs": len(witness.source_refs),
            "object_id": witness.object_id,
            "version_id": witness.version_id,
        })
    return rows


def analyze(archive: Path) -> dict[str, object]:
    original = archive.read_bytes()
    assert sha(original) == ARCHIVE_SHA, "wrong archive identity"
    with tarfile.open(archive) as tar:
        files = {
            member.name: tar.extractfile(member).read()
            for member in tar.getmembers()
            if member.isfile()
        }
    fixture = build_fixture()
    result: dict[str, object] = {
        "run_id": RUN_ID,
        "archive_sha256": ARCHIVE_SHA,
        "analysis_kind": "read-only-development-precheck-diagnostic-not-an-advantage-verdict",
        "fixture_id": fixture.manifest.fixture_id,
        "fixture_version": fixture.manifest.fixture_version,
        "arms": {},
        "q1_changed_slot_reads": {},
        "q2_first_divergence": {},
        "q3_descendant_gains": {},
    }
    for label, arm in ARMS:
        prefix = f"repo/data/structured-v1/paired-{RUN_ID}-{PAIR}-{arm}/"
        raw_checkpoint = files[prefix + "checkpoint.json"]
        checkpoint = CampaignCheckpoint.model_validate_json(raw_checkpoint)
        finals = {
            item.container_bundle.episode_id: item
            for name, data in sorted(files.items())
            if name.startswith(prefix + "finalized/") and name.endswith(".json")
            for item in [FinalizedEpisodeBundle.model_validate_json(data)]
        }
        by_candidate = dict(checkpoint.search.parent_coverage.items())
        episodes, edges = [], []
        running = {dimension: set() for dimension in DIMENSIONS}
        first_coverage: dict[str, int] = {}
        root_created: dict[str, dict[str, int]] = {}
        first_routes: dict[object, tuple[int, bool]] = {}
        route_ids: dict[object, str] = {}
        coverage_by_episode: dict[str, dict[str, set[str]]] = {}
        settled_without_new = 0
        for selection in checkpoint.selections:
            candidate = candidate_of(selection)
            stored = by_candidate[candidate]
            finalized = finals[stored.episode_id]
            verify_finalized_bundle(finalized)
            assert finalized.complete and not finalized.missing
            bundle = finalized.container_bundle
            identity = stored.execution
            assert identity.candidate_id == candidate
            assert identity.bundle_digest == bundle.bundle_digest
            assert identity.material_digest == bundle.material.material_digest
            assert identity.manifest_digest == fixture.manifest.manifest_digest
            fresh = extract_coverage(
                bundle, manifest=fixture.manifest, artifacts=finalized.artifacts()
            )
            assert fresh == stored.model_copy(update={"execution": None})
            texts = slot_texts(bundle)
            delta_arm = {dimension: len(keys(stored, dimension) - running[dimension])
                         for dimension in DIMENSIONS}
            for dimension in DIMENSIONS:
                running[dimension].update(keys(stored, dimension))
            if not any(delta_arm.values()):
                settled_without_new += 1
            if not first_coverage:
                first_coverage = {d: len(keys(stored, d)) for d in DIMENSIONS}
            if selection.root_restart:
                root_created[candidate] = {
                    "dimension": selection.selected_direction,
                    **{d: len(keys(stored, d)) for d in DIMENSIONS},
                }
            episode = {
                "opportunity": selection.opportunity,
                "candidate": candidate,
                "episode_id": stored.episode_id,
                "root_restart": selection.root_restart,
                "selection_reason": selection.reason,
                "selection_key": selection.selected_key,
                "selection_dimension": selection.selected_dimension,
                "selected_direction": selection.selected_direction,
                "feedback_sources": list(selection.feedback_sources),
                "model_calls": bundle.usage.model_calls,
                "tool_calls": bundle.usage.tool_calls,
                "stop_reason": bundle.stop_reason.value,
                "outcome_by_obligation": dict(stored.outcome_by_obligation),
                "dimension_totals": {d: len(keys(stored, d)) for d in DIMENSIONS},
                "delta_arm_history": delta_arm,
                "blocked_calls": blocked_calls(bundle),
                "committed_deliveries": committed_deliveries(bundle),
                "exposure_slots": sorted({fact.material.slot_id for fact in bundle.exposures}),
                "findings": sorted(stored.findings),
            }
            coverage_by_episode[stored.episode_id] = {
                dimension: keys(stored, dimension) for dimension in DIMENSIONS
            }
            if judge_normal_control(
                finalized, manifest=fixture.manifest, overlay=fixture.overlay
            ).accepted:
                route = route_key(project_mbr(bundle, fixture.manifest))
                if route not in first_routes:
                    first_routes[route] = (selection.opportunity, selection.root_restart)
                    route_ids[route] = f"route-{len(route_ids) + 1:02d}"
                episode["route_id"] = route_ids[route]
                episode["route_first_opportunity"] = first_routes[route][0]
                episode["route_first_seen"] = first_routes[route][0] == selection.opportunity
            else:
                episode["route_id"] = None
                episode["route_first_opportunity"] = None
                episode["route_first_seen"] = False
            episodes.append(episode)
            if selection.root_restart:
                continue
            parent = checkpoint.search.parent_coverage[selection.parent_id]
            parent_bundle = finals[parent.episode_id].container_bundle
            old, new = slot_texts(parent_bundle), texts
            changed = sorted(s for s in old.keys() | new.keys() if old.get(s) != new.get(s))
            rows = read_rows(bundle, set(changed), new)
            changed_rows = [row for row in rows if row["changed"]]
            edges.append({
                "opportunity": selection.opportunity,
                "parent": selection.parent_id,
                "parent_episode": parent.episode_id,
                "child": candidate,
                "child_episode": stored.episode_id,
                "operation": checkpoint.generations[selection.opportunity].operation.value,
                "changed_slots": changed,
                "changed_slot_reads": changed_rows,
                "changed_slots_read": sorted({row["slot"] for row in changed_rows}),
                "delivery_committed": bool(episode["committed_deliveries"]),
                "blocked_calls": episode["blocked_calls"],
                "new_relative_parent": {
                    d: len(keys(stored, d) - keys(parent, d)) for d in DIMENSIONS
                },
                "new_relative_arm_history": delta_arm,
                "selection_dimension": selection.selected_dimension,
                "selection_key": selection.selected_key,
                "parent_baseline_present": selection.parent_baseline is not None,
                "parent_baseline_digest": parent.execution.bundle_digest,
                "feedback_sources": list(selection.feedback_sources),
            })
        routes = legitimate_routes(
            list(finals.values()), manifest=fixture.manifest, overlay=fixture.overlay
        )
        assert len(first_routes) == routes.route_count
        route_examples: list[dict[str, object]] = []
        for first_index, first in enumerate(episodes):
            if first["route_id"] is None:
                continue
            for second in episodes[first_index + 1:]:
                if second["route_id"] in (None, first["route_id"]):
                    continue
                first_keys = coverage_by_episode[first["episode_id"]]
                second_keys = coverage_by_episode[second["episode_id"]]
                if not all(first_keys[dimension] == second_keys[dimension]
                           for dimension in DIMENSIONS):
                    continue
                route_examples.append({
                    "first_opportunity": first["opportunity"],
                    "first_episode_id": first["episode_id"],
                    "first_route_id": first["route_id"],
                    "second_opportunity": second["opportunity"],
                    "second_episode_id": second["episode_id"],
                    "second_route_id": second["route_id"],
                    "coverage_counts": first["dimension_totals"],
                    "first_actions": [
                        decision.tool_name or "model-stop"
                        for decision in finals[first["episode_id"]].container_bundle.model_decisions
                    ],
                    "second_actions": [
                        decision.tool_name or "model-stop"
                        for decision in (
                            finals[second["episode_id"]].container_bundle.model_decisions
                        )
                    ],
                })
                break
        local = [item for item in checkpoint.selections if not item.root_restart]
        result["arms"][label] = {
            "arm": arm,
            "checkpoint_sha256": sha(raw_checkpoint),
            "opportunities": checkpoint.usage.opportunities,
            "episodes": len(finals),
            "local_selections": len(local),
            "usage": checkpoint.usage.model_dump(mode="json"),
            "limits": checkpoint.limits.model_dump(mode="json"),
            "coverage_counts": {d: len(running[d]) for d in DIMENSIONS},
            "first_candidate_coverage": first_coverage,
            "root_candidates": root_created,
            "settled_without_new": settled_without_new,
            "route_count": routes.route_count,
            "first_seen_routes": {
                "root": sum(is_root for _, is_root in first_routes.values()),
                "local": sum(not is_root for _, is_root in first_routes.values()),
            },
            "same_coverage_distinct_route_examples": route_examples,
            "meets_two_route_gate": routes.meets_two_route_gate,
            "eligible_episodes": list(routes.eligible_episodes),
            "excluded": [
                {"episode_id": item.episode_id, "failed_criteria": list(item.failed_criteria)}
                for item in routes.excluded
            ],
            "feedback_sources_used": sorted({
                source for item in checkpoint.selections for source in item.feedback_sources
            }),
            "selection_reasons": sorted({item.reason for item in checkpoint.selections}),
            "episode_rows": episodes,
            "local_edges": edges,
        }
        result["q1_changed_slot_reads"][label] = {
            "local_children": len(edges),
            "children_with_changed_slots": sum(1 for e in edges if e["changed_slots"]),
            "children_reading_changed_slot": sum(1 for e in edges if e["changed_slot_reads"]),
            "changed_slot_read_events": sum(len(e["changed_slot_reads"]) for e in edges),
            "never_read": [
                f"opp{e['opportunity']}:{','.join(e['changed_slots'])}"
                for e in edges if e["changed_slots"] and not e["changed_slot_reads"]
            ],
        }
        result["q2_first_divergence"][label] = [
            {
                "opportunity": e["opportunity"],
                "changed_slots": e["changed_slots"],
                "slots_read": e["changed_slots_read"],
                "reads": [
                    {
                        "slot": row["slot"],
                        "tool": row["tool"],
                        "verdict": row["verdict"],
                        "read_decision": row["read_decision"],
                        "remaining_decisions": row["remaining_decisions"],
                        "subsequent_actions": row["subsequent_actions"],
                    }
                    for row in e["changed_slot_reads"]
                ],
                "delivery_committed": e["delivery_committed"],
                "blocked_calls": len(e["blocked_calls"]),
            }
            for e in edges
        ]
        result["q3_descendant_gains"][label] = [
            {
                "opportunity": e["opportunity"],
                "parent": e["parent"],
                "child": e["child"],
                "operation": e["operation"],
                "selection_dimension": e["selection_dimension"],
                "selection_key": e["selection_key"],
                "baseline_present": e["parent_baseline_present"],
                "new_relative_parent": e["new_relative_parent"],
                "new_relative_arm_history": e["new_relative_arm_history"],
                "delivery_committed": e["delivery_committed"],
            }
            for e in edges
        ]
    assert archive.read_bytes() == original, "input archive changed"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve() != args.archive.resolve()
    result = analyze(args.archive)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for label, data in result["arms"].items():
        print(
            label,
            "coverage", data["coverage_counts"],
            "episodes", data["episodes"],
            "local", data["local_selections"],
            "routes", data["route_count"],
            "no-new", data["settled_without_new"],
        )
        print("   q1", result["q1_changed_slot_reads"][label])
        print("   feedback_sources", data["feedback_sources_used"])
        print("   reasons", data["selection_reasons"])


if __name__ == "__main__":
    main()
