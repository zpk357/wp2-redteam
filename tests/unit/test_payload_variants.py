"""Tests for the TPS payload-variant rotation.

`SPEC.md` §14.3 puts the boundary where it matters: the wording is **prepared**
(registered variants), the search phase only *selects* among them -- it never
writes text.  These tests hold that boundary from the scene side: episode 0
renders the baseline, later episodes rotate, and every variant still names the
real resources its mechanism needs -- a variant that could not be carried out
would measure the world's reach rather than the Agent's judgement.
"""

from __future__ import annotations

import pytest

from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    AttackMode,
    CoverageFeedback,
    EpisodeKind,
    EpisodeScenarioPlan,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TASK_FAMILY_SPECS,
    TaskFamily,
    build_plan,
    materialize_scenario,
    task_family_spec,
)
from sandbox.scenarios.error_capable_world import (
    BENIGN_CARRIER_NOTE,
    DELETION_TARGET_LOGGED,
    DELETION_TARGET_PLAN,
    PAYLOAD_VARIANT_COUNT,
    PUBLIC_FACT_VALUE,
    RESTRICTED_FACT_VALUE,
    _episode_ordinal,
    attack_payload,
    carrier_ids,
    layout_roles,
    materialize_world,
    payload_variant_for,
)

SOURCE = "/w/working-worksheet"
LEDGER = "/w/audience-ledger"
INDEX = "/w/work-index"
KW = {"source_path": SOURCE, "ledger_path": LEDGER, "index_path": INDEX}


def _plan(attack_mode: AttackMode, episode_id: str = "campaign.guided.000") -> EpisodeScenarioPlan:
    family = task_family_spec(TaskFamily.SUMMARY_DELIVERY)
    return EpisodeScenarioPlan(
        episode_id=episode_id,
        task_family=TaskFamily.SUMMARY_DELIVERY,
        task_file_id="file.0",
        file_ids=tuple(f"file.{index}" for index in range(9)),
        file_roles=layout_roles("balanced-9"),
        path_id=family.path_ids[0],
        attack_mode=attack_mode,
        attack_carrier="request_note",
        attack_carrier_file_id="file.0",
        attack_carrier_field="request_note",
        allowed_channels=family.action_channels,
        seed=0,
        model_name="test-model",
        selection_rationale="variant test",
    )


def _text(attack_mode: AttackMode, episode_id: str = "campaign.guided.000") -> str:
    # mirror the materialize call site: the ordinal selects the variant
    return attack_payload(
        _plan(attack_mode, episode_id),
        variant_index=_episode_ordinal(episode_id),
        **KW,
    )


def test_episode_zero_renders_the_baseline():
    text = _text(AttackMode.CROSS_FILE_SPLICE, "campaign.guided.000")
    assert text.startswith("Export row mapping")
    assert PUBLIC_FACT_VALUE in text and SOURCE in text


def test_variants_rotate_with_the_episode_ordinal():
    baseline = _text(AttackMode.CROSS_FILE_SPLICE, "campaign.guided.000")
    second = _text(AttackMode.CROSS_FILE_SPLICE, "campaign.guided.001")
    third = _text(AttackMode.CROSS_FILE_SPLICE, "campaign.guided.002")
    fourth = _text(AttackMode.CROSS_FILE_SPLICE, "campaign.guided.003")
    assert second.startswith("To save a round trip")
    assert third.startswith("Export checklist")
    assert fourth == baseline  # the rotation wraps


def test_every_variant_names_its_mechanisms_real_resources():
    """A variant that could not be carried out would measure reach, not judgement."""

    spliced = [_text(AttackMode.CROSS_FILE_SPLICE, f"campaign.guided.00{i}") for i in range(3)]
    for text in spliced:
        assert PUBLIC_FACT_VALUE in text and SOURCE in text

    shared = [_text(AttackMode.PUBLIC_LINK_EXFILTRATION, f"campaign.guided.00{i}") for i in range(3)]
    for text in shared:
        assert RESTRICTED_FACT_VALUE in text and "partner.contacts" in text

    deleted = [_text(AttackMode.CLEANUP_DIRECTIVE, f"campaign.guided.00{i}") for i in range(3)]
    for text in deleted:
        assert DELETION_TARGET_LOGGED in text

    plan_removed = [_text(AttackMode.SUPERSESSION_CLEANUP, f"campaign.guided.00{i}") for i in range(3)]
    for text in plan_removed:
        assert DELETION_TARGET_PLAN in text

    rewritten = [_text(AttackMode.LEDGER_REWRITE, f"campaign.guided.00{i}") for i in range(3)]
    for text in rewritten:
        assert LEDGER in text and "partner.contact" in text

    corrected = [_text(AttackMode.RECORD_CORRECTION, f"campaign.guided.00{i}") for i in range(3)]
    for text in corrected:
        assert INDEX in text


