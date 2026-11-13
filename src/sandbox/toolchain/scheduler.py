"""Coverage-feedback scheduling: four arms over one candidate pool.

``TASK.md`` TASK-05 fixes the pool, the mutation actions and the phase rules, and
compares four arms: no coverage, behaviour only, risk only, both.  The point is to
separate "coverage works" from "repair strategy works", so this module must not
invent candidates: it only **orders** a pool it is given.

What a candidate can declare before running (its *targets*):

* ``target_behaviour_key`` -- the behaviour its mutation is meant to provoke;
* ``target_risk_key`` -- the risk family it is aimed at;
* ``target_risk_level`` -- the level it is aimed at, 1..6.

After a run the scheduler learns the *observed* keys and levels.  A candidate
whose target was observed is "covered" and falls in priority; a candidate whose
target level is still low is what a risk arm keeps offering, which is the same
rule the selector prompt states about reading levels from the bottom.

The arms differ only in how they weigh those facts, and ``no_coverage`` is not a
degenerate arm: it is the control that says how much of any gain came from the
ordering at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Arm(StrEnum):
    NO_COVERAGE = "no_coverage"
    BEHAVIOUR_ONLY = "behaviour_only"
    RISK_ONLY = "risk_only"
    DUAL = "dual"


@dataclass(frozen=True)
class SchedulableCandidate:
    """A pool entry plus the targets it declares before execution."""

    candidate_id: str
    target_behaviour_key: str | None = None
    target_risk_key: str | None = None
    target_risk_level: int | None = None
    cost: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "target_behaviour_key": self.target_behaviour_key,
            "target_risk_key": self.target_risk_key,
            "target_risk_level": self.target_risk_level,
            "cost": self.cost,
        }


@dataclass(frozen=True)
class Observation:
    """What one executed candidate actually produced."""

    candidate_id: str
    behaviour_keys: tuple[str, ...] = ()
    risk_keys: tuple[str, ...] = ()
    risk_levels: dict[str, int] = field(default_factory=dict)
    violation: bool = False
    tokens: int = 0


@dataclass
class CoverageHistory:
    """Everything the arms are allowed to know."""

    behaviour_keys: set[str] = field(default_factory=set)
    risk_keys: set[str] = field(default_factory=set)
    risk_levels: dict[str, int] = field(default_factory=dict)
    executed: list[str] = field(default_factory=list)
    violations: set[str] = field(default_factory=set)
    tokens: int = 0

    def record(self, observation: Observation) -> None:
        self.behaviour_keys.update(observation.behaviour_keys)
        self.risk_keys.update(observation.risk_keys)
        for key, level in observation.risk_levels.items():
            self.risk_levels[key] = max(self.risk_levels.get(key, 0), level)
        self.executed.append(observation.candidate_id)
        if observation.violation:
            self.violations.add(observation.candidate_id)
        self.tokens += observation.tokens

    def known_behaviour(self, candidate: SchedulableCandidate) -> bool:
        return bool(candidate.target_behaviour_key) and (
            candidate.target_behaviour_key in self.behaviour_keys
        )

    def known_risk(self, candidate: SchedulableCandidate) -> bool:
        if candidate.target_risk_key is None:
            return False
        if candidate.target_risk_key in self.risk_keys:
            return True
        target = candidate.target_risk_level
        current = self.risk_levels.get(candidate.target_risk_key, 0)
        return target is not None and current >= target


def schedule(
    arm: Arm,
    pool: list[SchedulableCandidate],
    history: CoverageHistory,
    *,
    limit: int | None = None,
) -> list[SchedulableCandidate]:
    """Order the pool for one arm, dropping candidates already executed.

    Every arm skips a candidate it has already run: re-running is the repeat
    exploration the experiment counts, not a scheduling decision.  Ties keep pool
    order so the arms differ only by their stated rule.
    """

    pending = [item for item in pool if item.candidate_id not in history.executed]
    if arm is Arm.NO_COVERAGE:
        ordered = list(pending)
    else:
        rank = {
            Arm.BEHAVIOUR_ONLY: _behaviour_rank,
            Arm.RISK_ONLY: _risk_rank,
            Arm.DUAL: _dual_rank,
        }.get(arm)
        if rank is None:  # pragma: no cover - StrEnum makes this unreachable
            raise ValueError(f"unknown arm: {arm}")
        ordered = [
            item
            for _, item in sorted(
                enumerate(pending), key=lambda pair: rank(pair[1], history, pair[0])
            )
        ]
    return ordered[:limit] if limit is not None else ordered


def _behaviour_rank(
    candidate: SchedulableCandidate, history: CoverageHistory, pool_index: int
) -> tuple[int, int]:
    """Unobserved behaviour targets first; ties keep pool order."""

    return (1 if history.known_behaviour(candidate) else 0, pool_index)


def _risk_rank(
    candidate: SchedulableCandidate, history: CoverageHistory, pool_index: int
) -> tuple[int, int, int]:
    """Lowest covered level first -- an untested risk is what an episode is for."""

    level = 0
    if candidate.target_risk_key is not None:
        level = history.risk_levels.get(candidate.target_risk_key, 0)
    return (1 if history.known_risk(candidate) else 0, level, pool_index)


def _dual_rank(
    candidate: SchedulableCandidate, history: CoverageHistory, pool_index: int
) -> tuple[int, int, int, int]:
    """The joint rule: a candidate counts as covered only when both halves are."""

    behaviour_known = 1 if history.known_behaviour(candidate) else 0
    risk_known = 1 if history.known_risk(candidate) else 0
    level = 0
    if candidate.target_risk_key is not None:
        level = history.risk_levels.get(candidate.target_risk_key, 0)
    return (behaviour_known + risk_known, level, behaviour_known, pool_index)


def summarise(history: CoverageHistory) -> dict[str, Any]:
    """The TASK-05 read-outs, per arm ledger."""

    return {
        "executed": len(history.executed),
        "violations": len(history.violations),
        "distinct_violations": len(history.violations),
        "behaviour_keys": len(history.behaviour_keys),
        "risk_keys": len(history.risk_keys),
        "risk_levels": dict(sorted(history.risk_levels.items())),
        "tokens": history.tokens,
        "tokens_per_violation": (
            round(history.tokens / len(history.violations), 1) if history.violations else None
        ),
        "repeat_exploration": len(history.executed) - len(set(history.executed)),
    }
