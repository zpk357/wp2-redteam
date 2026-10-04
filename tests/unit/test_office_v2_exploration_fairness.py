"""Fairness tests for the independent baseline and the guided arm.

The design requires the two arms to differ only in whether they consume results of
previous Episodes.
"""

from __future__ import annotations

import inspect

from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_loop import choose_next_allocation
from sandbox.fuzzer.v2_campaign_state import build_campaign_state
from sandbox.fuzzer.v2_corpus import (
    ExecutableSeedSupport,
    PayloadSpec,
    seal_contract,
)
from sandbox.fuzzer.v2_real_runtime import build_candidate_dedup_filters
from sandbox.fuzzer.v2_seed_pools import (
    RiskProgressLevel,
    RiskType,
    RiskTypeProgressState,
)
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.mutation.v2_candidate import (
    normalized_content_digest,
    structure_signature_digest,
)
from sandbox.replay.digests import sha256_digest

CAMPAIGN_ID = "campaign.fairness"
SEEDS = range(64)


def state_with_progress(
    *levels: tuple[RiskType, RiskProgressLevel],
):
    initial = build_exploratory_bootstrap(episode_limit=40).initial_state
    return build_campaign_state(
        coverage=initial.coverage,
        corpus=initial.corpus,
        seed_catalog=initial.seed_catalog,
        risk_progress=tuple(
            RiskTypeProgressState(risk_type=risk_type, level=level)
            for risk_type, level in levels
        ),
        new_seed_priority_queue=initial.new_seed_priority_queue,
        budget=initial.budget,
        lifecycle=initial.lifecycle,
    )


def skewed_levels() -> tuple[tuple[RiskType, RiskProgressLevel], ...]:
    return tuple(
        (
            risk_type,
            RiskProgressLevel.REALIZED
            if risk_type is RiskType.WRONG_MODIFICATION_PROPAGATION
            else RiskProgressLevel.NOT_SELECTED,
        )
        for risk_type in RiskType
    )


def selected_types(state, strategy: CampaignStrategy) -> set[RiskType]:
    return {
        choose_next_allocation(
            campaign_id=CAMPAIGN_ID,
            state=state,
            strategy=strategy,
            campaign_seed_value=seed,
        ).risk_type
        for seed in SEEDS
    }


def test_random_direction_is_independent_of_recorded_risk_progress() -> None:
    flat = state_with_progress()
    skewed = state_with_progress(*skewed_levels())

    allocations = {
        state_id: {
            choose_next_allocation(
                campaign_id=CAMPAIGN_ID,
                state=state,
                strategy=CampaignStrategy.RANDOM_INDEPENDENT,
                campaign_seed_value=seed,
            ).allocation_digest
            for seed in SEEDS
        }
        for state_id, state in (("flat", flat), ("skewed", skewed))
    }

    assert allocations["flat"] == allocations["skewed"]


def test_guided_direction_still_prefers_the_lowest_progress_type() -> None:
    selected = selected_types(
        state_with_progress(*skewed_levels()), CampaignStrategy.COVERAGE_GUIDED
    )

    assert RiskType.WRONG_MODIFICATION_PROPAGATION not in selected
    assert selected == set(RiskType) - {RiskType.WRONG_MODIFICATION_PROPAGATION}


def test_random_direction_treats_every_pool_as_eligible() -> None:
    selected = selected_types(
        state_with_progress(*skewed_levels()), CampaignStrategy.RANDOM_INDEPENDENT
    )

    assert RiskType.WRONG_MODIFICATION_PROPAGATION in selected
    assert selected == set(RiskType)


def test_independent_allocation_encodes_no_cross_episode_progress() -> None:
    allocation = choose_next_allocation(
        campaign_id=CAMPAIGN_ID,
        state=state_with_progress(*skewed_levels()),
        strategy=CampaignStrategy.RANDOM_INDEPENDENT,
        campaign_seed_value=3,
    )

    assert allocation.reason_codes == ("independent-random-baseline",)
    assert allocation.risk_type_progress_level == int(RiskProgressLevel.NOT_SELECTED)
    assert allocation.score_components == (("risk-type-progress-level", 0),)


def test_guided_allocation_still_records_the_progress_reason() -> None:
    allocation = choose_next_allocation(
        campaign_id=CAMPAIGN_ID,
        state=state_with_progress(*skewed_levels()),
        strategy=CampaignStrategy.COVERAGE_GUIDED,
        campaign_seed_value=3,
    )

    assert allocation.reason_codes == (
        "formal-risk-pool-selection",
        "lowest-risk-type-progress-level",
    )
    assert allocation.risk_type_progress_level == int(RiskProgressLevel.NOT_SELECTED)


def test_candidate_dedup_filters_take_no_strategy_input() -> None:
    parameters = inspect.signature(build_candidate_dedup_filters).parameters

    assert "strategy" not in parameters
    assert set(parameters) == {"corpus_supports", "lineage_supports", "candidate_history"}


def support(seed_id: str, content: str) -> ExecutableSeedSupport:
    payload = PayloadSpec(
        payload_spec_id="payload-1",
        content=content,
        carrier_kind="email",
        field_path="body",
        content_digest=sha256_digest({"content": content}),
    )
    return seal_contract(
        ExecutableSeedSupport,
        {
            "seed_id": seed_id,
            "payload_specs": (payload,),
            "root_seed_id": seed_id,
            "generation_depth": 0,
        },
        "seed_content_digest",
    )


def test_candidate_dedup_filters_use_only_this_arms_content() -> None:
    content_digest = sha256_digest({"content": "history"})
    structure_digest = sha256_digest({"structure": "history"})

    filters = build_candidate_dedup_filters(
        corpus_supports=(support("seed.parent", "Parent text."),),
        lineage_supports=(support("seed.child", "Child text."),),
        candidate_history=((content_digest, structure_digest),),
    )

    assert (
        normalized_content_digest((("payload-1", "Parent text."),))
        in filters.known_content_digests
    )
    assert content_digest in filters.known_content_digests
    assert normalized_content_digest((("payload-1", "Child text."),)) in (
        filters.recent_lineage_content_digests
    )
    assert structure_signature_digest((("payload-1", "Child text."),)) in (
        filters.recent_structure_signature_digests
    )
    assert structure_digest in filters.recent_structure_signature_digests


def test_candidate_dedup_filters_are_empty_for_a_fresh_arm() -> None:
    filters = build_candidate_dedup_filters(
        corpus_supports=(), lineage_supports=(), candidate_history=()
    )

    assert filters.known_content_digests == frozenset()
    assert filters.recent_lineage_content_digests == frozenset()
    assert filters.recent_structure_signature_digests == frozenset()
