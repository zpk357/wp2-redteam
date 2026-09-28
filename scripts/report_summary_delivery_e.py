"""Verify E evidence and paired unique-J curves locally. Never starts or resumes execution."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from itertools import product
from pathlib import Path

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.structured_v1.bundle import load_finalized_bundle
from sandbox.structured_v1.campaign import load_checkpoint
from sandbox.structured_v1.campaign_report import checkpoint_report
from sandbox.structured_v1.coverage import extract_coverage
from sandbox.structured_v1.mbr import project_mbr
from sandbox.structured_v1.phase_report import phase_report
from sandbox.structured_v1.routes import legitimate_routes, route_key


def read_arm(root: Path, arm: str, fixture) -> dict:
    checkpoint = load_checkpoint(root / "checkpoint.json")
    report = checkpoint_report(checkpoint)
    if any(s.arm.value != arm for s in checkpoint.selections):
        raise ValueError("wrong arm in checkpoint")
    finalized = [load_finalized_bundle(path)
                 for path in sorted((root / "finalized").glob("*.json"))]
    bundles = {f.container_bundle.episode_id: f for f in finalized}
    if len(bundles) != len(finalized) or len(bundles) != report["episodes"]:
        raise ValueError("finalized evidence count does not match checkpoint")
    by_candidate = checkpoint.search.parent_coverage
    evidence = []
    identities = set()
    for selection in checkpoint.selections:
        index = selection.opportunity
        child = f"root-restart-{index}" if selection.root_restart else f"opportunity-{index}"
        coverage = by_candidate[child]
        final = bundles[coverage.episode_id]
        bundle = final.container_bundle
        if bundle.overlay_digest != fixture.mapping.overlay_digest:
            raise ValueError("bundle overlay differs from the frozen fixture")
        if coverage.execution is None or coverage.execution.bundle_digest != bundle.bundle_digest:
            raise ValueError("checkpoint coverage is not bound to the finalized bundle")
        recomputed = extract_coverage(
            bundle, manifest=fixture.manifest, artifacts=final.artifacts(),
        )
        if recomputed.findings != coverage.findings:
            raise ValueError("checkpoint findings differ from original evidence")
        for dimension in ("behavior", "risk", "joint"):
            if {a.key for a in getattr(recomputed, dimension)} != {
                a.key for a in getattr(coverage, dimension)
            }:
                raise ValueError(f"checkpoint {dimension} differs from original evidence")
        identities.add((
            coverage.fixture_id, coverage.execution.manifest_digest,
            coverage.execution.execution_config_digest, coverage.execution.coverage_version,
        ))
        parent = (None if selection.root_restart else
                  checkpoint.search_candidates[selection.parent_id])
        reading = phase_report(bundle, manifest=fixture.manifest, parent=parent)
        reading["opportunity"] = index
        reading["local"] = not selection.root_restart
        reading["feedback_sources"] = selection.feedback_sources
        reading["parent_baseline_bound"] = (
            selection.parent_baseline is not None
            and selection.parent_baseline == by_candidate[selection.parent_id]
        )
        parent_coverage = by_candidate.get(selection.parent_id) if parent is not None else None
        reading["trace_differs_from_parent"] = False
        if parent_coverage is not None:
            parent_bundle = bundles[parent_coverage.episode_id].container_bundle
            def trace(b):
                # Episode-specific object IDs and reworded answers are not behavior branches.
                return ([(d.phase_id, d.tool_name) for d in b.model_decisions],
                        route_key(project_mbr(b, manifest=fixture.manifest)),
                        {a.key for a in extract_coverage(b, manifest=fixture.manifest).behavior})
            reading["trace_differs_from_parent"] = trace(parent_bundle) != trace(bundle)
        evidence.append(reading)
    expected_protocol = fixture.manifest.session_protocol
    valid_phases = all(
        (len(item["phases"]) == 2 or (len(item["phases"]) == 1
         and item["phases"][0]["end_reason"] == "budget-exceeded"))
        and item["phases"][0]["task_text"] == fixture.manifest.public_task_text
        and (len(item["phases"]) == 1
             or item["phases"][1]["task_text"] == expected_protocol.action_task_text)
        and not item["phase_overruns"] and not item["phase_time_overruns"]
        for item in evidence
    )
    restricted = any(item["restricted_read_with_joint"] for item in evidence)
    window = any(item["changed_material_with_action_window"]
                 and item["trace_differs_from_parent"] for item in evidence if item["local"])
    feedback = (all(not item["feedback_sources"] for item in evidence)
                if arm == "random_evolution" else
                any(item["local"] and item["parent_baseline_bound"]
                    and "parent_coverage" in item["feedback_sources"] for item in evidence))
    routes = legitimate_routes(finalized, manifest=fixture.manifest, overlay=fixture.overlay)
    return {
        **report, "limits": checkpoint.limits.model_dump(), "identity": sorted(identities),
        "search_seed": checkpoint.search_seed,
        "evidence": evidence, "routes": {
            **asdict(routes), "meets_two_route_gate": routes.meets_two_route_gate,
        },
        "valid": (report["complete"] and valid_phases and all(f.complete for f in finalized)
                  and checkpoint.pending_search_opportunity is None),
        "research_conditions": {"restricted": restricted, "window": window, "feedback": feedback},
        "j_auc": sum(row["cumulative"]["joint"] for row in report["curves"]),
    }


def paired_result(differences: list[int]) -> dict:
    """Exact paired sign-flip test for the predeclared sum of J-curve areas."""
    observed = sum(differences)
    values = [sum(sign * value for sign, value in zip(signs, differences, strict=True))
              for signs in product((-1, 1), repeat=len(differences))]
    return {"differences": differences, "mean_difference": observed / len(differences),
            "one_sided_p": sum(value >= observed for value in values) / len(values)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", action="append", nargs=2, type=Path, required=True,
                        metavar=("GUIDED_ROOT", "EVOLUTION_ROOT"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    roots = [root.resolve() for pair in args.pair for root in pair]
    if len(set(roots)) != len(roots):
        parser.error("each arm of each pair must name a different evidence directory")
    if any(output.is_relative_to(root.resolve()) for pair in args.pair for root in pair):
        parser.error("output must be outside original campaign evidence directories")
    fixture = load_fixture("summary-delivery-e")
    pairs = []
    for guided, evolution in args.pair:
        arms = {name: read_arm(root, name, fixture) for name, root in (
            ("coverage_guided", guided), ("random_evolution", evolution),
        )}
        g, r = arms.values()
        expected_seed = f"summary-delivery-e-final-{len(pairs) + 1:02d}"
        pairs.append({
            "arms": arms, "valid": g["valid"] and r["valid"]
            and g["limits"] == r["limits"] and len(g["identity"]) == 1
            and g["identity"] == r["identity"] and g["limits"]["opportunities"] == 16
            and g["search_seed"] == f"{expected_seed}:coverage_guided"
            and r["search_seed"] == f"{expected_seed}:random_evolution",
            "research_conditions": all(all(a["research_conditions"].values()) for a in (g, r)),
            "j_auc_difference": g["j_auc"] - r["j_auc"],
        })
    common = pairs[0]["arms"]["coverage_guided"]
    for pair in pairs:
        for arm in pair["arms"].values():
            pair["valid"] &= (arm["identity"] == common["identity"]
                              and arm["limits"] == common["limits"])
    eligible = (len(pairs) == 8 and all(p["valid"] and p["research_conditions"] for p in pairs))
    statistics = paired_result([p["j_auc_difference"] for p in pairs]) if len(pairs) <= 16 else None
    status = ("invalid" if not all(p["valid"] for p in pairs) else
              "research-conditions-insufficient" if not eligible else
              "guided-advantage" if statistics["mean_difference"] > 0
              and statistics["one_sided_p"] <= 0.05 else "no-established-guided-advantage")
    result = {"protocol": "summary-delivery-e-draft-run-package", "status": status,
              "pairs": pairs, "statistics": statistics, "confirmatory_eligible": eligible}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
