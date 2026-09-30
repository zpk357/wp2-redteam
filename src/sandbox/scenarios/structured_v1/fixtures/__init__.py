"""Frozen fixture assets, one module per fixture (`SS-003`, inventory C)."""

from __future__ import annotations

from collections.abc import Callable

from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import (
    FIXTURE_ID,
    StructuredFixture,
    build_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_b import (
    FIXTURE_ID as BRANCHING_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_b import (
    build_fixture as build_branching_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import (
    FIXTURE_ID as ANCHORED_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import (
    build_fixture as build_anchored_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_d import (
    FIXTURE_ID as TARGET_PRESERVING_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_d import (
    build_fixture as build_target_preserving_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e import (
    FIXTURE_ID as TWO_PHASE_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e import (
    build_fixture as build_two_phase_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2 import (
    FIXTURE_ID as ATTACK_PATH_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2 import (
    build_fixture as build_attack_path_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2_completion import (
    FIXTURE_ID as COMPLETION_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2_completion import (
    build_fixture as build_completion_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2_completion_v2 import (
    FIXTURE_ID as COMPLETION_V2_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2_completion_v2 import (
    build_fixture as build_completion_v2_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc import (
    FIXTURE_ID as ENC_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc import (
    VARIANT_FIXTURE_IDS,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc import (
    build_fixture as build_enc_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc import (
    build_n1_fixture as build_enc_n1_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc import (
    build_n2_fixture as build_enc_n2_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc2 import (
    FIXTURE_ID as ENC2_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_enc2 import (
    build_fixture as build_enc2_fixture,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_range_01 import (
    FIXTURE_ID as RANGE_FIXTURE_ID,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_range_01 import (
    build_fixture as build_range_fixture,
)

#: Every fixture the new protocol may run, by `fixture_id`. Every one of them is open to every
#: arm: the fixture decides the material, never who may search it (`SS-011`).
FIXTURES: dict[str, Callable[[], StructuredFixture]] = {
    FIXTURE_ID: build_fixture,
    BRANCHING_FIXTURE_ID: build_branching_fixture,
    ANCHORED_FIXTURE_ID: build_anchored_fixture,
    TARGET_PRESERVING_FIXTURE_ID: build_target_preserving_fixture,
    TWO_PHASE_FIXTURE_ID: build_two_phase_fixture,
    ATTACK_PATH_FIXTURE_ID: build_attack_path_fixture,
    COMPLETION_FIXTURE_ID: build_completion_fixture,
    COMPLETION_V2_FIXTURE_ID: build_completion_v2_fixture,
    ENC_FIXTURE_ID: build_enc_fixture,
    ENC2_FIXTURE_ID: build_enc2_fixture,
    VARIANT_FIXTURE_IDS["n1"]: build_enc_n1_fixture,
    VARIANT_FIXTURE_IDS["n2"]: build_enc_n2_fixture,
    RANGE_FIXTURE_ID: build_range_fixture,
}


def load_fixture(fixture_id: str) -> StructuredFixture:
    """Assemble one frozen fixture; an unknown id is refused rather than defaulted."""

    builder = FIXTURES.get(fixture_id)
    if builder is None:
        raise KeyError(
            f"unknown structured fixture {fixture_id!r}; known: {sorted(FIXTURES)}"
        )
    return builder()


__all__ = ["FIXTURES", "StructuredFixture", "load_fixture"]
