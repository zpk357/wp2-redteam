"""Closed failure classification for campaign retry and pause decisions."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from sandbox.coverage.exceptions import CoverageIntegrityError, TaxonomyError
from sandbox.fuzzer.exceptions import CampaignConfigurationError, FuzzerIntegrityError
from sandbox.fuzzer.models import CampaignStatus, CampaignStopReason, FailureKind
from sandbox.mutation.exceptions import (
    MutationConfigError,
    MutationIntegrityError,
    MutationProviderError,
    MutationProviderFailureKind,
    MutationStorageError,
    MutationTargetError,
)
from sandbox.replay.exceptions import (
    ArtifactIntegrityError,
    CanonicalizationError,
    ManifestIntegrityError,
)

TRANSIENT_EXECUTION_ERROR_CODES = frozenset(
    {
        "ConnectTimeout",
        "ConnectionError",
        "InfrastructureError",
        "LeaseExpired",
        "ReadTimeout",
        "RuntimeTimeoutError",
        "RuntimeTransportError",
        "RuntimeUnavailable",
        "TimeoutError",
        "URLError",
        "execution_timed_out",
        "timed_out",
        "ollama_response_truncated",
    }
)

CASE_EXECUTION_ERROR_CODES = frozenset({"agent_invalid_submit", "agent_no_submit"})

CANCELLED_EXECUTION_ERROR_CODES = frozenset({"cancelled", "execution_cancelled"})

MODEL_DIGEST_EXECUTION_ERROR_CODES = frozenset({"ollama_model_digest_mismatch"})

INTEGRITY_EXECUTION_ERROR_CODES = frozenset(
    {
        "ArtifactIntegrityError",
        "CanonicalizationError",
        "CoverageIntegrityError",
        "FuzzerIntegrityError",
        "ManifestIntegrityError",
        "MutationIntegrityError",
        "MutationStorageError",
        "ProtocolError",
        "TraceIntegrityError",
        "adapter_terminal_contract_error",
        "trace_duplicate_submit",
        "trace_duplicate_tool_call_id",
        "trace_mixed_submit_batch",
        "trace_scenario_state_integrity_error",
        "ollama_response_integrity_error",
    }
)

PERMANENT_EXECUTION_ERROR_CODES = frozenset(
    {
        "CleanupError",
        "PermanentInfrastructureError",
    }
)

CONFIGURATION_EXECUTION_ERROR_CODES = frozenset(
    {
        "CampaignConfigurationError",
        "MutationConfigError",
        "SandboxConfigurationError",
        "TaxonomyError",
        "ollama_provider_configuration_error",
        "trace_scenario_configuration_error",
    }
)


@dataclass(frozen=True)
class CampaignPauseDecision:
    reason: CampaignStopReason
    category: str


def classify_execution_error_code(error_code: str) -> FailureKind:
    if error_code in CASE_EXECUTION_ERROR_CODES:
        return FailureKind.CASE_FAILURE
    if error_code in CANCELLED_EXECUTION_ERROR_CODES:
        return FailureKind.CANCELLED
    if error_code in TRANSIENT_EXECUTION_ERROR_CODES:
        return FailureKind.TRANSIENT_INFRASTRUCTURE
    if error_code in PERMANENT_EXECUTION_ERROR_CODES:
        return FailureKind.INTEGRITY_FAILURE
    if error_code in MODEL_DIGEST_EXECUTION_ERROR_CODES:
        return FailureKind.INTEGRITY_FAILURE
    if error_code in INTEGRITY_EXECUTION_ERROR_CODES:
        return FailureKind.INTEGRITY_FAILURE
    if error_code in CONFIGURATION_EXECUTION_ERROR_CODES:
        return FailureKind.INTEGRITY_FAILURE
    # An unknown exception is a contract breach, never an automatic retry signal.
    return FailureKind.INTEGRITY_FAILURE


def pause_reason_for_execution_error_code(error_code: str | None) -> CampaignStopReason:
    if error_code in CONFIGURATION_EXECUTION_ERROR_CODES:
        return CampaignStopReason.CONFIGURATION_ERROR
    if error_code in MODEL_DIGEST_EXECUTION_ERROR_CODES:
        return CampaignStopReason.MODEL_DIGEST_MISMATCH
    if error_code in PERMANENT_EXECUTION_ERROR_CODES:
        return CampaignStopReason.SYSTEMIC_INFRASTRUCTURE_FAILURE
    if error_code in INTEGRITY_EXECUTION_ERROR_CODES:
        return CampaignStopReason.DATA_INTEGRITY_ERROR
    return CampaignStopReason.UNCLASSIFIED_ERROR


def classify_campaign_exception(error: Exception) -> CampaignPauseDecision:
    if isinstance(error, MutationProviderError):
        if error.recoverable:
            return CampaignPauseDecision(
                CampaignStopReason.TRANSIENT_PROVIDER_UNAVAILABLE,
                "recoverable_provider_error_escaped",
            )
        if error.kind == MutationProviderFailureKind.MODEL_MISMATCH:
            return CampaignPauseDecision(
                CampaignStopReason.MODEL_DIGEST_MISMATCH,
                "model_digest_mismatch",
            )
        return CampaignPauseDecision(
            CampaignStopReason.PERMANENT_PROVIDER_ERROR,
            f"provider_{error.kind.value}",
        )
    if isinstance(
        error,
        (
            MutationConfigError,
            MutationTargetError,
            CampaignConfigurationError,
            TaxonomyError,
            ValidationError,
        ),
    ):
        return CampaignPauseDecision(
            CampaignStopReason.CONFIGURATION_ERROR,
            "configuration",
        )
    if isinstance(
        error,
        (
            MutationIntegrityError,
            MutationStorageError,
            CoverageIntegrityError,
            FuzzerIntegrityError,
            ArtifactIntegrityError,
            ManifestIntegrityError,
            CanonicalizationError,
        ),
    ):
        return CampaignPauseDecision(
            CampaignStopReason.DATA_INTEGRITY_ERROR,
            "data_integrity",
        )
    return CampaignPauseDecision(
        CampaignStopReason.UNCLASSIFIED_ERROR,
        "unclassified",
    )


def pause_campaign_for_exception(
    store,
    error: Exception,
    *,
    phase: str,
) -> CampaignPauseDecision:
    decision = classify_campaign_exception(error)
    audit_data = {
        "phase": phase,
        "failure_category": decision.category,
        "error_code": type(error).__name__,
        "error_detail": str(error)[:500],
    }
    current = store.status()
    if current == CampaignStatus.CREATED:
        store.transition_campaign(
            CampaignStatus.PAUSED,
            reason=decision.reason,
            audit_data=audit_data,
        )
        return decision
    if current in {CampaignStatus.BOOTSTRAPPING, CampaignStatus.RUNNING}:
        store.transition_campaign(
            CampaignStatus.PAUSE_REQUESTED,
            reason=decision.reason,
            audit_data=audit_data,
        )
        current = CampaignStatus.PAUSE_REQUESTED
    if current == CampaignStatus.PAUSE_REQUESTED:
        store.transition_campaign(
            CampaignStatus.PAUSED,
            reason=decision.reason,
            audit_data=audit_data,
        )
    elif current == CampaignStatus.PAUSED:
        store.record_paused_error(reason=decision.reason, audit_data=audit_data)
    return decision
