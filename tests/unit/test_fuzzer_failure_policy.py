from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sandbox.engine.case_source import TemplateCaseSource
from sandbox.fuzzer.cli import _CampaignComponents
from sandbox.fuzzer.config import FuzzerConfig
from sandbox.fuzzer.engine import FuzzingEngine
from sandbox.fuzzer.executor import classify_outcome
from sandbox.fuzzer.failure_policy import pause_reason_for_execution_error_code
from sandbox.fuzzer.models import (
    CampaignManifest,
    CampaignStatus,
    CampaignStopReason,
    CandidateExecutionOutcome,
    WorkSourceKind,
    WorkSourceRef,
)
from sandbox.fuzzer.store import FuzzerStore
from sandbox.mutation.exceptions import (
    MutationIntegrityError,
    MutationProviderError,
    MutationProviderFailureKind,
)


def _outcome(error_code: str) -> CandidateExecutionOutcome:
    now = datetime.now(UTC)
    return CandidateExecutionOutcome(
        work_item_id="sha256:" + "1" * 64,
        attempt=1,
        source=WorkSourceRef(kind=WorkSourceKind.INITIAL_CASE, case_id="case"),
        coverage_source_kind="week1",
        execution_id="fuzz-" + "a" * 24,
        execution_status="failed",
        container_removed=True,
        started_at=now,
        finished_at=now,
        duration_ms=1,
        error_code=error_code,
    )


def _manifest(campaign_id: str) -> CampaignManifest:
    digest = "sha256:" + "1" * 64
    return CampaignManifest(
        campaign_id=campaign_id,
        config_digest=digest,
        taxonomy_version="taxonomy-v1",
        taxonomy_digest=digest,
        risk_scope_version="scope-v1",
        risk_scope_digest=digest,
        mutation_registry_version="registry-v1",
        mutation_registry_digest=digest,
        mutation_provider="ollama",
        mutation_provider_version="provider-v1",
        agent_model_name="fake",
        agent_image="agent:test",
        target_profile_id="test",
        energy_formula_version="energy-v1",
        corpus_policy_version="coverage-corpus-v1",
        scheduler_policy_version="single-host-v1",
        random_seed=42,
    )


def test_execution_retry_is_closed_to_known_transient_errors() -> None:
    assert classify_outcome(_outcome("TimeoutError")) == "transient_infrastructure"
    assert classify_outcome(_outcome("InfrastructureError")) == "transient_infrastructure"
    assert classify_outcome(_outcome("RuntimeTimeoutError")) == "transient_infrastructure"
    assert classify_outcome(_outcome("RuntimeTransportError")) == "transient_infrastructure"
    assert classify_outcome(_outcome("PermanentInfrastructureError")) == "integrity_failure"
    assert classify_outcome(_outcome("SandboxConfigurationError")) == "integrity_failure"
    assert classify_outcome(_outcome("APIError")) == "integrity_failure"
    assert classify_outcome(_outcome("DockerException")) == "integrity_failure"
    assert classify_outcome(_outcome("ProtocolError")) == "integrity_failure"
    assert classify_outcome(_outcome("UnexpectedLibraryError")) == "integrity_failure"


@pytest.mark.parametrize(
    ("error_code", "classification"),
    [
        ("agent_no_submit", "case_failure"),
        ("execution_timed_out", "transient_infrastructure"),
        ("timed_out", "transient_infrastructure"),
        ("ollama_response_truncated", "transient_infrastructure"),
        ("execution_cancelled", "cancelled"),
        ("cancelled", "cancelled"),
        ("trace_duplicate_tool_call_id", "integrity_failure"),
        ("trace_duplicate_submit", "integrity_failure"),
        ("trace_mixed_submit_batch", "integrity_failure"),
        ("trace_scenario_state_integrity_error", "integrity_failure"),
        ("trace_scenario_configuration_error", "integrity_failure"),
        ("ollama_model_digest_mismatch", "integrity_failure"),
        ("ollama_response_integrity_error", "integrity_failure"),
        ("ollama_provider_configuration_error", "integrity_failure"),
        ("agent_invalid_submit", "case_failure"),
        ("adapter_terminal_contract_error", "integrity_failure"),
        ("CleanupError", "integrity_failure"),
        ("unknown_error", "integrity_failure"),
    ],
)
def test_runtime_error_codes_have_closed_campaign_classification(
    error_code: str,
    classification: str,
) -> None:
    assert classify_outcome(_outcome(error_code)) == classification


