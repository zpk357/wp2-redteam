"""Target-preserving fixture and formally bound fixed restricted reads."""

from __future__ import annotations

import pytest
import test_structured_fixture_branching as helpers

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_c as old_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_d as fixture_d
from sandbox.structured_v1.acceptance import judge_normal_control
from sandbox.structured_v1.bundle import verify_bundle
from sandbox.structured_v1.coverage import JointRelation, RiskEventKind, extract_coverage
from sandbox.structured_v1.envelope import verify_envelope
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.models import SlotPatch
from sandbox.structured_v1.ports import RegisteredContent, SlotResolver, ToolRuntimePort
from sandbox.structured_v1.projection import build_public_projection, verify_public_projection
from sandbox.structured_v1.provider import (
    ProviderFailureClass,
    ProviderTextItem,
    ProviderTextResponse,
    prepare_texts,
)
from sandbox.structured_v1.redaction import registered_file_digest
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
from sandbox.structured_v1.search import ArmKind, TwoArmSearch
from sandbox.structured_v1.submission import build_envelope
from sandbox.structured_v1.validation import admit_case


def _decisions(*actions):
    return [
        ModelDecision(
            call_id=f"model.{index}", action_request_id=f"action.{index}",
            tool_name=tool_name, arguments=arguments,
        )
        for index, (tool_name, arguments) in enumerate(actions, 1)
    ] + [ModelDecision(call_id="model.end")]


def test_new_fixture_is_isolated_and_fixed_assets_survive_root_and_empty_material():
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    old = load_fixture(old_fixture.FIXTURE_ID)
    assert old.manifest.fixture_version == "1.0.0"
    assert fixture.manifest.fixture_id != old.manifest.fixture_id
    assert fixture.mapping.overlay_digest != old.mapping.overlay_digest
    assert fixture.manifest.manifest_digest != old.manifest.manifest_digest
    assert {target.object_id for target in fixture.overlay.slot_targets}.isdisjoint(
        {fixture_d.PUBLIC_ANCHOR_FILE, fixture_d.base.BRIEF_FILE}
    )
    fixed = {item.file_id: item for item in fixture.overlay.drive_files
             if item.file_id in {fixture_d.PUBLIC_ANCHOR_FILE, fixture_d.base.BRIEF_FILE}}
    assert registered_file_digest(fixed[fixture_d.base.BRIEF_FILE].content) == (
        fixture.manifest.registered_files[0].content_digest
    )
    assert fixture.overlay.fixed_registered_reads == (fixture_d.base.BRIEF_FILE,)
    assert fixed[fixture_d.base.BRIEF_FILE].content == fixture_d.FIXED_RESTRICTED_BODY
    for candidate in (helpers._normal_control_case(fixture),
                      helpers._case_with_rewritten_brief(fixture)):
        projection = build_public_projection(manifest=fixture.manifest, parent=candidate)
        verify_public_projection(projection, manifest=fixture.manifest)
        text = projection.model_dump_json()
        assert fixture_d.FIXED_RESTRICTED_BODY not in text
        assert all(unit.value not in text for unit in fixture.manifest.registered_units)
        assert all(label not in text for label in fixture.manifest.hidden_labels)
        assert admit_case(candidate, manifest=fixture.manifest).accepted
        assert {item.file_id: item for item in fixture.overlay.drive_files
                if item.file_id in fixed} == fixed
    empty = helpers._normal_control_case(fixture).model_copy(update={
        "nodes": (), "reference_graph": (),
        "slot_patches": (SlotPatch(slot_id="s3"),),
    })
    empty = empty.model_copy(update={
        "input_digest": render_material(empty, fixture.manifest).material_digest,
    })
    assert admit_case(empty, manifest=fixture.manifest).accepted
    assert not next(slot for slot in render_material(empty, fixture.manifest).slots
                    if slot.slot_id == "s3").contents
    bundle = helpers._run(fixture, _decisions(
        ("read_drive_file", {"file_id": fixture_d.base.BRIEF_FILE}),
    ), case=empty)
    assert bundle.exposures[0].material.resource_id == fixture_d.base.BRIEF_FILE


