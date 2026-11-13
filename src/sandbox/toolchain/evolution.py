"""The TASK-05 harness: one arm of the coverage experiment, driven by the scheduler.

``scheduler.py`` orders a pool but knows nothing about the scenario; the campaign
knows the scenario but only accepts a ``CampaignSelector``.  This module is the
joint: a selector that builds the pool from ``legal_combinations(request)`` on
its first call, folds the feedback into a ``CoverageHistory``, and returns the
one candidate the arm's rule puts first.

What it does **not** do: choose by model.  That is the point of the arm -- the
treatment is the ordering rule, so a model's preference would be a second
treatment layered on the first and the comparison would stop meaning anything.

Targets are declared before running, from the frozen menu itself:

* ``target_risk_key`` is the mechanism's ``target_violation_type`` -- the harm
  that mechanism is written to provoke;
* ``target_behaviour_key`` is the ``family.path`` neighborhood -- the direction
  the combination belongs to.

Both are approximations, and deliberately so: a candidate's real behaviour is
only known after it runs, which is what the observation half is for.
"""

from __future__ import annotations

from typing import Any

from sandbox.scenarios.error_capable import (
    AttackMode,
    SelectorDecision,
    TaskFamily,
    attack_spec,
)
from sandbox.scenarios.error_capable_selector import legal_combinations
from sandbox.toolchain.diagnosis import (
    Action,
    RepairLedger,
    StageFacts,
    Trivalent,
    next_action,
)
from sandbox.toolchain.scheduler import (
    Arm,
    CoverageHistory,
    SchedulableCandidate,
    schedule,
)

#: Targets start at level 1: the mechanism is aimed at one harm, and the level it
#: has actually reached is read back from the feedback, not assumed here.
INITIAL_TARGET_LEVEL = 1


def candidate_id(combination: dict[str, str]) -> str:
    """One stable name for a combination, independent of dict ordering."""

    return "|".join(f"{key}={combination[key]}" for key in sorted(combination))


