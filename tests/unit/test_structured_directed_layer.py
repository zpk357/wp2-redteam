"""`DIR`: the directed entrance is additive -- it opens on proximity and never replaces explore."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from structured_coverage_helpers import record_coverage  # noqa: E402

from sandbox.structured_v1.coverage import (  # noqa: E402
    BehaviorAtom,
    CoverageResult,
    DataAudienceRelation,
    RecipientRelation,
    RiskAtom,
    RiskEventKind,
    unit_key_string,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs  # noqa: E402
from sandbox.structured_v1.search import (  # noqa: E402
    FDM_POSITION_VERSION,
    PROXIMITY_HIT,
    PROXIMITY_NEAR,
    PROXIMITY_NONE,
    ArmKind,
    SelectionLayer,
    TwoArmSearch,
    unit_proximity,
)
from sandbox.structured_v1.streams import build_random  # noqa: E402

DATA_RELEASE = "data-release"
AUDIENCE_SCOPE = "audience-scope"


def _risk_key(
    direction: str,
    *,
    event: RiskEventKind,
    recipient: str = RecipientRelation.TASK_UNAUTHORIZED.value,
    audience: str = DataAudienceRelation.ALLOWED.value,
) -> tuple[str, ...]:
    return (direction, "restricted", recipient, audience, event.value, "platform")


def _search(manifest, *, seed: str) -> TwoArmSearch:
    return TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed=seed,
    )


def _at_local_opportunity(search: TwoArmSearch, seed: str, direction: str) -> int:
    """Place the arm on a non-root local opportunity in one obligation direction."""

    index = next(
        candidate
        for candidate in range(1, 80)
        if candidate % 5 not in search._root_slots(f"{seed}:{direction}", candidate // 5)
    )
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0 if direction == DATA_RELEASE else 1,
        "direction_opportunities": {direction: index},
    })
    return index


def _layer_draw(seed: str, direction: str, index: int) -> int:
    random_state = f"{seed}:{ArmKind.COVERAGE_GUIDED.value}:{direction}:{index}"
    return build_random(f"{random_state}:layer:{FDM_POSITION_VERSION}").randrange(2)


def _seed_parent(search: TwoArmSearch, direction: str, *, near: bool = True):
    """Give one parent a witnessed risk unit, so the directed scan has something to find."""

    parent = sorted(search.parents)[0]
    atoms = [BehaviorAtom(key=("seed",))]
    if near:
        atoms.append(RiskAtom(
            key=_risk_key(direction, event=RiskEventKind.ATTEMPTED),
            event_kind=RiskEventKind.ATTEMPTED,
        ))
    record_coverage(
        search,
        CoverageResult(
            episode_id="episode-seed",
            fixture_id=search.manifest.fixture_id,
            behavior=(atoms[0],),
            risk=tuple(atom for atom in atoms[1:]),
        ),
        parent_id=parent,
    )
    return parent


def _seed_behavior_only(search: TwoArmSearch, parents: int = 3):
    """A ledger with no risk or joint unit at all: the directed entrance cannot open."""

    for parent in sorted(search.parents)[:parents]:
        record_coverage(
            search,
            CoverageResult(
                episode_id=f"episode-{parent}",
                fixture_id=search.manifest.fixture_id,
                behavior=(BehaviorAtom(key=("behavior", parent)),),
            ),
            parent_id=parent,
        )


def test_a_stale_position_version_refuses_to_resume(manifest):
    """`DIR-03`: the quota travels inside the protocol identity, so a v2 state cannot resume."""

    search = _search(manifest, seed="directed-identity")
    stale = search.state.protocol_identity.model_copy(
        update={"probability_version": "structured-fdm-position-v2"}
    )
    search.state = search.state.model_copy(update={"protocol_identity": stale})
    with pytest.raises(ValueError, match="protocol identity mismatch"):
        search.select(ArmKind.COVERAGE_GUIDED)


def test_the_layer_is_drawn_from_the_frozen_stream(manifest):
    """`DIR-03`: the entrance is reproducible from the receipt's own random state alone.

    This fails if the layer is drawn from any other stream or with any other expression, and it is
    what makes the explore-golden comparison reproducible: the receipt's stream is untouched by the
    layer draw, so a v2 and a v3 run agree wherever the directed entrance does not open.
    """

    for number in range(12):
        seed = f"directed-stream-{number}"
        search = _search(manifest, seed=seed)
        _at_local_opportunity(search, seed, DATA_RELEASE)
        _seed_behavior_only(search)

        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        drawn_directed = build_random(
            f"{receipt.random_state}:layer:{FDM_POSITION_VERSION}"
        ).randrange(2) == 0
        if drawn_directed:
            assert receipt.selection_layer in (SelectionLayer.DIRECTED, SelectionLayer.FALLBACK)
        else:
            assert receipt.selection_layer is SelectionLayer.EXPLORE


def test_unit_proximity_reads_the_tier_out_of_the_key_alone():
    near = _risk_key(DATA_RELEASE, event=RiskEventKind.ATTEMPTED)
    hit = _risk_key(DATA_RELEASE, event=RiskEventKind.COMMITTED)
    hit_blocked = _risk_key(DATA_RELEASE, event=RiskEventKind.BLOCKED)
    in_scope = _risk_key(
        DATA_RELEASE,
        event=RiskEventKind.COMMITTED,
        recipient=RecipientRelation.TASK_AUTHORIZED.value,
    )

    assert unit_proximity(unit_key_string(near), "risk") == PROXIMITY_NEAR
    assert unit_proximity(unit_key_string(hit), "risk") == PROXIMITY_HIT
    assert unit_proximity(unit_key_string(hit_blocked), "risk") == PROXIMITY_NEAR
    assert unit_proximity(unit_key_string(in_scope), "risk") == PROXIMITY_NONE
    # A behavior unit is cross-cutting and can never be near: it carries no audience at all.
    assert unit_proximity(unit_key_string(("behavior", "deliver")), "behavior") == PROXIMITY_NONE
    # A joint key nests its risk key at position 0 and must read the same tier.
    assert unit_proximity(unit_key_string((hit, ("behavior",), "same_transition")), "joint") == (
        PROXIMITY_HIT
    )
    # A key that does not carry the approved positions is not guessed into a tier.
    assert unit_proximity(unit_key_string(("too", "short")), "risk") == PROXIMITY_NONE


def test_directed_entrance_selects_the_near_unit_in_the_scheduled_direction(manifest):
    for number in range(40):
        seed = f"directed-hit-{number}"
        probe = _search(manifest, seed=seed)
        index = _at_local_opportunity(probe, seed, DATA_RELEASE)
        if _layer_draw(seed, DATA_RELEASE, index) != 0:
            continue

        search = _search(manifest, seed=seed)
        _at_local_opportunity(search, seed, DATA_RELEASE)
        _seed_parent(search, DATA_RELEASE)

        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        assert receipt.selection_layer is SelectionLayer.DIRECTED
        assert receipt.reason == "directed-near-violation"
        assert receipt.selected_unit is not None
        assert receipt.selected_dimension == "risk"
        assert unit_proximity(receipt.selected_unit, "risk") == PROXIMITY_NEAR
        return
    raise AssertionError("no seed exercised the directed entrance")


def test_directed_entrance_stays_closed_for_the_other_direction(manifest):
    """`DIR-07`: a near unit of the wrong obligation must never open the entrance."""

    saw_fallback = False
    for number in range(40):
        seed = f"directed-miss-{number}"
        probe = _search(manifest, seed=seed)
        index = _at_local_opportunity(probe, seed, DATA_RELEASE)
        draws_directed = _layer_draw(seed, DATA_RELEASE, index) == 0

        search = _search(manifest, seed=seed)
        _at_local_opportunity(search, seed, DATA_RELEASE)
        _seed_parent(search, AUDIENCE_SCOPE)  # near, but for the other obligation

        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        assert receipt.selection_layer is not SelectionLayer.DIRECTED
        if draws_directed:
            assert receipt.selection_layer is SelectionLayer.FALLBACK
            saw_fallback = True
    assert saw_fallback, "the fallback path was never reached"


def test_explore_rule_still_governs_when_nothing_is_near(manifest):
    """`DIR-01`/`DIR-04`: with no near unit the explore ranking decides, whatever was drawn."""

    saw_directed_draw = False
    for number in range(40):
        seed = f"directed-explore-{number}"
        probe = _search(manifest, seed=seed)
        index = _at_local_opportunity(probe, seed, DATA_RELEASE)
        saw_directed_draw |= _layer_draw(seed, DATA_RELEASE, index) == 0

        search = _search(manifest, seed=seed)
        _at_local_opportunity(search, seed, DATA_RELEASE)
        _seed_behavior_only(search)
        assert search._directed_candidates(DATA_RELEASE) == {}

        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        assert receipt.selection_layer is not SelectionLayer.DIRECTED
        assert receipt.selected_unit is not None
        # The chosen unit has to be one the explore ranking would tie for: fewest allocated
        # opportunities first, then fewest appearances.
        chosen = (
            search.state.unit_opportunities.get(receipt.selected_unit, 0),
            search.state.unit_occurrence.get(receipt.selected_unit, 0),
        )
        assert chosen == (0, 1)
        assert receipt.selected_dimension == "behavior"
    assert saw_directed_draw, "the directed draw never happened, so the fallback was not exercised"
