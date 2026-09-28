"""COMP-A fixture identity, prompt-boundary and asset-isolation checks."""

from __future__ import annotations

import test_structured_fixture_branching as helpers

from sandbox.scenarios.structured_v1.fixtures import FIXTURES, load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_e2 as E2
from sandbox.scenarios.structured_v1.fixtures import (
    summary_delivery_e2_completion as COMPLETION,
)
from sandbox.structured_v1.fixture import verify_fixture_manifest
from sandbox.structured_v1.projection import build_public_projection, verify_public_projection


def test_completion_fixture_is_registered_with_a_distinct_manifest_identity() -> None:
    old = load_fixture(E2.FIXTURE_ID)
    completion = load_fixture(COMPLETION.FIXTURE_ID)

    assert COMPLETION.FIXTURE_ID in FIXTURES
    assert completion.manifest.fixture_id == COMPLETION.FIXTURE_ID
    assert completion.manifest.fixture_version == COMPLETION.FIXTURE_VERSION
    assert verify_fixture_manifest(completion.manifest)
    assert completion.manifest.manifest_digest != old.manifest.manifest_digest
    assert completion.mapping.manifest_digest == completion.manifest.manifest_digest
    assert completion.mapping.manifest_digest != old.mapping.manifest_digest


def test_completion_reuses_e2_world_and_overlay_without_sharing_objects() -> None:
    old = load_fixture(E2.FIXTURE_ID)
    completion = load_fixture(COMPLETION.FIXTURE_ID)

    assert completion.base_world.model_dump(mode="json") == old.base_world.model_dump(mode="json")
    assert completion.overlay.model_dump(mode="json") == old.overlay.model_dump(mode="json")
    assert completion.mapping.base_world_digest == old.mapping.base_world_digest
    assert completion.mapping.overlay_digest == old.mapping.overlay_digest
    assert completion.base_world is not old.base_world
    assert completion.overlay is not old.overlay
    assert completion.manifest.registered_files == old.manifest.registered_files
    assert completion.manifest.registered_units == old.manifest.registered_units
    assert completion.manifest.hidden_labels == old.manifest.hidden_labels


def test_completion_changes_only_the_trusted_prompt_contract_and_identity() -> None:
    old = load_fixture(E2.FIXTURE_ID)
    completion = load_fixture(COMPLETION.FIXTURE_ID)
    excluded = {
        "fixture_id",
        "fixture_version",
        "manifest_digest",
        "public_task_text",
        "session_protocol",
    }
    old_fields = old.manifest.model_dump(mode="json")
    completion_fields = completion.manifest.model_dump(mode="json")

    assert {
        key: value for key, value in completion_fields.items() if key not in excluded
    } == {key: value for key, value in old_fields.items() if key not in excluded}
    assert completion.manifest.public_task_text == COMPLETION.INVESTIGATION_TASK
    assert completion.manifest.session_protocol is not None
    assert completion.manifest.session_protocol.action_task_text == COMPLETION.ACTION_TASK
    assert completion.manifest.session_protocol.action_task_text != (
        old.manifest.session_protocol.action_task_text
    )


def test_completion_prompt_does_not_contain_limited_values_or_hidden_labels() -> None:
    completion = load_fixture(COMPLETION.FIXTURE_ID)
    protocol = completion.manifest.session_protocol
    assert protocol is not None
    prompt_texts = (
        completion.manifest.public_task_text,
        protocol.action_task_text,
        COMPLETION.INVESTIGATION_TASK,
        COMPLETION.ACTION_TASK,
    )

    for text in prompt_texts:
        assert all(value not in text for value in completion.manifest.private_values)
        assert all(label not in text for label in completion.manifest.hidden_labels)
    assert E2.LIMITED_UNIT_VALUE not in "\n".join(prompt_texts)


def test_completion_public_projection_remains_redacted() -> None:
    completion = load_fixture(COMPLETION.FIXTURE_ID)
    parent = helpers._normal_control_case(completion)

    projection = build_public_projection(manifest=completion.manifest, parent=parent)
    verify_public_projection(projection, manifest=completion.manifest)
    serialized = projection.model_dump_json()

    assert all(value not in serialized for value in completion.manifest.private_values)
    assert all(label not in serialized for label in completion.manifest.hidden_labels)


def test_building_completion_does_not_mutate_the_original_e2_identity_or_assets() -> None:
    before = load_fixture(E2.FIXTURE_ID)
    before_manifest = before.manifest.model_dump_json()
    before_overlay = before.overlay.model_dump_json()
    before_mapping = before.mapping.model_dump_json()

    _ = load_fixture(COMPLETION.FIXTURE_ID)
    after = load_fixture(E2.FIXTURE_ID)

    assert after.manifest.model_dump_json() == before_manifest
    assert after.overlay.model_dump_json() == before_overlay
    assert after.mapping.model_dump_json() == before_mapping

