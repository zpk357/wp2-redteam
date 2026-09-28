"""ENC-01/02/03: the low-trust section is written into the file the task always reads.

rec-v2 measured the failure: eight observable mutations, none ever read, because every mutable slot
sat in a side file the task did not require.  The first fix moved the section onto the public
anchor, and the model opened that source in only one of three episodes -- so the encounter still
depended on a choice.  These tests pin the working shape: the section is appended to the internal
sheet, which every episode of every batch has read, and the public anchor stays immutable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_enc as ENC
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import (
    PUBLIC_ANCHOR_FILE,
    PUBLIC_FACTS,
)
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_e2 import (
    LIMITED_UNIT_VALUE,
    RESTRICTED_FILE,
)
from sandbox.structured_v1.fixture import verify_fixture_manifest
from sandbox.structured_v1.projection import build_public_projection, projection_leaks
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.world import (
    LOW_TRUST_SECTION_HEADER,
    FixtureWorldOverlay,
    SlotFieldKind,
    materialize_world,
)

LEGACY_FIXTURE_ID = "summary-delivery-e2-completion-v2"


@pytest.fixture()
def fixture():
    return load_fixture(ENC.FIXTURE_ID)


def _sheet_body(state) -> str:
    return next(
        item.content
        for item in state.domain_graph.drive.versions
        if item.file_id == RESTRICTED_FILE
    )


def test_the_fixture_is_registered_with_its_own_identity(fixture) -> None:
    legacy = load_fixture(LEGACY_FIXTURE_ID)

    assert fixture.manifest.fixture_id == ENC.FIXTURE_ID
    assert fixture.manifest.fixture_version == ENC.FIXTURE_VERSION
    assert verify_fixture_manifest(fixture.manifest)
    assert fixture.manifest.manifest_digest != legacy.manifest.manifest_digest
    assert fixture.manifest.public_task_text == legacy.manifest.public_task_text, (
        "the task text stays the baseline original, so a change is attributable to the section"
    )
    legacy_task = legacy.manifest.session_protocol.action_task_text
    assert fixture.manifest.session_protocol.action_task_text == legacy_task, (
        "the delivery task must keep its safety clauses verbatim"
    )


def test_the_section_slot_targets_the_always_read_sheet(fixture) -> None:
    targets = {target.slot_id: target for target in fixture.overlay.slot_targets}

    assert targets[ENC.SECTION_SLOT].field_kind is SlotFieldKind.DRIVE_FILE_SECTION
    assert targets[ENC.SECTION_SLOT].object_id == RESTRICTED_FILE
    assert len(fixture.overlay.slot_targets) == 4, "every original slot stays registered"
    for slot_id in ("s1", "s2", "s4"):
        assert targets[slot_id].object_id != RESTRICTED_FILE
    assert PUBLIC_ANCHOR_FILE not in {t.object_id for t in fixture.overlay.slot_targets}


def test_one_read_returns_the_fixed_sheet_and_the_mutable_section(fixture) -> None:
    # `case=None` renders the fixture's own (no-injection) material.
    material = render_material(None, fixture.manifest)
    section = next(
        "\n".join(slot.contents)
        for slot in material.slots
        if slot.slot_id == ENC.SECTION_SLOT
    )
    sheet = next(
        item for item in fixture.overlay.drive_files if item.file_id == RESTRICTED_FILE
    )

    result = materialize_world(
        fixture.manifest,
        material,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        episode_id="episode-enc-0001",
    )
    body = _sheet_body(result.initial_state)

    assert sheet.content.rstrip() in body, "the fixed sheet survives in the same field"
    assert LOW_TRUST_SECTION_HEADER in body, "the section is separated by the frozen marker"
    assert section.strip() in body, "the episode's own section text is inside the read result"
    assert LIMITED_UNIT_VALUE in body, "the read still carries the registered value to compare"


def test_the_fixed_sheet_cannot_be_replaced_whole(fixture) -> None:
    payload = fixture.overlay.model_dump(mode="json")
    payload["slot_targets"] = [
        target for target in payload["slot_targets"] if target["slot_id"] != ENC.SECTION_SLOT
    ] + [
        {
            "slot_id": ENC.SECTION_SLOT,
            "field_kind": SlotFieldKind.DRIVE_FILE_CONTENT.value,
            "object_id": RESTRICTED_FILE,
        }
    ]

    with pytest.raises(ValidationError):
        FixtureWorldOverlay.model_validate(payload)


def test_the_public_anchor_stays_immutable(fixture) -> None:
    anchor = next(
        item for item in fixture.overlay.drive_files if item.file_id == PUBLIC_ANCHOR_FILE
    )

    assert anchor.classification is not None
    for fact in PUBLIC_FACTS:
        assert fact in anchor.content
    assert LIMITED_UNIT_VALUE not in anchor.content


def test_the_projection_names_the_host_and_the_public_recipients(fixture) -> None:
    projection = build_public_projection(manifest=fixture.manifest)
    slots = {slot.slot_id: slot for slot in projection.slots}

    assert slots[ENC.SECTION_SLOT].host_role
    assert slots[ENC.SECTION_SLOT].host_purpose
    assert slots[ENC.SECTION_SLOT].host_name
    names = {recipient.name for recipient in projection.public_recipients}
    assert {"partner.contact", "internal.reviewer"} <= names
    assert all(recipient.channel for recipient in projection.public_recipients)
    assert projection_leaks(projection, manifest=fixture.manifest) == ()
    serialized = projection.model_dump_json()
    assert LIMITED_UNIT_VALUE not in serialized
    assert "task_authorized" not in serialized, (
        "whether a recipient is authorized is the Oracle's answer, not the generator's input"
    )


def test_a_fixture_that_does_not_use_the_new_fields_keeps_its_shape(fixture) -> None:
    legacy = load_fixture(LEGACY_FIXTURE_ID)
    payload = legacy.manifest.model_dump(mode="json")

    assert "slot_hosts" not in payload
    assert "public_recipients" not in payload
    projection = build_public_projection(manifest=legacy.manifest)
    assert "public_recipients" not in projection.model_dump(mode="json")
    assert all(
        "host_name" not in slot.model_dump(mode="json") for slot in projection.slots
    )


@pytest.mark.parametrize("variant", ["n1", "n2"])
def test_a_frozen_variant_materialises_its_own_note_into_the_read_source(variant) -> None:
    """ENC-04: a no-injection run must put the frozen note inside the read result."""

    variant_fixture = load_fixture(ENC.VARIANT_FIXTURE_IDS[variant])
    text = ENC.NEUTRAL_VARIANTS[variant]
    material = render_material(None, variant_fixture.manifest)
    section = next(
        "\n".join(slot.contents)
        for slot in material.slots
        if slot.slot_id == ENC.SECTION_SLOT
    )

    result = materialize_world(
        variant_fixture.manifest,
        material,
        base_world=variant_fixture.base_world,
        overlay=variant_fixture.overlay,
        episode_id=f"episode-{variant}",
    )
    body = _sheet_body(result.initial_state)

    assert text in section
    assert text in body, "the frozen note must come back in the same read as the sheet"
    assert LOW_TRUST_SECTION_HEADER in body
    assert LIMITED_UNIT_VALUE in body


def test_the_two_variants_differ_only_in_their_frozen_note() -> None:
    n1 = load_fixture(ENC.VARIANT_FIXTURE_IDS["n1"])
    n2 = load_fixture(ENC.VARIANT_FIXTURE_IDS["n2"])

    assert n1.manifest.manifest_digest != n2.manifest.manifest_digest
    assert n1.manifest.original_slots != n2.manifest.original_slots
    assert n1.manifest.public_task_text == n2.manifest.public_task_text
    assert [target.model_dump() for target in n1.overlay.slot_targets] == [
        target.model_dump() for target in n2.overlay.slot_targets
    ]
    assert n1.overlay.drive_files == n2.overlay.drive_files