@pytest.mark.parametrize("rewrite", [False, True])
def test_search_then_real_fixed_read_yields_registered_r_and_same_exchange_j(rewrite):
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    prepared = build_envelope(
        helpers._normal_control_case(fixture), manifest=fixture.manifest,
        base_world=fixture.base_world, overlay=fixture.overlay, budget=helpers._budget(),
        episode_id="episode-target-discovery", arm_id="target-preserving",
    )
    runtime = assemble_tool_runtime(
        materialized=prepared.materialized, manifest=fixture.manifest,
    )
    port = ToolRuntimePort(
        runtime, slots=SlotResolver.from_overlay(fixture.overlay),
        registered=RegisteredContent.from_manifest(fixture.manifest),
    )
    search = port.execute(ModelDecision(
        call_id="model.search", action_request_id="action.search",
        tool_name="search_drive_files", arguments={"page_size": 25},
    ))
    discovered = next(
        item["file_id"] for item in search.model_visible_result["data"]["items"]
        if item["file_id"] == fixture_d.base.BRIEF_FILE
    )
    case = helpers._case_with_rewritten_brief(fixture) if rewrite else None
    bundle = helpers._run(fixture, _decisions(
        ("search_drive_files", {"page_size": 25}),
        ("read_drive_file", {"file_id": discovered}),
    ), case=case)
    verify_bundle(bundle)
    assert type(bundle).model_validate_json(bundle.model_dump_json()) == bundle
    assert bundle.model_decisions[0].tool_name == "search_drive_files"
    assert bundle.tool_returns[0].execution_evidence.status.value == "succeeded"
    assert len(bundle.exposures) == 1
    fact = bundle.exposures[0]
    assert fact.material.slot_id is None
    assert fact.material.resource_id == fixture_d.base.BRIEF_FILE
    assert bundle.tool_returns[1].execution_evidence.read_origin == "frozen_resource"
    assert bundle.tool_returns[1].execution_evidence.read_resource.version_id == (
        "f-draft-brief-v1"
    )
    assert bundle.frozen_resource_digests[0][3] == (
        fixture.manifest.registered_files[0].content_digest
    )
    coverage = extract_coverage(bundle, manifest=fixture.manifest)
    assert any(item.event_kind is RiskEventKind.READ_RESTRICTED for item in coverage.risk)
    assert any(item.relation is JointRelation.SAME_EXCHANGE for item in coverage.joint)
    assert any(item.event_kind is RiskEventKind.READ_RESTRICTED for item in coverage.risk)


def test_unread_fixed_asset_and_no_delivery_do_not_earn_restricted_r_or_task_completion():
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    bundle = helpers._run(fixture, _decisions(
        ("search_drive_files", {"page_size": 25}),
        ("read_drive_file", {"file_id": fixture_d.PUBLIC_ANCHOR_FILE}),
    ))
    verify_bundle(bundle)
    assert not bundle.exposures
    assert not bundle.frozen_resource_digests
    coverage = extract_coverage(bundle, manifest=fixture.manifest)
    assert not any(item.event_kind is RiskEventKind.READ_RESTRICTED for item in coverage.risk)
    finalized = helpers._finalized(fixture, _decisions(
        ("read_drive_file", {"file_id": fixture_d.PUBLIC_ANCHOR_FILE}),
    ), episode_id="episode-target-no-delivery")
    assert not judge_normal_control(finalized, manifest=fixture.manifest,
                                    overlay=fixture.overlay).accepted


def test_fixed_read_rejects_a_missing_formal_return_or_altered_registered_digest():
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    bundle = helpers._run(fixture, _decisions(
        ("read_drive_file", {"file_id": fixture_d.base.BRIEF_FILE}),
    ))
    reported = bundle.tool_returns[0]
    corrupted = reported.model_copy(update={"execution_evidence": None})
    bad = bundle.model_copy(update={"tool_returns": (corrupted,)})
    bad = bad.model_copy(update={"bundle_digest": sha256_digest(bad.digest_payload())})
    with pytest.raises(EnvelopeRefusal) as error:
        verify_bundle(bad)
    assert error.value.code is FailureCode.EXPOSURE_UNBOUND
    incorrect = tuple((*entry[:3], "sha256:" + "0" * 64)
                      for entry in bundle.frozen_resource_digests)
    bad_digest = bundle.model_copy(update={"frozen_resource_digests": incorrect})
    bad_digest = bad_digest.model_copy(update={
        "bundle_digest": sha256_digest(bad_digest.digest_payload()),
    })
    assert not any(item.event_kind is RiskEventKind.READ_RESTRICTED for item in (
        extract_coverage(bad_digest, manifest=fixture.manifest).risk
    ))


