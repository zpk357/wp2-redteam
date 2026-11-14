"""Tests for the TASK-05 harness selector.

The selector must build its pool from the frozen menu, fold the feedback into the
same history the scheduler uses, and let the four arms disagree -- if they do not,
the campaign would measure nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.scenarios.error_capable import AttackMode, TaskFamily, TASK_FAMILY_SPECS
from sandbox.toolchain.evolution import CoverageSchedulerSelector, candidate_id
from sandbox.toolchain.scheduler import Arm


def _request(feedback=None) -> SimpleNamespace:
    spec = next(item for item in TASK_FAMILY_SPECS if item.task_family is TaskFamily.SUMMARY_DELIVERY)
    return SimpleNamespace(
        available_task_families=(TaskFamily.SUMMARY_DELIVERY,),
        available_paths=tuple(spec.path_ids[:2]),
        available_attacks=(AttackMode.CROSS_FILE_SPLICE, AttackMode.AUDIENCE_BROADENING),
        available_carriers=("request_note", "review_comment"),
        available_layouts=("balanced-9", "distributed-11"),
        feedback=feedback,
        require_unobserved=True,
    )


def _feedback(
    *,
    behaviour_keys=(),
    risk_keys=(),
    risk_dimensions=(),
    chosen=(),
    violations=(),
):
    return SimpleNamespace(
        behaviour_keys=behaviour_keys,
        risk_keys=risk_keys,
        risk_dimensions=risk_dimensions,
        violation_delta=violations,
        chosen_menu_cells=chosen,
    )


class _History:
    """The campaign's sentinel; this selector ignores it on purpose."""


def test_the_first_call_builds_the_pool_and_returns_a_legal_decision():
    selector = CoverageSchedulerSelector(Arm.NO_COVERAGE)
    decision, raw = selector(_request(), _History(), episode_index=0)
    assert decision.task_family is TaskFamily.SUMMARY_DELIVERY
    assert decision.path_id
    assert decision.attack_mode in (AttackMode.CROSS_FILE_SPLICE, AttackMode.AUDIENCE_BROADENING)
    assert decision.attack_carrier in ("request_note", "review_comment")
    assert decision.layout_id in ("balanced-9", "distributed-11")
    assert "no_coverage" in raw


def test_no_coverage_takes_the_pool_in_menu_order():
    selector = CoverageSchedulerSelector(Arm.NO_COVERAGE)
    first, _ = selector(_request(), _History(), episode_index=0)
    second, _ = selector(_request(), _History(), episode_index=1)
    # layout is the innermost axis of frozen_menu's loop, so the first two entries
    # differ there and nowhere else.
    assert (first.task_family, first.path_id, first.attack_mode) == (
        second.task_family, second.path_id, second.attack_mode
    )
    assert first.layout_id != second.layout_id


def test_chosen_cells_are_skipped_after_they_are_reported():
    selector = CoverageSchedulerSelector(Arm.NO_COVERAGE)
    first, _ = selector(_request(), _History(), episode_index=0)
    cell = {
        "task_family": first.task_family.value,
        "path_id": first.path_id,
        "attack_mode": first.attack_mode.value,
        "attack_carrier": first.attack_carrier,
        "layout_id": first.layout_id,
    }
    feedback = _feedback(chosen=(cell,))
    second, _ = selector(_request(feedback), _History(), episode_index=1)
    assert candidate_id(
        {
            "task_family": second.task_family.value,
            "path_id": second.path_id,
            "attack_mode": second.attack_mode.value,
            "attack_carrier": second.attack_carrier,
            "layout_id": second.layout_id,
        }
    ) != candidate_id(cell)


def test_risk_feedback_moves_the_risk_arm_but_not_the_control():
    # Mark the deployment mechanism's harm as already at a high level; a risk arm
    # should then prefer a combination aimed at a different type.
    feedback = _feedback(
        risk_keys=("content_release",),
        risk_dimensions=({"risk_type": "content_release", "level": 5},),
    )
    risk_selector = CoverageSchedulerSelector(Arm.RISK_ONLY)
    decision, _ = risk_selector(_request(feedback), _History(), episode_index=0)
    assert decision.attack_mode is AttackMode.AUDIENCE_BROADENING

    control = CoverageSchedulerSelector(Arm.NO_COVERAGE)
    control_decision, _ = control(_request(feedback), _History(), episode_index=0)
    assert control_decision.attack_mode is AttackMode.CROSS_FILE_SPLICE


def test_the_pool_exhausts_rather_than_repeating():
    selector = CoverageSchedulerSelector(Arm.NO_COVERAGE)
    request = _request()
    # 1 family x 2 paths x 2 mechanisms x 2 carriers x 2 layouts = 16 combinations.
    seen = set()
    for index in range(16):
        decision, _ = selector(request, _History(), episode_index=index)
        seen.add(
            (
                decision.task_family.value,
                decision.path_id,
                decision.attack_mode.value,
                decision.attack_carrier,
                decision.layout_id,
            )
        )
    assert len(seen) == 16
    with pytest.raises(RuntimeError):
        selector(request, _History(), episode_index=16)


