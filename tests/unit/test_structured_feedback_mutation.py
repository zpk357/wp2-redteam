"""FDM-T02: verified parent evidence and the bounded public feedback contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_structured_fixture_branching as helpers  # noqa: E402
import test_structured_fixture_e2 as e2  # noqa: E402

from sandbox.replay.digests import sha256_digest  # noqa: E402
from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_b as B  # noqa: E402
from sandbox.structured_v1.bundle import finalize_bundle, write_finalized_bundle  # noqa: E402
from sandbox.structured_v1.coverage import bind_coverage_execution, extract_coverage  # noqa: E402
from sandbox.structured_v1.feedback import (  # noqa: E402
    ParentBundleNotFound,
    ParentEvidenceError,
    PublicFeedback,
    build_parent_feedback,
    load_finalized_bundle_for_digest,
    validate_public_feedback,
)
from sandbox.structured_v1.host import (  # noqa: E402
    build_host_closure_receipt,
    build_runtime_receipt,
)
from sandbox.structured_v1.model import ModelDecision  # noqa: E402
from sandbox.structured_v1.provider import (  # noqa: E402
    ProviderBoundaryError,
    ProviderTextItem,
    ProviderTextResponse,
    prepare_texts,
)


def _complete_parent():
    fixture, bundle = e2._run(branch=e2.READ_ONLY)
    case = helpers._normal_control_case(fixture)
    runtime = build_runtime_receipt(
        episode_id=bundle.episode_id,
        container_id="offline-container",
        image_digest="sha256:" + "a" * 64,
        container_bundle_digest=bundle.bundle_digest,
        source="test-substitute",
    )
    closure = build_host_closure_receipt(
        runtime_receipt=runtime,
        stop_requested=True,
        container_absent=True,
        no_post_bundle_activity=True,
        isolation_confirmed=True,
        source="test-substitute",
    )
    finalized = finalize_bundle(bundle, runtime_receipt=runtime, host_closure_receipt=closure)
    result = bind_coverage_execution(
        extract_coverage(bundle, manifest=fixture.manifest),
        bundle=bundle,
        case=case,
        manifest=fixture.manifest,
        execution_config_digest=sha256_digest({"offline": "fdm-t02"}),
    )
    return fixture, case, finalized, result.execution


def test_public_feedback_is_closed_and_canonical(manifest):
    feedback = PublicFeedback(
        observed_slot_ids=("s2", "s1", "s1"),
        observed_tool_roles=("read", "discover", "read"),
        action_window="observed",
    )
    assert feedback.observed_slot_ids == ("s1", "s2")
    assert feedback.observed_tool_roles == ("discover", "read")
    validate_public_feedback(feedback, manifest=manifest)
    assert set(feedback.model_dump(mode="json")) == {
        "feedback_version", "observed_slot_ids", "observed_tool_roles",
        "action_window", "repeat_observation",
    }


def test_public_feedback_rejects_unknown_slot_and_role(manifest):
    with pytest.raises(ParentEvidenceError):
        from sandbox.structured_v1.feedback import validate_public_feedback
        validate_public_feedback(
            PublicFeedback(observed_slot_ids=("not-registered",)), manifest=manifest
        )
    with pytest.raises(ValueError):
        PublicFeedback(observed_tool_roles=("oracle",))


def test_complete_parent_without_slot_exposure_stays_unknown_and_is_replayable(tmp_path):
    fixture, case, finalized, identity = _complete_parent()
    path = write_finalized_bundle(finalized, tmp_path / "parent.json")
    loaded = load_finalized_bundle_for_digest(tmp_path, bundle_digest=identity.bundle_digest)
    assert loaded.final_bundle_digest == finalized.final_bundle_digest
    evidence = build_parent_feedback(
        loaded,
        manifest=fixture.manifest,
        case=case,
        execution_identity=identity,
        target_slot_id="s1",
    )
    assert evidence.public_feedback.action_window == "unknown"
    assert evidence.public_feedback.observed_slot_ids == ()
    assert evidence.exposure_count == 1
    assert evidence.complete_without_slot_exposure is True
    assert path.exists()


def test_parent_digest_lookup_fails_closed(tmp_path):
    with pytest.raises(ParentBundleNotFound):
        load_finalized_bundle_for_digest(
            tmp_path, bundle_digest="sha256:" + "b" * 64
        )


def test_parent_identity_and_incomplete_bundle_are_not_reconstructed_from_keys():
    fixture, case, finalized, identity = _complete_parent()
    incomplete = finalize_bundle(
        finalized.container_bundle,
        runtime_receipt=finalized.runtime_receipt,
        host_closure_receipt=build_host_closure_receipt(
            runtime_receipt=finalized.runtime_receipt,
            stop_requested=True,
            container_absent=False,
            no_post_bundle_activity=True,
            isolation_confirmed=True,
            source="test-substitute",
        ),
    )
    with pytest.raises(ParentEvidenceError, match="incomplete"):
        build_parent_feedback(
            incomplete,
            manifest=fixture.manifest,
            case=case,
            execution_identity=identity,
        )


class _Provider:
    provider_id = "scripted"
    provider_version = "fdm-t02"

    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ProviderTextResponse(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            items=tuple(
                ProviderTextItem(node_id=node, text="改写文本")
                for node in request.node_ids
            ),
        )


def test_provider_receives_only_public_feedback_and_no_host_references(manifest):
    provider = _Provider()
    feedback = PublicFeedback(observed_slot_ids=(manifest.slots[0].slot_id,))
    preparation = prepare_texts(
        provider=provider,
        manifest=manifest,
        node_ids=("n1",),
        obligation_direction="data-release",
        feedback=feedback,
    )
    assert preparation.accepted
    payload = json.dumps(provider.requests[0].model_dump(mode="json"), ensure_ascii=False)
    assert "evidence_refs" not in payload
    assert "bundle_digest" not in payload
    assert "risk_keys" not in payload
    assert provider.requests[0].feedback == feedback
    assert preparation.request_summaries[0].feedback_digest == feedback.canonical_digest()


def test_tampered_projection_is_rejected_before_provider_call(manifest):
    from sandbox.structured_v1.projection import build_public_projection
    from sandbox.structured_v1.provider import (
        ProviderRequestKind,
        ProviderTextRequest,
        verify_provider_request,
    )

    projection = build_public_projection(manifest=manifest, parent=None)
    tampered = projection.model_copy(update={"public_task_text": "changed"})
    request = ProviderTextRequest(
        request_id="fdm-t02",
        kind=ProviderRequestKind.GENERATION,
        projection=tampered,
        node_ids=("n1",),
        obligation_direction="data-release",
    )
    with pytest.raises(ProviderBoundaryError):
        verify_provider_request(request, manifest=manifest)


def test_slot_window_uses_decision_order_and_keeps_source_slot_evidence():
    fixture = load_fixture(B.FIXTURE_ID)
    case = helpers._normal_control_case(fixture)
    decisions = [
        ModelDecision(
            call_id="model.001",
            action_request_id="action.0001",
            tool_name="read_drive_file",
            arguments={"file_id": B.BRIEF_FILE},
        ),
        ModelDecision(
            call_id="model.002",
            action_request_id="action.0002",
            tool_name="send_email",
            arguments={"to": [B.PARTNER_ID], "subject": "summary", "body": "public"},
        ),
        ModelDecision(call_id="model.003"),
    ]
    bundle = helpers._run(fixture, decisions, case=case)
    runtime = build_runtime_receipt(
        episode_id=bundle.episode_id,
        container_id="offline-container",
        image_digest="sha256:" + "a" * 64,
        container_bundle_digest=bundle.bundle_digest,
        source="test-substitute",
    )
    finalized = finalize_bundle(
        bundle,
        runtime_receipt=runtime,
        host_closure_receipt=build_host_closure_receipt(
            runtime_receipt=runtime,
            stop_requested=True,
            container_absent=True,
            no_post_bundle_activity=True,
            isolation_confirmed=True,
            source="test-substitute",
        ),
    )
    execution = bind_coverage_execution(
        extract_coverage(bundle, manifest=fixture.manifest),
        bundle=bundle,
        case=case,
        manifest=fixture.manifest,
        execution_config_digest=sha256_digest({"offline": "fdm-t02-window"}),
    ).execution
    evidence = build_parent_feedback(
        finalized,
        manifest=fixture.manifest,
        case=case,
        execution_identity=execution,
        target_slot_id="s3",
    )
    proof = next(item for item in evidence.slot_proofs if item.slot_id == "s3")
    assert proof.action_window == "observed"
    assert evidence.public_feedback.action_window == "observed"
    assert "read" in evidence.public_feedback.observed_tool_roles
    assert all("risk" not in item for item in evidence.public_feedback.model_dump(mode="json"))
