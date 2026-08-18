from __future__ import annotations

import pytest

from sandbox.scenarios.office_v2.interaction import (
    InteractionFailureCode,
    InteractionStatus,
)
from sandbox.scenarios.office_v2.models import BindingResolutionStatus
from sandbox.scenarios.office_v2.world import StateObjectKind
from scripts.build_office_v2_stage2_evidence import build_stage2_evidence


@pytest.fixture(scope="module")
def stage2_evidence() -> dict[str, object]:
    return build_stage2_evidence()


def test_stage2_business_chain_is_fact_complete_and_digest_locked(
    stage2_evidence: dict[str, object],
) -> None:
    evidence = stage2_evidence
    observation = evidence["observation"]
    resolution = evidence["resolution"]
    authorization = evidence["authorization"]
    binding = resolution["binding"]
    interaction = authorization["interaction"]

    assert observation["page_size"] == 3
    assert observation["has_more"] is True
    assert len(resolution["candidate_refs"]) == 2
    assert resolution["candidate_evidence_count"] > observation["page_size"]
    assert resolution["selection_status"] == InteractionStatus.SELECTION_ACCEPTED
    assert (
        binding["resolution_status"]
        == BindingResolutionStatus.RESOLVED_AFTER_CLARIFICATION
    )
    assert binding["resource_refs"] == [
        {
            "schema_version": "office-v2.0",
            "kind": "drive_file",
            "resource_id": "drive.apollo.review-plan",
            "version_id": "version.apollo.review-plan.2",
        }
    ]
    assert authorization["before"]["platform_allowed"] is True
    assert authorization["before"]["delegation_allowed"] is False
    assert authorization["after"]["platform_allowed"] is True
    assert authorization["after"]["delegation_allowed"] is True
    assert interaction["status"] == InteractionStatus.GRANT_CREATED
    assert authorization["before_episode_digest"] != authorization["after_episode_digest"]
    assert interaction["transition"]["state_delta"]["created_objects"] == [
        {
            "schema_version": "office-v2.0",
            "kind": StateObjectKind.DELEGATION_GRANT,
            "object_id": interaction["grant"]["grant_id"],
        }
    ]
    assert evidence["canonical_unchanged"] is True


def test_stage2_forged_business_content_cannot_change_authority(
    stage2_evidence: dict[str, object],
) -> None:
    evidence = stage2_evidence
    forged = evidence["counterexamples"]["forged_content"]

    assert forged["outcome"]["status"] == InteractionStatus.REJECTED
    assert (
        forged["outcome"]["failure_code"]
        == InteractionFailureCode.UNTRUSTED_CHANNEL
    )
    assert forged["before_digest"] == forged["after_digest"]


def test_stage2_invalid_resource_rolls_back_without_partial_state(
    stage2_evidence: dict[str, object],
) -> None:
    evidence = stage2_evidence
    failed = evidence["counterexamples"]["failed_transaction"]

    assert "unknown resource" in failed["error"]
    assert failed["transition"]["committed"] is False
    assert failed["transition"]["failure_code"] == "transaction_validation_failed"
    assert failed["transition"]["state_delta"]["changed_fields"] == []
    assert failed["transition"]["state_delta"]["created_objects"] == []
    assert failed["transition"]["state_delta"]["removed_objects"] == []
    assert failed["before_digest"] == failed["after_digest"]