def test_candidate_id_is_independent_of_dict_order():
    a = {"task_family": "f", "path_id": "p", "attack_mode": "m", "attack_carrier": "c", "layout_id": "l"}
    b = {"layout_id": "l", "attack_carrier": "c", "attack_mode": "m", "path_id": "p", "task_family": "f"}
    assert candidate_id(a) == candidate_id(b)


# -- diagnosis-driven selection (TASK-06 integration) ---------------------------------------

def _violation_feedback() -> SimpleNamespace:
    return SimpleNamespace(
        behaviour_keys=(),
        risk_keys=("content_release",),
        risk_dimensions=(),
        violation_delta=("content_release",),
        chosen_menu_cells=(),
    )


def _risk_feedback() -> SimpleNamespace:
    return SimpleNamespace(
        behaviour_keys=(),
        risk_keys=("audience_mismatch",),
        risk_dimensions=({"risk_type": "audience_mismatch", "level": 4},),
        violation_delta=(),
        chosen_menu_cells=(),
    )


def test_a_violation_round_prefers_untried_combinations_of_the_same_mechanism():
    """§16.2 R4/§13 "提高相似场景的探索优先级": break a boundary, mine it."""

    selector = CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS)
    selector(_request(), _History(), episode_index=0)
    first_attack = selector._current_attack
    # The previous episode broke a boundary under the same mechanism.
    second, _ = selector(_request(_violation_feedback()), _History(), episode_index=1)
    assert selector._current_attack == first_attack
    # and it is a different, untried combination of that mechanism.
    assert second.attack_mode.value == first_attack


def test_no_violation_keeps_the_risk_schedule():
    """Without a violation the arm behaves exactly as before the integration."""

    selector = CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS)
    decision, raw = selector(_request(_risk_feedback()), _History(), episode_index=0)
    # audience_mismatch is already at level 4, so the risk arm picks a different type.
    assert decision.attack_mode is AttackMode.CROSS_FILE_SPLICE
    assert "mark_protection" not in raw


def test_the_repair_ledger_counts_violation_rounds():
    selector = CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS)
    selector(_request(), _History(), episode_index=0)
    selector(_request(_violation_feedback()), _History(), episode_index=1)
    counts = selector._ledger.to_dict()["counts"]
    assert counts.get("R4", 0) == 0  # R4 is mark_protection; a plain violation is
    # exploitation, not protection-marking -- the ledger only counts what §16.2
    # budgets, and exploitation is not a budgeted repair.


# -- per-arm gating (TASK-06 real-model validation: three arms over one harness) -------------


def test_other_arms_do_not_mine_a_violation():
    """The treatment is the diagnosis layer, so control arms keep the plain schedule.

    The published two-arm numbers came from selectors that had no §16 layer at all;
    the rerun of those arms must reproduce exactly that behaviour, violation or not.
    """

    for arm in (Arm.NO_COVERAGE, Arm.RISK_ONLY):
        selector = CoverageSchedulerSelector(arm)
        selector(_request(), _History(), episode_index=0)
        first_attack = selector._current_attack
        decision, raw = selector(_request(_violation_feedback()), _History(), episode_index=1)
        # No §16 action was taken and none is reported: these arms neither diagnose
        # nor exploit, whatever the feedback says.
        assert selector.last_action is None
        assert "action=" not in raw
        if arm is Arm.NO_COVERAGE:
            # The control ignores feedback outright: its second pick is plain menu
            # order, which for this menu is the same mechanism at the next layout.
            assert decision.attack_mode.value == first_attack
        else:
            # The risk arm reacts to the reported risk key, not to the violation:
            # content_release is now a known risk, so it moves off the mechanism --
            # which is the risk schedule's own doing, not same-mechanism mining
            # (the mining would have stayed on it despite the key).
            assert decision.attack_mode.value != first_attack


def test_only_the_diagnosis_arm_declares_tps():
    """TPS rotation is part of the third arm's treatment, not the harness's."""

    assert CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS).uses_payload_variants is True
    assert CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS).diagnoses is True
    for arm in (Arm.NO_COVERAGE, Arm.RISK_ONLY, Arm.BEHAVIOUR_ONLY, Arm.DUAL):
        selector = CoverageSchedulerSelector(arm)
        assert selector.uses_payload_variants is False
        assert selector.diagnoses is False


def test_absorb_final_closes_the_ledger_after_the_last_episode():
    """The final Episode's violation has no next selector call to ride on.

    Measured in the 2026-11-14 three-arm batch: every arm whose final Episode
    violated under-counted its ledger by exactly one.  The campaign now does one
    final absorb; this holds the contract it relies on.
    """

    selector = CoverageSchedulerSelector(Arm.DIAGNOSIS_TPS)
    selector(_request(), _History(), episode_index=0)
    assert selector.history_summary()["violations"] == 0
    selector.absorb_final(_violation_feedback())
    summary = selector.history_summary()
    assert summary["violations"] == 1
    assert selector._current in selector._history.violations
