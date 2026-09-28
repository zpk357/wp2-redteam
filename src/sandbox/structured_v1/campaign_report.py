"""Read-only checkpoint curves (SOC-FBK-14/15/16); not a bundle verifier."""

from __future__ import annotations

from sandbox.structured_v1.campaign import CampaignCheckpoint, mutator_usage
from sandbox.structured_v1.search import ArmKind

DIMENSIONS = ("behavior", "risk", "joint")


def checkpoint_report(state: CampaignCheckpoint) -> dict:
    """Retain every reserved opportunity, including failures and incomplete work.

    Coverage is the execution-bound checkpoint projection, not newly judged evidence.
    A failed generation has no agent execution; its mutator receipt is its saved plan.
    Missing plans/costs stay unknown and cannot turn into a free opportunity.
    """
    plans = {plan.opportunity: plan for plan in state.generations}
    selections = {item.opportunity: item for item in state.selections}
    parent_failures = {
        int(item["opportunity"]): item for item in state.failed_parent_evidence
    }
    if len(plans) != len(state.generations) or len(selections) != len(state.selections):
        raise ValueError("duplicate opportunity evidence")
    seen = {dimension: set() for dimension in DIMENSIONS}
    findings: set[str] = set()
    totals = {key: 0 for key in state.usage.model_dump() if key != "opportunities"}
    curves, missing = [], []
    episodes = failures = no_new = repeated = parent_evidence_failures = 0
    signatures = set()
    reservations = sorted(state.reservations.values(), key=lambda item: item.opportunity)
    if [item.opportunity for item in reservations] != list(range(len(reservations))):
        raise ValueError("non-contiguous opportunity evidence")
    for reservation in reservations:
        index = reservation.opportunity
        plan, selection = plans.get(index), selections.get(index)
        parent_failure = parent_failures.get(index)
        parent_evidence_failures += parent_failure is not None
        if parent_failure is not None and (
            plan is not None or selection is not None or reservation.submitted
            or not reservation.settled
        ):
            raise ValueError("parent evidence failure has inconsistent opportunity evidence")
        actual = reservation.actual
        coverage = None
        if selection is not None:
            # These are the existing generation identities in TwoArmSearch._generate.
            candidate = (
                selection.parent_id if selection.arm == ArmKind.RANDOM_INDEPENDENT
                else f"root-restart-{index}" if selection.root_restart
                else f"opportunity-{index}"
            )
            coverage = state.search.parent_coverage.get(candidate)
            if (
                coverage is None or coverage.execution is None
                or coverage.execution.candidate_id != candidate
                or state.lineage.get(candidate) != selection.parent_id
            ):
                raise ValueError(f"missing execution binding for opportunity {index}")
        is_failure = reservation.settled and not reservation.submitted
        if actual is None and is_failure and plan is not None:
            actual = mutator_usage(plan)
        costs = actual.model_dump() if actual is not None else {}
        if reservation.settled:
            for key in totals:
                value = costs.get(key)
                totals[key] = (
                    None if totals[key] is None or value is None else totals[key] + value
                )
        delta = dict.fromkeys(DIMENSIONS, 0)
        if coverage is not None:
            episodes += 1
            signature = tuple(
                frozenset(atom.key for atom in getattr(coverage, dimension))
                for dimension in DIMENSIONS
            )
            repeated += signature in signatures
            signatures.add(signature)
            for dimension in DIMENSIONS:
                keys = {atom.key for atom in getattr(coverage, dimension)}
                delta[dimension] = len(keys - seen[dimension])
                seen[dimension].update(keys)
            findings.update(coverage.findings)
        elif reservation.submitted:
            missing.append(f"opportunity-{index}: no settled coverage evidence")
        failures += is_failure
        if reservation.settled:
            no_new = no_new + 1 if not any(delta.values()) else 0
        if plan is None and parent_failure is None:
            missing.append(f"opportunity-{index}: missing mutation plan")
        curves.append({
            "opportunity": index,
            "settled": reservation.settled,
            "episode_id": coverage.episode_id if coverage else None,
            "generation_failed": is_failure,
            "failure_class": (
                str(parent_failure["reason"]) if parent_failure is not None
                else plan.failure_class if plan and is_failure else None
            ),
            "planned_operation": plan.operation if plan else None,
            "planned_local": plan.operation is not None if plan else None,
            "executed_local": bool(coverage and selection and not selection.root_restart
                                   and selection.arm != ArmKind.RANDOM_INDEPENDENT),
            "selection_reason": selection.reason if selection else None,
            "feedback_sources": selection.feedback_sources if selection else None,
            "delta": delta,
            "cumulative": {dimension: len(keys) for dimension, keys in seen.items()},
            "unique_findings": len(findings),
            "unknown_obligations": (
                sum(value == "unknown" for value in coverage.outcome_by_obligation.values())
                if coverage else None
            ),
            "judgment_missing": coverage.judgment_missing if coverage else None,
            "no_new_streak": no_new,
            "cost": costs or None,
            "cumulative_cost": dict(totals),
        })
    ledger = state.search.ledger.global_seen
    for dimension in DIMENSIONS:
        if seen[dimension] != set(getattr(ledger, dimension)):
            raise ValueError(f"checkpoint {dimension} ledger does not match executed coverage")
    costs_match = all(totals[key] == getattr(state.usage, key) for key in totals)
    settled = sum(item.settled for item in reservations)
    if settled != state.usage.opportunities or not costs_match:
        raise ValueError("checkpoint usage does not reconcile with opportunity receipts")
    return {
        "kind": "development-checkpoint-report-not-an-advantage-verdict",
        "complete": (not state.stopped and state.usage.complete
                     and settled == state.limits.opportunities
                     and len(reservations) == settled and not missing),
        "stop_reason": state.stop_reason,
        "curves": curves,
        "episodes": episodes,
        "generation_failures": failures - parent_evidence_failures,
        "parent_evidence_failures": parent_evidence_failures,
        "repeated_coverage_episodes": repeated,
        "repeat_rate": repeated / episodes if episodes else None,
        "usage_reconciled": costs_match,
        "usage_complete": state.usage.complete,
        "missing": missing,
        "limitations": [
            "Checkpoint projection only; raw bundle/Oracle verification is separate.",
            "Material read windows and legitimate routes require original bundles.",
            "Recovery count and end-to-end stage time require external logs.",
            "A refused child may retain an accepted wording plan with no refusal code.",
        ],
    }