def test_old_slot_bundle_remains_recomputable_with_its_existing_fixture_identity():
    old = load_fixture(old_fixture.FIXTURE_ID)
    bundle = helpers._run(old, _decisions(
        ("read_drive_file", {"file_id": fixture_d.base.BRIEF_FILE}),
    ))
    verify_bundle(bundle)
    assert not bundle.frozen_resource_digests
    assert bundle.exposures[0].material.slot_id == "s3"
    assert bundle.exposures[0].material.resource_id is None
    assert "frozen_resource_digests" not in bundle.model_dump(mode="json")
    assert "fixed_registered_files" not in old.manifest.model_dump(mode="json")
    assert "fixed_registered_reads" not in old.overlay.model_dump(mode="json")


def test_provider_rejects_full_registered_file_without_receiving_its_raw_body():
    fixture = load_fixture(fixture_d.FIXTURE_ID)

    class EchoProvider:
        def __init__(self):
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return ProviderTextResponse(
                provider_id="scripted", provider_version="1",
                items=(ProviderTextItem(node_id="n1", text=fixture_d.FIXED_RESTRICTED_BODY),),
            )

    provider = EchoProvider()
    result = prepare_texts(
        provider=provider, manifest=fixture.manifest, node_ids=("n1",),
        obligation_direction="data-release",
    )
    assert not result.accepted
    assert len(provider.requests) == 2
    assert all(item.failure_class is ProviderFailureClass.PRIVATE_VALUE
               for item in result.attempts)
    assert all(fixture_d.FIXED_RESTRICTED_BODY not in request.model_dump_json()
               for request in provider.requests)


def test_changed_fixed_file_is_refused_before_any_episode_executes():
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    prepared = build_envelope(
        helpers._normal_control_case(fixture), manifest=fixture.manifest,
        base_world=fixture.base_world, overlay=fixture.overlay, budget=helpers._budget(),
        episode_id="episode-target-invalid", arm_id="target-preserving",
    )
    replaced = tuple(
        item.model_copy(update={"content": "replaced"})
        if item.file_id == fixture_d.base.BRIEF_FILE else item
        for item in fixture.overlay.drive_files
    )
    overlay = fixture.overlay.model_copy(update={"drive_files": replaced})
    with pytest.raises(EnvelopeRefusal) as error:
        verify_envelope(prepared.envelope.model_copy(update={"overlay": overlay}))
    assert error.value.code is FailureCode.FIXTURE_MISMATCH


def test_new_fixture_arms_share_the_schedule_and_only_guided_reads_feedback():
    fixture = load_fixture(fixture_d.FIXTURE_ID)
    inputs = prepare_inputs(fixture.manifest, count=10)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    guided = TwoArmSearch(parents, fixture.manifest, seed="mat-one-pair")
    random = TwoArmSearch(parents, fixture.manifest, seed="mat-one-pair")
    directions = []
    for _ in range(6):
        guided_receipt = guided.select(ArmKind.COVERAGE_GUIDED)
        random_receipt = random.select(ArmKind.RANDOM_EVOLUTION)
        assert guided_receipt.selected_direction == random_receipt.selected_direction
        assert random_receipt.feedback_sources == ()
        assert random_receipt.parent_baseline is None
        directions.append(guided_receipt.selected_direction)
        guided.generate(guided_receipt)
        random.generate(random_receipt)
    assert directions == ["data-release", "audience-scope"] * 3
    assert fixture.manifest.manifest_digest == guided.manifest.manifest_digest
    assert fixture.manifest.manifest_digest == random.manifest.manifest_digest
