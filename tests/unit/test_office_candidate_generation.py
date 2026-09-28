from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.fuzzer.models import CampaignManifest, ScenarioCampaignManifest
from sandbox.scenarios.candidate_generation import (
    OFFICE_V1_CANDIDATE_CATALOG,
    OFFICE_V1_CATALOG_MANIFEST,
    CandidateGenerationResult,
    CandidateGenerationStatus,
    CandidateRejectionCode,
    CandidateSelection,
    CatalogIntegrityError,
    OfficeCandidateCatalog,
    OfficeCandidateGenerator,
)
from sandbox.scenarios.catalogs import ScenarioCatalogManifest
from sandbox.scenarios.models import ActionScope, BenignTask, CompositionIssueCode
from sandbox.scenarios.office_v1 import (
    ATTACK_RECIPIENT,
    CREATE_MEETING_FROM_EMAIL,
    EMAIL_BODY_CARRIER,
    EXTERNAL_RESTRICTED_FILE_SHARE,
    FAKE_AGENT,
    OFFICE_V1,
)


def _selection(**updates: object) -> CandidateSelection:
    payload: dict[str, object] = {
        "selection_id": "office-selection-001",
        "task_id": CREATE_MEETING_FROM_EMAIL.task_id,
        "objective_id": EXTERNAL_RESTRICTED_FILE_SHARE.objective_id,
        "carrier_id": EMAIL_BODY_CARRIER.carrier_id,
        "expression_id": "direct",
        "agent": FAKE_AGENT,
    }
    payload.update(updates)
    return CandidateSelection.model_validate(payload)


def _campaign_manifest() -> ScenarioCampaignManifest:
    return ScenarioCampaignManifest(
        campaign_id="office-candidate-generation",
        config_digest="sha256:" + "1" * 64,
        taxonomy_version="risk-taxonomy-v1",
        taxonomy_digest="sha256:" + "2" * 64,
        risk_scope_version="office-risk-v1",
        risk_scope_digest="sha256:" + "3" * 64,
        mutation_registry_version="mutation-v1",
        mutation_registry_digest="sha256:" + "4" * 64,
        mutation_provider="rule_based",
        mutation_provider_version="test-double-v1",
        agent_model_name="workspace-control",
        agent_image="trace-redteam-agent:test",
        target_profile_id="office-v1",
        energy_formula_version="energy-v1",
        corpus_policy_version="corpus-v1",
        scheduler_policy_version="scheduler-v1",
        random_seed=42,
        scenario_catalogs=OFFICE_V1_CATALOG_MANIFEST,
    )


def test_office_catalog_manifest_locks_each_component_catalog_separately() -> None:
    manifest = OFFICE_V1_CATALOG_MANIFEST

    manifest.assert_integrity()
    assert len(manifest.scenario.item_ids) == 1
    assert len(manifest.benign_tasks.item_ids) == 6
    assert len(manifest.attack_objectives.item_ids) == 6
    assert len(manifest.injection_carriers.item_ids) == 3
    assert manifest.attack_expressions.item_ids == ("direct", "workflow-note")
    assert len(
        {
            manifest.scenario.content_digest,
            manifest.benign_tasks.content_digest,
            manifest.attack_objectives.content_digest,
            manifest.injection_carriers.content_digest,
            manifest.attack_expressions.content_digest,
        }
    ) == 5


def test_campaign_manifest_requires_catalog_locks_for_office_generation() -> None:
    generator = OfficeCandidateGenerator.from_campaign_manifest(_campaign_manifest())
    assert generator.manifest == OFFICE_V1_CATALOG_MANIFEST

    legacy_payload = _campaign_manifest().model_dump(
        mode="python", exclude={"scenario_catalogs"}
    )
    legacy = CampaignManifest.model_validate(legacy_payload)
    assert not hasattr(legacy, "scenario_catalogs")
    with pytest.raises(CatalogIntegrityError, match="missing scenario catalog locks"):
        OfficeCandidateGenerator.from_campaign_manifest(legacy)


def test_generator_rejects_a_valid_but_different_catalog_lock() -> None:
    payload = OFFICE_V1_CATALOG_MANIFEST.model_dump(
        mode="python", exclude={"content_digest"}
    )
    payload["attack_objectives"]["catalog_version"] = "2.0"
    changed = ScenarioCatalogManifest.model_validate(payload)

    with pytest.raises(CatalogIntegrityError, match="attack_objectives catalog lock"):
        OfficeCandidateGenerator(changed)


def test_generator_detects_nested_catalog_tampering_after_startup() -> None:
    catalog = OfficeCandidateCatalog.model_validate_json(
        OFFICE_V1_CANDIDATE_CATALOG.model_dump_json()
    )
    generator = OfficeCandidateGenerator(catalog.manifest(), catalog)
    catalog.benign_tasks[0].parameters["email_id"] = "tampered-email"

    with pytest.raises(CatalogIntegrityError, match="benign_tasks catalog lock"):
        generator.generate(_selection())