def test_execution_failures_keep_distinct_campaign_pause_reasons() -> None:
    assert (
        pause_reason_for_execution_error_code("SandboxConfigurationError")
        == CampaignStopReason.CONFIGURATION_ERROR
    )
    assert (
        pause_reason_for_execution_error_code("PermanentInfrastructureError")
        == CampaignStopReason.SYSTEMIC_INFRASTRUCTURE_FAILURE
    )
    assert (
        pause_reason_for_execution_error_code("ProtocolError")
        == CampaignStopReason.DATA_INTEGRITY_ERROR
    )
    assert (
        pause_reason_for_execution_error_code("UnexpectedLibraryError")
        == CampaignStopReason.UNCLASSIFIED_ERROR
    )
    assert (
        pause_reason_for_execution_error_code("CleanupError")
        == CampaignStopReason.SYSTEMIC_INFRASTRUCTURE_FAILURE
    )
    assert (
        pause_reason_for_execution_error_code("trace_scenario_configuration_error")
        == CampaignStopReason.CONFIGURATION_ERROR
    )
    assert (
        pause_reason_for_execution_error_code("trace_scenario_state_integrity_error")
        == CampaignStopReason.DATA_INTEGRITY_ERROR
    )
    assert (
        pause_reason_for_execution_error_code("ollama_model_digest_mismatch")
        == CampaignStopReason.MODEL_DIGEST_MISMATCH
    )


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (
            MutationProviderError(
                "digest changed",
                kind=MutationProviderFailureKind.MODEL_MISMATCH,
            ),
            CampaignStopReason.MODEL_DIGEST_MISMATCH,
        ),
        (MutationIntegrityError("conflicting batch"), CampaignStopReason.DATA_INTEGRITY_ERROR),
        (RuntimeError("unexpected"), CampaignStopReason.UNCLASSIFIED_ERROR),
    ],
)
def test_generation_failures_persist_paused_campaign(
    tmp_path: Path,
    error: Exception,
    expected_reason: CampaignStopReason,
) -> None:
    campaign_id = "failure-policy"
    with FuzzerStore(tmp_path, campaign_id) as store:
        store.initialize(_manifest(campaign_id))
        store.transition_campaign(CampaignStatus.BOOTSTRAPPING)
        engine = object.__new__(FuzzingEngine)
        engine.store = store

        engine._pause_for_generation_error(error)

        assert store.status() == CampaignStatus.PAUSED
        assert store.stop_reason() == expected_reason
        audit = store.audit_events()
        assert audit[-1]["data"]["error_code"] == type(error).__name__
        assert audit[-1]["data"]["phase"] == "mutation_generation"


def test_component_setup_model_drift_pauses_existing_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "setup-model-drift"
    config = FuzzerConfig(campaign_id=campaign_id, store_root=tmp_path)
    with FuzzerStore(tmp_path, campaign_id) as store:
        store.initialize(_manifest(campaign_id))

    components = _CampaignComponents(object(), config, TemplateCaseSource())

    def raise_model_drift():
        raise MutationProviderError(
            "digest changed",
            kind=MutationProviderFailureKind.MODEL_MISMATCH,
        )

    monkeypatch.setattr(components, "_assemble_after_store", raise_model_drift)

    with pytest.raises(MutationProviderError, match="digest changed"):
        components.__enter__()

    with FuzzerStore(tmp_path, campaign_id) as store:
        assert store.status() == CampaignStatus.PAUSED
        assert store.stop_reason() == CampaignStopReason.MODEL_DIGEST_MISMATCH
        assert store.audit_events()[-1]["data"]["phase"] == "component_setup"


async def test_engine_guard_pauses_unclassified_exception_from_campaign_body(
    tmp_path: Path,
) -> None:
    campaign_id = "engine-run-guard"
    with FuzzerStore(tmp_path, campaign_id) as store:
        store.initialize(_manifest(campaign_id))
        engine = object.__new__(FuzzingEngine)
        engine.store = store

        async def fail_after_bootstrap() -> CampaignStatus:
            store.transition_campaign(CampaignStatus.BOOTSTRAPPING)
            raise RuntimeError("simulated commit failure")

        engine._run_campaign = fail_after_bootstrap

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await engine.run()

        assert store.status() == CampaignStatus.PAUSED
        assert store.stop_reason() == CampaignStopReason.UNCLASSIFIED_ERROR
        audit = store.audit_events()[-1]["data"]
        assert audit["phase"] == "campaign_run"
        assert audit["error_code"] == "RuntimeError"
