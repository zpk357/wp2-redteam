"""Tests for the four-arm coverage scheduler.

TASK-05's experiment only means something if the arms genuinely differ, so one
test here shows four orders over the same four candidates and the same history.
"""

from __future__ import annotations

from sandbox.toolchain.scheduler import (
    Arm,
    CoverageHistory,
    Observation,
    SchedulableCandidate,
    schedule,
    summarise,
)


def _pool() -> list[SchedulableCandidate]:
    return [
        SchedulableCandidate("cand.a", "b.a", "content_release", 1),
        SchedulableCandidate("cand.b", "b.b", "audience_mismatch", 3),
        SchedulableCandidate("cand.c", "b.c", "content_release", 1),
        SchedulableCandidate("cand.d", "b.d", "audience_mismatch", 1),
    ]


def _history() -> CoverageHistory:
    """a's behaviour is already known; c has the lowest risk level."""

    history = CoverageHistory()
    history.behaviour_keys.add("b.a")
    history.risk_keys.update({"content_release", "audience_mismatch"})
    history.risk_levels.update({"content_release": 0, "audience_mismatch": 3})
    return history


def _ids(order: list[SchedulableCandidate]) -> list[str]:
    return [item.candidate_id for item in order]


def test_no_coverage_keeps_pool_order():
    assert _ids(schedule(Arm.NO_COVERAGE, _pool(), _history())) == [
        "cand.a", "cand.b", "cand.c", "cand.d"
    ]


def test_behaviour_arm_ranks_a_covered_target_last():
    order = _ids(schedule(Arm.BEHAVIOUR_ONLY, _pool(), _history()))
    assert order == ["cand.b", "cand.c", "cand.d", "cand.a"]


def test_risk_arm_orders_by_level_then_pool_order():
    order = _ids(schedule(Arm.RISK_ONLY, _pool(), _history()))
    # content_release at level 0: a and c (pool order); audience_mismatch at 3:
    # b and d keep their pool order, so b comes before d.
    assert order == ["cand.a", "cand.c", "cand.b", "cand.d"]


def test_dual_arm_ranks_fully_uncovered_first():
    order = _ids(schedule(Arm.DUAL, _pool(), _history()))
    # a is covered on both halves (behaviour known, risk known) -> last.
    assert order[-1] == "cand.a"
    assert order[0] in {"cand.c", "cand.d"}


def test_the_four_arms_produce_at_least_three_orders():
    history = _history()
    orders = {tuple(_ids(schedule(arm, _pool(), history))) for arm in (
        Arm.NO_COVERAGE, Arm.BEHAVIOUR_ONLY, Arm.RISK_ONLY, Arm.DUAL
    )}
    assert len(orders) >= 3


def test_executed_candidates_are_always_skipped():
    history = _history()
    history.record(Observation("cand.a"))
    for arm in (Arm.NO_COVERAGE, Arm.BEHAVIOUR_ONLY, Arm.RISK_ONLY, Arm.DUAL):
        assert "cand.a" not in _ids(schedule(arm, _pool(), history))


def test_limit_truncates_after_ordering():
    assert _ids(schedule(Arm.RISK_ONLY, _pool(), _history(), limit=1)) == ["cand.a"]


def test_history_accumulates_levels_and_flags_repeats():
    history = _history()
    history.record(Observation("cand.a", tokens=1200, violation=True))
    summary = summarise(history)
    assert summary["tokens"] == 1200
    assert summary["tokens_per_violation"] == 1200.0
    assert summary["risk_levels"] == {"audience_mismatch": 3, "content_release": 0}

    history.record(Observation("cand.a", tokens=800))
    assert summarise(history)["repeat_exploration"] == 1
    assert summarise(history)["tokens"] == 2000


def test_tokens_per_violation_is_none_without_violations():
    history = CoverageHistory()
    history.record(Observation("x", tokens=500))
    assert summarise(history)["tokens_per_violation"] is None


def test_a_later_level_never_lowers_a_recorded_one():
    history = CoverageHistory()
    history.record(Observation("a", risk_keys=("k",), risk_levels={"k": 4}))
    history.record(Observation("b", risk_keys=("k",), risk_levels={"k": 2}))
    assert history.risk_levels["k"] == 4


# -- RANDOM_UNIFORM: the competitive scheduling baseline (SPEC §1.1.3) -----------------------


def test_random_uniform_is_reproducible_within_a_seed():
    first = _ids(schedule(Arm.RANDOM_UNIFORM, _pool(), _history(), salt="610710000"))
    second = _ids(schedule(Arm.RANDOM_UNIFORM, _pool(), _history(), salt="610710000"))
    assert first == second


def test_random_uniform_draws_a_different_order_per_seed():
    orders = {
        tuple(_ids(schedule(Arm.RANDOM_UNIFORM, _pool(), _history(), salt=salt)))
        for salt in ("610710000", "610711000", "610712000", "610713000")
    }
    assert len(orders) >= 2  # four draws over four candidates must not all coincide


def test_random_uniform_ignores_the_ledger_entirely():
    """Blind to feedback: covered and uncovered candidates keep the same hash order."""

    blind = _ids(schedule(Arm.RANDOM_UNIFORM, _pool(), _history(), salt="s1"))
    bare = _ids(schedule(Arm.RANDOM_UNIFORM, _pool(), CoverageHistory(), salt="s1"))
    assert blind == bare


def test_random_uniform_skips_executed_candidates():
    history = _history()
    history.record(Observation("cand.c"))
    order = _ids(schedule(Arm.RANDOM_UNIFORM, _pool(), history, salt="s1"))
    assert "cand.c" not in order
    assert len(order) == 3


def test_random_uniform_differs_from_the_menu_order():
    pool = _pool()
    assert _ids(schedule(Arm.RANDOM_UNIFORM, pool, CoverageHistory(), salt="610710000")) != _ids(
        schedule(Arm.NO_COVERAGE, pool, CoverageHistory())
    )
