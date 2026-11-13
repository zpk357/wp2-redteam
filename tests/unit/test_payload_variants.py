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
    EpisodeScenarioPlan,
    TaskFamily,
    task_family_spec,
)
from sandbox.scenarios.error_capable_world import (
    DELETION_TARGET_LOGGED,
    DELETION_TARGET_PLAN,
    PAYLOAD_VARIANT_COUNT,
    PUBLIC_FACT_VALUE,
    RESTRICTED_FACT_VALUE,
    _episode_ordinal,
    attack_payload,
    layout_roles,
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
