"""The ENC encounter, run under the revised action phase (`ZIE-03`/`ZIE-11`).

The encounter itself -- which slot is appended to the sheet the task always reads, where the index
lands, which recipients are nameable -- is taken verbatim from `summary-delivery-enc-01`, so the
only difference this fixture introduces is the action phase: its task text (`ZIE-03`) and the tools
it offers (`ZIE-11`).  Keeping the world identical is what makes a reading change attributable.
"""

from __future__ import annotations

from sandbox.scenarios.structured_v1.fixtures import summary_delivery_e2_completion_v3 as base
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_enc as enc
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import StructuredFixture
from sandbox.structured_v1.assets import FixtureBaseMapping, FrozenWorldAsset, overlay_digest
from sandbox.structured_v1.fixture import build_fixture_manifest
from sandbox.structured_v1.phases import TwoPhaseProtocol
from sandbox.structured_v1.world import SlotFieldKind, SlotWorldTarget

FIXTURE_ID = "summary-delivery-enc-02"
FIXTURE_VERSION = enc.FIXTURE_VERSION

INVESTIGATION_TASK = enc.INVESTIGATION_TASK
ACTION_TASK = base.ACTION_TASK
COMPLETION_CONTRACT_VERSION = base.COMPLETION_CONTRACT_VERSION


def build_fixture() -> StructuredFixture:
    """The same encounter as `enc-01`, with the action phase revised."""

    source = base.build_fixture()
    fields = {
        key: value
        for key, value in source.manifest.model_dump().items()
        if key not in {
            "manifest_digest", "fixture_id", "fixture_version", "public_task_text",
            "session_protocol", "slot_hosts", "public_recipients",
        }
    }
    manifest = build_fixture_manifest(
        **fields,
        fixture_id=FIXTURE_ID,
        fixture_version=FIXTURE_VERSION,
        public_task_text=INVESTIGATION_TASK,
        session_protocol=TwoPhaseProtocol(
            version="investigate-act-v2",
            action_task_text=ACTION_TASK,
            action_tools=base.ACTION_TOOLS,
        ),
        slot_hosts=enc.SLOT_HOSTS,
        public_recipients=enc.PUBLIC_RECIPIENTS,
    )
    slot_targets = tuple(
        SlotWorldTarget(
            slot_id=target.slot_id,
            field_kind=SlotFieldKind.DRIVE_FILE_SECTION,
            object_id=enc.RESTRICTED_FILE,
        )
        if target.slot_id == enc.SECTION_SLOT
        else target
        for target in source.overlay.slot_targets
    )
    drive_files = tuple(
        item.model_copy(update={"content": enc.INDEX_BODY})
        if item.file_id == enc.INDEX_FILE
        else item
        for item in source.overlay.drive_files
    )
    overlay = source.overlay.model_copy(
        update={"drive_files": drive_files, "slot_targets": slot_targets}
    )
    mapping = FixtureBaseMapping(
        fixture_id=manifest.fixture_id,
        fixture_version=manifest.fixture_version,
        manifest_digest=manifest.manifest_digest,
        base_locator=FrozenWorldAsset(world=source.base_world).locator,
        base_world_digest=source.base_world.world_digest,
        overlay_digest=overlay_digest(overlay),
    )
    fixture = StructuredFixture(
        manifest=manifest, base_world=source.base_world, overlay=overlay, mapping=mapping,
    )
    enc._require_invariants(fixture)
    return fixture
