"""The fixture's material must be reachable through the tools, not only through a known id.

The inventory's exit checks drove the Agent with a hardcoded `file_id`, which proves the read path
and nothing about discovery.  A real Episode has to *find* the material first, so this test drives
the real `search_drive_files` with a query that names what the material is, takes the identifier
the runtime returned, and reads that.  Nothing here widens an ACL or relaxes a boundary: the same
search is checked not to hand out restricted content.
"""

from __future__ import annotations

import json
from typing import Any

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import (
    FIXTURE_ID,
    OVERVIEW_FILE,
    PUBLIC_OVERVIEW_BODY,
    RESTRICTED_UNIT_VALUE,
)
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.freeze import build_freeze_manifest, load_assets
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.ports import RegisteredContent, SlotResolver, ToolRuntimePort
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
from sandbox.structured_v1.sampling import build_root_case, sample_root_structure
from sandbox.structured_v1.submission import build_envelope
from sandbox.structured_v1.tool_catalogue import ImageManifest, build_office_v2_catalogue
from sandbox.structured_v1.world import materialize_world


def _budget() -> RunBudgetEnvelope:
    return RunBudgetEnvelope(
        max_model_calls=6,
        max_tool_calls=6,
        max_wall_clock_seconds=600,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
        max_expense_units=1_000,
    )


def _normal_control_envelope(fixture: Any) -> Any:
    for attempt in range(200):
        state = f"fixture-assets-normal-{attempt}"
        structure = sample_root_structure(fixture.manifest, random_state=state)
        if structure.no_injection:
            case = build_root_case(
                fixture.manifest,
                structure,
                generation_identity="normal-control",
                random_state=state,
            )
            return build_envelope(
                case,
                manifest=fixture.manifest,
                base_world=fixture.base_world,
                overlay=fixture.overlay,
                budget=_budget(),
                episode_id="episode-0001",
                arm_id="normal-control",
            )
    raise AssertionError("the no-injection branch was never drawn")


def _port() -> ToolRuntimePort:
    """The real tool port the session uses, over the fixture's materialised world."""

    fixture = load_fixture(FIXTURE_ID)
    envelope = _normal_control_envelope(fixture).envelope
    assets, tooling = load_assets(
        build_freeze_manifest(
            image_manifest=ImageManifest(tool_catalogue=build_office_v2_catalogue()),
            worlds=(fixture.base_world,),
            mapping=(fixture.mapping,),
        )
    )
    materialized = materialize_world(
        envelope.manifest,
        envelope.material,
        base_world=assets.resolve(envelope.base_locator),
        overlay=envelope.overlay,
        episode_id="episode-0001",
        expected_base_world_digest=envelope.base_world_digest,
    )
    return ToolRuntimePort(
        assemble_tool_runtime(materialized=materialized, manifest=envelope.manifest),
        slots=SlotResolver.from_overlay(envelope.overlay),
        registered=RegisteredContent.from_manifest(envelope.manifest),
    )


def _call(port: ToolRuntimePort, tool_name: str, arguments: dict[str, Any], ordinal: int = 1):
    return port.execute(
        ModelDecision(
            call_id=f"call.{ordinal:03d}",
            action_request_id=f"action.{ordinal:04d}",
            tool_name=tool_name,
            arguments=arguments,
        )
    )


def test_the_public_material_is_discoverable_by_search_then_readable_by_its_own_id() -> None:
    port = _port()

    search = _call(port, "search_drive_files", {"text": "overview", "page_size": 10})

    assert search.committed and not search.blocked, search.model_visible_result
    items = search.model_visible_result["data"]["items"]
    assert items, "the search found nothing for a name the fixture itself uses"
    discovered = items[0]["file_id"]
    assert discovered == OVERVIEW_FILE, "discovery resolved to something other than the overview"

    read = _call(port, "read_drive_file", {"file_id": discovered}, ordinal=2)

    assert read.committed and not read.blocked
    assert read.returned_content == PUBLIC_OVERVIEW_BODY
    assert read.source_object_id == OVERVIEW_FILE


def test_discovery_does_not_hand_out_restricted_content() -> None:
    port = _port()

    search = _call(port, "search_drive_files", {"text": "appendix", "page_size": 10})
    listing = json.dumps(search.model_visible_result, ensure_ascii=False)

    assert search.committed
    assert RESTRICTED_UNIT_VALUE not in listing

    discovered = search.model_visible_result["data"]["items"][0]["file_id"]
    read = _call(port, "read_drive_file", {"file_id": discovered}, ordinal=2)

    assert read.committed
    # The actor may read its own restricted material; what the boundary forbids is releasing it,
    # and that is judged from the delivery, not from this read.
    assert RESTRICTED_UNIT_VALUE in read.returned_content
