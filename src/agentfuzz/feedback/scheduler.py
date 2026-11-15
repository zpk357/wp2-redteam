"""The feedback scheduler: the advisor six-term score with hand-set weights.

Score(x) = w1*N_b + w2*N_r + w3*P + w4*U - w5*C - w6*D.  N_b/N_r estimate the
coverage a candidate would add; P its historical reach rate; U its unexplored
degree; C its token cost; D the repeat penalty.  A candidate that spent its
repair budget without progress is excluded from selection until the run ends --
the advisor cap that keeps the loop from drilling one failed sample forever.
"""

from __future__ import annotations

from agentfuzz.feedback.coverage import CoverageDB
from agentfuzz.feedback.stage_diagnosis import (
    ExecutionStage,
    FeedbackAction,
    stage_action,
)

DEFAULT_WEIGHTS = {"nb": 2.0, "nr": 3.0, "p": 1.0, "u": 1.0, "c": 1e-6, "d": 1.0}

#: Rough per-stage reach probability, used until a case has history.
_STAGE_P = {
    ExecutionStage.EFFECT_OBSERVED.value: 1.0,
    ExecutionStage.PARAMS_AFFECTED.value: 0.9,
    ExecutionStage.CHAIN_COMPLETE.value: 0.8,
    ExecutionStage.CHAIN_PARTIAL.value: 0.5,
    ExecutionStage.INJECTION_NOT_READ.value: 0.2,
}


class FeedbackScheduler:
    """Selects the next case and its action from the coverage DB and history."""

    def __init__(self, *, weights: dict[str, float] | None = None,
                 max_repairs_per_chain: int = 4) -> None:
        self.db = CoverageDB()
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.max_repairs_per_chain = max_repairs_per_chain
        self.last_stage: dict[str, str] = {}

    def record(
        self,
        case_id: str,
        *,
        stage: str,
        behavior_key: tuple[str, ...],
        risk_key: tuple[str, ...],
        tokens: int,
        effect_kind: str | None = None,
    ) -> dict[str, Any]:
        """Absorb one settled episode; returns what was newly covered."""

        case = self.db.case(case_id)
        case["attempts"] += 1
        case["tokens"] += tokens
        case["stages_seen"].add(stage)
        new_behavior = self.db.add_behavior(behavior_key)
        new_risk = self.db.add_risk(risk_key)
        if new_behavior or new_risk:
            case["repairs_since_progress"] = 0
        else:
            case["repairs_since_progress"] += 1
        if stage == "effect_observed":
            case["confirmed"] += 1
        self.last_stage[case_id] = stage
        return {
            "new_behavior": new_behavior,
            "new_risk": new_risk,
            "action": self.action_for(case_id),
        }

    def action_for(self, case_id: str) -> FeedbackAction:
        stage = self.last_stage.get(case_id)
        if stage is None:
            return FeedbackAction.PROMOTE_CASE
        case = self.db.case(case_id)
        return stage_action(
            ExecutionStage(stage),
            duplicate_finding=case["repairs_since_progress"] >= self.max_repairs_per_chain,
            new_coverage=True,
        )

    def _score(self, case_id: str) -> float:
        case = self.db.case(case_id)
        w = self.weights
        stages_seen = case["stages_seen"]
        nb = max(0, len(_STAGE_P) - len(stages_seen))  # stages this case has not reached
        nr = 1.0 if case["confirmed"] == 0 else 0.3
        best_stage = max(
            (_STAGE_P.get(s, 0.0) for s in stages_seen), default=0.5
        )
        p = best_stage
        u = 1.0 / (1.0 + case["attempts"])
        c = float(case["tokens"])
        d = float(case["repairs_since_progress"])
        return w["nb"] * nb + w["nr"] * nr + w["p"] * p + w["u"] * u - w["c"] * c - w["d"] * d

    def select(self, candidates: list[str]) -> tuple[str, FeedbackAction | None]:
        """Pick the next case and the action its last stage demands.

        Candidates past their repair cap are excluded; the cap is what makes the
        loop switch away from a failed sample instead of drilling it forever.
        """

        eligible = [
            c for c in candidates
            if self.db.case(c)["repairs_since_progress"] < self.max_repairs_per_chain
        ]
        if not eligible:
            eligible = list(candidates)  # everything capped: pick the least bad
        best = sorted(eligible, key=lambda c: (-self._score(c), c))[0]
        action = self.action_for(best) if best in self.last_stage else None
        return best, action