class CoverageSchedulerSelector:
    """A ``CampaignSelector`` that orders the frozen menu by coverage feedback."""

    name = "toolchain-coverage-scheduler-v1"
    #: Admitted by the campaign on the same footing as ``ScriptedSelector``: this
    #: selector makes no model call, so it is a contract double and not a second
    #: treatment layered on the arm.
    is_contract_selector = True

    def __init__(self, arm: Arm) -> None:
        self.arm = arm
        self._pool: list[SchedulableCandidate] = []
        self._coords: dict[str, dict[str, str]] = {}
        self._history = CoverageHistory()
        self._current: str | None = None
        self._current_attack: str | None = None
        self._ledger = RepairLedger()
        #: The action §16.2 selected for the previous episode (kept for the ledger
        #: dump; the selection itself is driven by the facts, not by the action).
        self.last_action: Action | None = None

    def __call__(
        self, request: Any, history: Any, *, episode_index: int
    ) -> tuple[SelectorDecision, str]:
        del history  # the campaign's sentinel is not a coverage record; feedback is
        if not self._pool:
            self._build_pool(request)
        feedback = getattr(request, "feedback", None)
        self._absorb(feedback)

        # §16.2: diagnose the previous episode from what the feedback can tell,
        # and let the action steer this selection.  The feedback carries no raw
        # trace, so most facts stay UNKNOWN -- which is exactly what the three-
        # valued design is for: unanswerable rows route to review/normal schedule
        # instead of spending a repair on a guess.
        facts = self._facts_from_feedback(feedback)
        action, _allowed = next_action(facts, self._ledger)
        self.last_action = action

        if facts.violation is Trivalent.YES and self._current_attack:
            # MARK_PROTECTION-adjacent exploitation: the previous episode broke a
            # boundary under this mechanism, so untried combinations of the same
            # mechanism come before the risk schedule (§13/§16 "提高相似场景的
            # 探索优先级").  The risk schedule orders whatever remains.
            pending = [
                item for item in schedule(self.arm, self._pool, self._history)
                if self._attack_of(item) == self._current_attack
            ]
            rest = [
                item for item in schedule(self.arm, self._pool, self._history)
                if self._attack_of(item) != self._current_attack
            ]
            ordered = pending + rest
        else:
            ordered = schedule(self.arm, self._pool, self._history)

        if not ordered:
            raise RuntimeError("candidate pool exhausted before the budget ran out")
        candidate = ordered[0]
        self._current = candidate.candidate_id
        self._current_attack = self._attack_of(candidate)
        self._history.executed.append(candidate.candidate_id)

        combination = self._coords[candidate.candidate_id]
        decision = SelectorDecision(
            task_family=TaskFamily(combination["task_family"]),
            path_id=combination["path_id"],
            attack_mode=AttackMode(combination["attack_mode"]),
            attack_carrier=combination["attack_carrier"],
            layout_id=combination["layout_id"],
            rationale=(
                f"arm={self.arm.value} action={action.value} "
                f"selected {candidate.candidate_id} (episode {episode_index})"
            ),
        )
        return decision, f"scheduler[{self.arm.value}] action={action.value} -> {candidate.candidate_id}"

    def _attack_of(self, candidate: SchedulableCandidate) -> str | None:
        combination = self._coords.get(candidate.candidate_id)
        return combination.get("attack_mode") if combination else None

    def _facts_from_feedback(self, feedback: Any) -> StageFacts:
        """Infer what the feedback can tell about the previous episode.

        Only the violation row is answerable from a feedback snapshot; everything
        else needs the trace and stays UNKNOWN.  ``operation_committed`` follows
        the violation row conservatively: a violation implies a committed effect,
        no violation says nothing about commitment.
        """

        delta = getattr(feedback, "violation_delta", None) if feedback is not None else None
        violation = Trivalent.YES if delta else (
            Trivalent.NO if feedback is not None else Trivalent.UNKNOWN
        )
        operation = Trivalent.YES if violation is Trivalent.YES else Trivalent.UNKNOWN
        return StageFacts(
            carrier_exposed=Trivalent.UNKNOWN,
            inducement_reached=Trivalent.UNKNOWN,
            dependency_verified=Trivalent.UNKNOWN,
            operation_committed=operation,
            evidence_complete=Trivalent.UNKNOWN,
            violation=violation,
            refusal_reason=Trivalent.UNKNOWN,
        )

    # -- pool construction -------------------------------------------------------------------

    def _build_pool(self, request: Any) -> None:
        for combination in legal_combinations(request):
            cid = candidate_id(combination)
            if cid in self._coords:
                continue
            self._coords[cid] = combination
            self._pool.append(
                SchedulableCandidate(
                    candidate_id=cid,
                    target_behaviour_key=f"{combination['task_family']}.{combination['path_id']}",
                    target_risk_key=_target_risk(combination["attack_mode"]),
                    target_risk_level=INITIAL_TARGET_LEVEL,
                )
            )

    # -- feedback fold ------------------------------------------------------------------------

    def _absorb(self, feedback: Any) -> None:
        if feedback is None:
            return
        for key in getattr(feedback, "behaviour_keys", ()) or ():
            self._history.behaviour_keys.add(str(key))
        for key in getattr(feedback, "risk_keys", ()) or ():
            self._history.risk_keys.add(str(key))
        for row in getattr(feedback, "risk_dimensions", ()) or ():
            self._absorb_risk_row(row)
        for cell in getattr(feedback, "chosen_menu_cells", ()) or ():
            if isinstance(cell, dict):
                cid = candidate_id({str(k): str(v) for k, v in cell.items()})
                if cid not in self._history.executed:
                    self._history.executed.append(cid)
        for violation in getattr(feedback, "violation_delta", ()) or ():
            if self._current and str(violation):
                self._history.violations.add(self._current)

    def _absorb_risk_row(self, row: Any) -> None:
        """Read one ``risk_dimensions`` row without assuming its exact key names.

        The row's shape has changed before and will change again; what this module
        needs is only "which harm" and "what level", and both are looked up through
        the handful of names the row has used.
        """

        if not isinstance(row, dict):
            return
        key = None
        for name in ("risk_type", "finding_kind", "type", "key", "kind", "name", "dimension"):
            value = row.get(name)
            if isinstance(value, str) and value:
                key = value
                break
        level = 0
        for name in ("level", "reached", "current_level", "current", "value", "score"):
            value = row.get(name)
            if isinstance(value, int):
                level = value
                break
        if key:
            self._history.risk_levels[key] = max(self._history.risk_levels.get(key, 0), level)

    # -- read-outs -----------------------------------------------------------------------------

    def history_summary(self) -> dict[str, Any]:
        from sandbox.toolchain.scheduler import summarise

        return summarise(self._history)


def _target_risk(attack_mode: str) -> str | None:
    try:
        return attack_spec(AttackMode(attack_mode)).target_violation_type.value
    except (StopIteration, ValueError):
        return None
