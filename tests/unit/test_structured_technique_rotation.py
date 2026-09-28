"""The attack-technique rotation is part of the shared schedule (`SS-010`).

Both arms must walk the same angle sequence, and one arm's 16 opportunities must probe every
(direction, technique) pair exactly once - otherwise the run scans a smaller space than the run
package claims, and the two arms stop being comparable.
"""

from __future__ import annotations

from sandbox.structured_v1.provider import ATTACK_TECHNIQUE_IDS
from sandbox.structured_v1.search import attack_technique_for

OPPORTUNITIES = 16
DIRECTIONS = ("data-release", "audience-scope")


def test_sixteen_opportunities_cover_each_technique_twice() -> None:
    techniques = [attack_technique_for(index) for index in range(OPPORTUNITIES)]

    assert sorted(techniques) == sorted(ATTACK_TECHNIQUE_IDS * 2)


def test_direction_and_technique_are_orthogonal_over_one_arm() -> None:
    combinations = {
        (DIRECTIONS[index % len(DIRECTIONS)], attack_technique_for(index))
        for index in range(OPPORTUNITIES)
    }

    assert len(combinations) == len(DIRECTIONS) * len(ATTACK_TECHNIQUE_IDS)


def test_the_angle_depends_only_on_the_opportunity() -> None:
    """No arm, parent or feedback enters it, so the two arms' sequences match by construction."""

    assert attack_technique_for(0) == ATTACK_TECHNIQUE_IDS[0]
    assert attack_technique_for(3) == ATTACK_TECHNIQUE_IDS[1]
    # The obligation direction keeps its own `opportunity % 2` schedule and is unaffected.
    assert attack_technique_for(2) == attack_technique_for(3) == ATTACK_TECHNIQUE_IDS[1]
    # It wraps after the eight angles.
    assert attack_technique_for(OPPORTUNITIES) == ATTACK_TECHNIQUE_IDS[0]