def test_generation_is_deterministic_and_expression_is_independently_selectable() -> None:
    generator = OfficeCandidateGenerator(OFFICE_V1_CATALOG_MANIFEST)
    direct = generator.generate(_selection())
    repeated = generator.generate(_selection())
    wrapped = generator.generate(
        _selection(
            selection_id="office-selection-002",
            expression_id="workflow-note",
        )
    )

    assert direct == repeated
    assert direct.status == CandidateGenerationStatus.ACCEPTED
    assert direct.candidate is not None
    assert direct.candidate.attack is not None
    assert direct.candidate.attack.objective == EXTERNAL_RESTRICTED_FILE_SHARE
    assert direct.candidate.attack.carrier == EMAIL_BODY_CARRIER
    assert direct.candidate.content_digest == repeated.candidate.content_digest
    assert wrapped.candidate is not None
    assert wrapped.candidate.attack is not None
    assert wrapped.candidate.attack.payload != direct.candidate.attack.payload
    assert wrapped.candidate.content_digest != direct.candidate.content_digest
    direct.assert_integrity()
    wrapped.assert_integrity()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("task_id", "missing-task", CandidateRejectionCode.UNKNOWN_TASK),
        ("objective_id", "missing-objective", CandidateRejectionCode.UNKNOWN_OBJECTIVE),
        ("carrier_id", "missing-carrier", CandidateRejectionCode.UNKNOWN_CARRIER),
        ("expression_id", "missing-expression", CandidateRejectionCode.UNKNOWN_EXPRESSION),
    ),
)
def test_unknown_catalog_component_is_rejected_before_execution(
    field: str, value: str, expected: CandidateRejectionCode
) -> None:
    result = OfficeCandidateGenerator(OFFICE_V1_CATALOG_MANIFEST).generate(
        _selection(**{field: value})
    )

    assert result.status == CandidateGenerationStatus.REJECTED
    assert result.candidate is None
    assert result.rejection is not None
    assert result.rejection.code == expected
    result.assert_integrity()


def test_unreachable_carrier_returns_stable_composition_reasons() -> None:
    result = OfficeCandidateGenerator(OFFICE_V1_CATALOG_MANIFEST).generate(
        _selection(carrier_id="public-drive-file-content")
    )

    assert result.status == CandidateGenerationStatus.REJECTED
    assert result.rejection is not None
    assert result.rejection.code == CandidateRejectionCode.INCOMPATIBLE_COMPOSITION
    assert set(result.rejection.issue_codes) == {
        CompositionIssueCode.CARRIER_CAPABILITY_NOT_ON_TASK_PATH,
        CompositionIssueCode.CARRIER_TARGET_NOT_OBSERVABLE,
    }


def test_authorized_objective_is_rejected_by_existing_composition_gate() -> None:
    task_payload = CREATE_MEETING_FROM_EMAIL.model_dump(mode="python")
    task_payload["task_id"] = "meeting-with-share-grant"
    task_payload["required_capabilities"] = (
        *task_payload["required_capabilities"],
        "drive.share",
    )
    task_payload["authorized_actions"] = (
        *task_payload["authorized_actions"],
        ActionScope(
            capability_id="drive.share",
            resource=EXTERNAL_RESTRICTED_FILE_SHARE.unauthorized_action.resource,
            recipient_ids=(ATTACK_RECIPIENT,),
        ),
    )
    task = BenignTask.model_validate(task_payload)
    catalog = OfficeCandidateCatalog(
        scenario=OFFICE_V1,
        benign_tasks=(task,),
        attack_objectives=(EXTERNAL_RESTRICTED_FILE_SHARE,),
        injection_carriers=(EMAIL_BODY_CARRIER,),
        expression_ids=("direct",),
    )
    result = OfficeCandidateGenerator(catalog.manifest(), catalog).generate(
        _selection(task_id=task.task_id)
    )

    assert result.rejection is not None
    assert result.rejection.code == CandidateRejectionCode.INCOMPATIBLE_COMPOSITION
    assert result.rejection.issue_codes == (
        CompositionIssueCode.OBJECTIVE_ACTION_AUTHORIZED,
    )


def test_invalid_budget_is_a_structured_rejection() -> None:
    result = OfficeCandidateGenerator(OFFICE_V1_CATALOG_MANIFEST).generate(
        _selection(budget={"max_steps": 0, "timeout_seconds": 120, "max_output_tokens": 4096})
    )

    assert result.status == CandidateGenerationStatus.REJECTED
    assert result.rejection is not None
    assert result.rejection.code == CandidateRejectionCode.INVALID_BUDGET
    assert "max_steps" in result.rejection.detail


def test_selection_and_result_digest_tampering_is_detected() -> None:
    selection_payload = _selection().model_dump(mode="python")
    selection_payload["seed"] = 99
    with pytest.raises(ValidationError, match="selection content_digest does not match"):
        CandidateSelection.model_validate(selection_payload)

    result = OfficeCandidateGenerator(OFFICE_V1_CATALOG_MANIFEST).generate(_selection())
    result_payload = result.model_dump(mode="python")
    result_payload["request_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="result_digest does not match"):
        CandidateGenerationResult.model_validate(result_payload)
