from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from sandbox.scenarios.error_capable import (
    AttackMode,
    CoverageFeedback,
    EffectStage,
    ErrorCapableMode,
    SelectorDecision,
    TaskFamily,
    attack_spec,
    task_family_spec,
)
from sandbox.scenarios.error_capable_local import (
    EpisodeReceipt,
    LocalCoverageLedger,
    SelectorReceipt,
    persist_episode,
    restore_episode,
    run_local_path_probe,
)


def _decision() -> SelectorDecision:
    # A cell is six ids, and the fixture names all of them.  The carrier is read off the mechanism
    # rather than written here, so it cannot drift from the menu.
    attack = attack_spec(AttackMode.PUBLIC_LINK_EXFILTRATION)
    return SelectorDecision(
        task_family=TaskFamily.SUMMARY_DELIVERY,
        task_variant=task_family_spec(TaskFamily.SUMMARY_DELIVERY).variants[0].variant_id,
        path_id="summary.shared-link",
        attack_mode=attack.attack_mode,
        attack_carrier=attack.carriers[0],
        layout_id="balanced-8",
        rationale="cover a registered joint gap",
    )


def test_selector_receipt_separates_guided_and_random_inputs() -> None:
    feedback = CoverageFeedback(menu_gaps=("summary",), target_menu_cells=1)
    guided = SelectorReceipt.record(
        episode_id="episode-1",
        mode=ErrorCapableMode.GUIDED,
        model_identity="model.test",
        request={"feedback": feedback.model_dump(mode="json")},
        decision=_decision(),
        feedback=feedback,
    )
    assert guided.feedback_digest == feedback.canonical_digest()
    random = SelectorReceipt.record(
        episode_id="episode-2",
        mode=ErrorCapableMode.RANDOM,
        model_identity="model.test",
        request={"seed": 2},
        decision=_decision(),
        feedback=None,
    )
    assert random.feedback_digest is None
    with pytest.raises(ValueError, match="random"):
        SelectorReceipt.record(
            episode_id="episode-3",
            mode=ErrorCapableMode.RANDOM,
            model_identity="model.test",
            request={},
            decision=_decision(),
            feedback=feedback,
        )


def test_coverage_ledger_exposes_four_increment_cases() -> None:
    ledger = LocalCoverageLedger()
    ledger, first = ledger.observe(("b1",), ("r1",))
    ledger, repeat = ledger.observe(("b1",), ("r1",))
    ledger, behavior_only = ledger.observe(("b2",), ("r1",))
    ledger, risk_only = ledger.observe(("b1",), ("r2",))
    ledger, joint = ledger.observe(("b2",), ("r2",))
    assert (first.new_behavior, first.new_risk, first.new_joint) == (True, True, True)
    assert (repeat.new_behavior, repeat.new_risk, repeat.new_joint) == (False, False, False)
    assert behavior_only.new_behavior and not behavior_only.new_risk
    assert risk_only.new_risk and not risk_only.new_behavior
    assert joint.new_joint
    # The delta above is a real, usable signal; the *feedback* is not, and this ledger refuses to
    # produce one.  It used to hand back the covered sets under the `*_gaps` names, which would have
    # pointed a selector at ground it had already covered (`RA-CLOSE-02`).  Coverage gaps are the
    # complement of the observed set over a frozen target space, which `CoverageLedger` computes
    # from execution evidence.
    with pytest.raises(NotImplementedError, match="CoverageLedger"):
        ledger.feedback()


def test_idempotent_episode_receipts() -> None:
    receipt = EpisodeReceipt(
        episode_id="episode-1",
        plan_digest="sha256:" + "a" * 64,
        trace_digest="sha256:" + "b" * 64,
        stage=EffectStage.READ_ONLY,
        completed=False,
        interrupted=True,
    )
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        path = persist_episode(receipt, root)
        assert restore_episode(path) == receipt
        assert persist_episode(receipt, root) == path
        with pytest.raises(ValueError, match="different evidence"):
            persist_episode(
                receipt.model_copy(update={"trace_digest": "sha256:" + "c" * 64}),
                root,
            )


def test_each_task_family_has_two_real_tool_paths() -> None:
    from sandbox.scenarios.error_capable import (
        ATTACK_SPECS,
        EpisodeKind,
        SelectorRequest,
        build_plan,
        materialize_scenario,
    )

    request = SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=9,
        agent_model_name="model.test",
        selector_model_name="model.test",
        available_task_families=tuple(TaskFamily),
        available_attacks=tuple(AttackMode),
        feedback=CoverageFeedback(menu_gaps=("path",), target_menu_cells=1),
    )
    path_pairs = {
        TaskFamily.SUMMARY_DELIVERY: ("summary.draft-confirm-email", "summary.shared-link"),
        TaskFamily.CALENDAR_COORDINATION: ("calendar.mail-to-event", "calendar.event-followup"),
        TaskFamily.ACCESS_REVIEW: ("access.report-only", "access.draft-review"),
    }
    for index, (family, paths) in enumerate(path_pairs.items()):
        traces = []
        for path_index, path_id in enumerate(paths):
            plan = build_plan(
                request,
                SelectorDecision(
                    task_family=family,
                    task_variant=task_family_spec(family).variants[0].variant_id,
                    path_id=path_id,
                    attack_mode=ATTACK_SPECS[index].attack_mode,
                    # The same carrier `build_plan` is given below.
                    attack_carrier=ATTACK_SPECS[index].carriers[0],
                    layout_id="balanced-8",
                    episode_kind=EpisodeKind.ATTACK,
                    rationale="real local path probe",
                ),
                episode_id=f"local-{family.value}-{path_index}",
                task_file_id=f"local-{family.value}-{path_index}-task",
                file_ids=tuple(
                    f"local-{family.value}-{path_index}-{role}"
                    for role in ("task", "public", "restricted", "history")
                ),
                attack_carrier=ATTACK_SPECS[index].carriers[0],
                model_name="model.test",
            )
            result = run_local_path_probe(materialize_scenario(plan), path_id=path_id)
            assert result.completed
            assert result.committed
            assert result.task_family == family.value
            traces.append(result.tool_names)
        assert traces[0] != traces[1]
