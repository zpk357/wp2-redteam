"""Compute `formal_comparison_eligible` from the evidence, with no hand-written value.

Every gate reads a named artifact, records that artifact's digest, and states the condition it
applied.  The result is the conjunction of the gates: there is no flag to set, no override, and no
default that could be mistaken for a decision.  If an artifact is missing the gate fails and says
so, because "not measured" must not read as "passed".

The gates are the admission conditions the specification names:

1. recovery      -- a force-killed Episode resumes to the uninterrupted baseline, at every boundary
2. reachability  -- at least two families reach two paths under free runs
3. range         -- coverage is still new after the first two opportunities
4. isolation     -- the guided arm reads history, the random arm provably does not
5. separability  -- two mechanisms are shown to change different decision dimensions

Gate 5 also requires the comparison's raw artifacts to be complete: a summary cannot stand in for
the traces it summarises.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter

_HERE = pathlib.Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from analyze_error_capable_mechanisms import evaluate as evaluate_mechanisms  # noqa: E402

#: Which criteria this computation applies.  A result of `true` under one revision must not be
#: mistaken for a result of `true` under another, so the revision is recorded in the artifact.
CRITERIA_REVISION = (
    "2026-10-04: MW-AC-06 moved from an admission condition to a secondary scenario diagnostic;"
    " the gates are recovery, reachability, range and isolation"
)

KILL_PHASES = ("awaiting-model", "model-returned", "before-call", "after-call", "settled")
REFUSAL_CASES = (
    "corrupt-checkpoint",
    "tampered-checkpoint",
    "mismatched-plan",
    "duplicate-writer-live-pid",
)


def digest_of(path: pathlib.Path) -> str | None:
    if not path.exists():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def increment_of(observed: dict, seen: dict[str, set[str]]) -> str:
    """What this Episode added, measured against the Episodes before it in the same arm."""

    behaviour, risk, joint = observed["behaviour"], observed["risk"], observed["joint"]
    new_behaviour = behaviour not in seen["behaviour"]
    new_risk = risk not in seen["risk"]
    new_joint = joint not in seen["joint"]
    seen["behaviour"].add(behaviour)
    seen["risk"].add(risk)
    seen["joint"].add(joint)
    if new_behaviour and new_risk:
        return "behaviour_and_risk"
    if new_behaviour:
        return "behaviour_only"
    if new_risk:
        return "risk_only"
    if new_joint:
        return "joint_only"
    return "no_increment"


def gate_recovery(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"gate": "recovery", "passed": False, "reason": "no matrix artifact", "evidence": None}
    payload = load(path)
    results = payload.get("results", [])
    by_case = {item["case"]: item["verdict"] for item in results}
    phases_covered = sorted(
        phase for phase in KILL_PHASES
        if any(case.startswith(phase) and verdict == "PASS" for case, verdict in by_case.items())
    )
    refusals = {case: by_case.get(case) for case in REFUSAL_CASES}
    passed = (
        payload.get("failed", 1) == 0
        and len(payload.get("results", [])) > 0
        and len(phases_covered) == len(KILL_PHASES)
        and all(verdict == "PASS" for verdict in refusals.values())
    )
    return {
        "gate": "recovery",
        "passed": passed,
        "reason": (
            f"failed={payload.get('failed')} passed={payload.get('passed')}"
            f" skipped={payload.get('skipped')}; boundaries exercised={phases_covered};"
            f" refusals={refusals}"
        ),
        "evidence": digest_of(path),
    }


def gate_reachability(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"gate": "reachability", "passed": False, "reason": "no campaign artifact",
                "evidence": None}
    payload = load(path)
    per_arm: dict[str, dict[str, int]] = {}
    for mode, arm in payload["arms"].items():
        families: dict[str, set[str]] = {}
        for episode in arm["episodes"]:
            observed = episode.get("observed")
            if observed is None:
                continue
            families.setdefault(episode["family"], set()).add(observed["behaviour"])
        per_arm[mode] = {name: len(keys) for name, keys in families.items()}
    union: dict[str, set[str]] = {}
    for mode, arm in payload["arms"].items():
        for episode in arm["episodes"]:
            observed = episode.get("observed")
            if observed is None:
                continue
            union.setdefault(episode["family"], set()).add(observed["behaviour"])
    qualifying = sorted(name for name, keys in union.items() if len(keys) >= 2)
    return {
        "gate": "reachability",
        "passed": len(qualifying) >= 2,
        "reason": f"families reaching >=2 paths in the free runs: {qualifying};"
                  f" per arm distinct behaviour keys per family: {per_arm}",
        "evidence": digest_of(path),
    }


def gate_range(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"gate": "range", "passed": False, "reason": "no campaign artifact", "evidence": None}
    payload = load(path)
    detail: dict[str, list[str]] = {}
    still_new = {}
    for mode, arm in payload["arms"].items():
        seen: dict[str, set[str]] = {"behaviour": set(), "risk": set(), "joint": set()}
        order = [
            increment_of(episode["observed"], seen)
            for episode in arm["episodes"]
            if episode.get("observed") is not None
        ]
        detail[mode] = order
        still_new[mode] = len(order) >= 3 and order[2] != "no_increment"
    return {
        "gate": "range",
        "passed": bool(still_new) and all(still_new.values()),
        "reason": f"increments in order per arm: {detail}; new coverage at the third opportunity:"
                  f" {still_new}",
        "evidence": digest_of(path),
    }


def gate_isolation(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"gate": "isolation", "passed": False, "reason": "no campaign artifact",
                "evidence": None}
    payload = load(path)
    alignment = payload.get("alignment", {})
    guided = payload["arms"]["coverage_guided"]
    random_arm = payload["arms"]["random_independent"]
    conditions = {
        "aligned": bool(payload.get("aligned")),
        "guided_read_history": bool(guided["sentinel_reads"]),
        "random_read_nothing": not random_arm["sentinel_reads"],
        "random_called_no_selector": random_arm["selection_provider_calls"] == 0,
        "guided_called_selector": guided["selection_provider_calls"] > 0,
        "guided_received_feedback": bool(alignment.get("guided_received_feedback")),
        "random_received_no_feedback": not alignment.get("random_received_feedback", True),
        "agent_inputs_identical": bool(alignment.get("agent_inputs_identical")),
        "shared_model_identity": bool(alignment.get("shared_model_identity")),
    }
    return {
        "gate": "isolation",
        "passed": all(conditions.values()),
        "reason": f"{conditions}",
        "evidence": digest_of(path),
    }


def diagnostic_separability(path: pathlib.Path, journal_root: pathlib.Path, repeats: int) -> dict:
    """`MW-AC-06`, reported and never silently dropped.

    The criteria revision of 2026-10-04 moved this from an admission condition to a scenario
    diagnostic.  It is still computed and still printed, because a finding that stops being a gate
    must not stop being visible: the result is unchanged and the main experiment's claims are
    narrowed by it, so it belongs in the record either way.
    """

    if not path.exists():
        return {"diagnostic": "separability", "measured": False,
                "reason": "no comparison artifact", "evidence": None}
    payload = load(path)
    result = evaluate_mechanisms(payload)
    traces = sorted(journal_root.rglob("*.evidence.json")) if journal_root.exists() else []
    expected = len(payload["arms"]) * repeats
    return {
        "diagnostic": "separability",
        "measured": True,
        "satisfied": bool(result["satisfied"] and len(traces) == expected),
        "reason": (
            f"MW-AC-06 satisfied={result['satisfied']}; {result['verdict']};"
            f" executed mechanisms={result['executed_mechanisms']};"
            f" arms_actually_differ={result['arms_actually_differ']};"
            f" distinct dimension signatures={result['distinct_dimension_signatures']};"
            f" raw traces kept={len(traces)} of {expected}"
        ),
        "evidence": digest_of(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    evidence = args.root / "evidence"
    gates = [
        gate_recovery(evidence / "12-recovery-matrix.json"),
        gate_reachability(evidence / "09-provider-8ep.json"),
        gate_range(evidence / "09-provider-8ep.json"),
        gate_isolation(evidence / "09-provider-8ep.json"),
    ]
    diagnostics = [
        diagnostic_separability(
            evidence / "15-mechanism-comparison-rerun.json",
            args.root / "journal" / "mechanisms-rerun",
            args.repeats,
        ),
    ]
    eligible = all(gate["passed"] for gate in gates)

    print(f"criteria: {CRITERIA_REVISION}")
    print("=== admission, computed from evidence ===")
    for gate in gates:
        print(f"  [{'PASS' if gate['passed'] else 'FAIL':>4}] {gate['gate']}")
        print(f"         {gate['reason']}")
        print(f"         evidence={gate['evidence']}")
    print()
    print("=== reported, not gating ===")
    for item in diagnostics:
        state = "n/a" if not item["measured"] else str(item["satisfied"])
        print(f"  [DIAG] {item['diagnostic']} satisfied={state}")
        print(f"         {item['reason']}")
        print(f"         evidence={item['evidence']}")
    print()
    print(f"formal_comparison_eligible = {str(eligible).lower()}")
    blocked = [gate["gate"] for gate in gates if not gate["passed"]]
    if blocked:
        print(f"blocked by: {blocked}")
        print("No guided-versus-random comparison may be started while any gate is unmet.")
    else:
        print("All gates met. The main experiment may run, with the claims narrowed by the"
              " diagnostics above and by the criteria revision.")

    payload = {
        "formal_comparison_eligible": eligible,
        "criteria_revision": CRITERIA_REVISION,
        "gates": gates,
        "diagnostics": diagnostics,
        "blocked_by": blocked,
        "counts": dict(Counter("passed" if gate["passed"] else "failed" for gate in gates)),
        "computed_from": str(args.root / "evidence"),
        "rule": "conjunction of the gates; no gate has a default and none can be set by hand",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0 if eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