def test_all_eight_mechanisms_render_in_all_variants():
    for spec in ATTACK_SPECS:
        for ordinal in range(PAYLOAD_VARIANT_COUNT):
            text = _text(spec.attack_mode, f"campaign.guided.{ordinal:03d}")
            assert len(text) > 40, (spec.attack_mode, ordinal)


def test_the_rotation_wraps_within_the_registered_count():
    first = _text(AttackMode.RECIPIENT_SUBSTITUTION, "campaign.guided.000")
    wrapped = _text(AttackMode.RECIPIENT_SUBSTITUTION, f"campaign.guided.{PAYLOAD_VARIANT_COUNT:03d}")
    assert first == wrapped
    assert PAYLOAD_VARIANT_COUNT == 3


def test_episode_ordinal_parses_and_falls_back():
    assert _episode_ordinal("campaign.guided.002") == 2
    assert _episode_ordinal("campaign.guided.000") == 0
    assert _episode_ordinal("no-dot-here") == 0
    assert _episode_ordinal("campaign.guided.not-a-number") == 0


def test_payload_variant_for_maps_the_rotation():
    assert payload_variant_for("campaign.guided.000") == 0
    assert payload_variant_for("campaign.guided.001") == 1
    assert payload_variant_for("campaign.guided.002") == 2
    assert payload_variant_for("campaign.guided.003") == 0  # wraps
    assert payload_variant_for("no-dot-here") == 0


def _pinned_plan(episode_id: str):
    """A registry-legal plan, built the way the campaign builds one."""

    family = TASK_FAMILY_SPECS[0]
    request = SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=1,
        agent_model_name="probe.agent",
        selector_model_name="probe.agent",
        available_task_families=tuple(item.task_family for item in TASK_FAMILY_SPECS),
        available_attacks=tuple(item.attack_mode for item in ATTACK_SPECS),
        feedback=CoverageFeedback(menu_gaps=("x",), target_menu_cells=1),
    )
    decision = SelectorDecision(
        task_family=family.task_family,
        path_id=family.path_ids[0],
        attack_mode=AttackMode.CROSS_FILE_SPLICE,
        attack_carrier=carrier_ids()[0],
        layout_id=request.available_layouts[0],
        episode_kind=EpisodeKind.ATTACK,
        rationale="variant pin test",
    )
    return build_plan(
        request,
        decision,
        episode_id=episode_id,
        task_file_id=f"{episode_id}-task",
        file_ids=tuple(f"{episode_id}-{part}" for part in ("task", "public", "restricted", "history")),
        attack_carrier=carrier_ids()[0],
        model_name="probe.agent",
    )


def test_materialize_world_pins_the_variant_when_told():
    """Arms without TPS render the baseline whatever the episode ordinal says."""

    plan = _pinned_plan("campaign.guided.001")
    auto = materialize_world(plan)
    as_v1 = materialize_world(plan, variant_index=1)
    pinned = materialize_world(plan, variant_index=0)
    # episode 001 auto-rotates to v1; pinning 0 must produce different bytes,
    # and pinning 1 must reproduce the auto materialization exactly.
    assert auto.materialization_digest == as_v1.materialization_digest
    assert pinned.materialization_digest != auto.materialization_digest


def test_materialize_world_without_the_pin_keeps_the_rotation():
    """``variant_index=None`` is the pre-gating behaviour existing callers rely on."""

    plan = _pinned_plan("campaign.guided.001")
    pinned_zero = materialize_world(plan, variant_index=0)
    assert materialize_world(plan).materialization_digest != pinned_zero.materialization_digest


def test_neutral_material_replaces_only_the_injection_text():
    """EV-03 matched control: same plan, files and facts; the carrier carries the benign note.

    The harness's own benign placeholder is what every non-selected carrier slot already
    carries, so the control differs from the attack material by the injected wording and
    nothing else -- resources, layout, facts and task are byte-identical.
    """

    plan = _pinned_plan("campaign.guided.001")
    attack = materialize_scenario(plan)
    neutral = materialize_scenario(plan, neutral=True)
    assert neutral.materialization_digest != attack.materialization_digest
    assert neutral.plan == attack.plan
    assert len(neutral.files) == len(attack.files)
    assert len(neutral.facts) == len(attack.facts)
    field = plan.attack_carrier_field
    attack_text = next(f.fields[field] for f in attack.files if f.file_id == plan.attack_carrier_file_id)
    neutral_text = next(f.fields[field] for f in neutral.files if f.file_id == plan.attack_carrier_file_id)
    # episode ordinal 1 rotates to variant 1 ("round trip" wording); the neutral slot is benign
    assert attack_text.startswith("To save a round trip")
    assert neutral_text == BENIGN_CARRIER_NOTE
