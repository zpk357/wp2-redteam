from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_corpus import ExecutionCosts
from sandbox.fuzzer.v2_work import (
    AttemptDisposition,
    AttemptReceipt,
    BudgetReservation,
    CandidateWork,
    CandidateWorkState,
    retry_allowed,
    seal_work_contract,
    total_attempt_costs,
)
from sandbox.replay.digests import sha256_digest


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def work(state: CandidateWorkState = CandidateWorkState.EXECUTING) -> CandidateWork:
    return seal_work_contract(
        CandidateWork,
        {
            "work_id": "work-1",
            "campaign_id": "campaign-1",
            "generation_allocation_id": "allocation-1",
            "generation_allocation_digest": digest("allocation"),
            "comparison_context_digest": digest("comparison"),
            "baseline_snapshot_digest": digest("baseline"),
            "max_attempts": 2,
            "budget_reservation": BudgetReservation(agent_tokens=500),
            "state": state,
        },
        "work_digest",
    )


def receipt(
    attempt_number: int,
    disposition: AttemptDisposition,
    error_code: str | None,
    cost: int,
) -> AttemptReceipt:
    return seal_work_contract(
        AttemptReceipt,
        {
            "attempt_id": f"attempt-{attempt_number}",
            "work_id": "work-1",
            "attempt_number": attempt_number,
            "disposition": disposition,
            "error_code": error_code,
            "response_digest": digest(f"response-{attempt_number}"),
            "response_byte_count": 80,
            "bounded_summary": "bounded diagnostic",
            "costs": ExecutionCosts(agent_tokens=cost, elapsed_ms=10),
        },
        "receipt_digest",
    )


def test_only_explicit_transient_failure_can_create_bounded_next_attempt() -> None:
    current = work()
    transient = receipt(1, AttemptDisposition.RETRYABLE, "provider-503", 100)
    permanent = receipt(1, AttemptDisposition.PERMANENT_FAILURE, "config-error", 100)
    assert retry_allowed(work=current, receipts=(transient,)) is True
    assert retry_allowed(work=current, receipts=(permanent,)) is False
    assert retry_allowed(work=current, receipts=(transient, transient)) is False


def test_ambiguous_and_sealed_work_never_auto_retry() -> None:
    transient = receipt(1, AttemptDisposition.RETRYABLE, "provider-503", 100)
    assert retry_allowed(
        work=work(CandidateWorkState.AMBIGUOUS), receipts=(transient,)
    ) is False

    sealed_payload = work().model_dump(mode="python", exclude={"work_digest"})
    sealed_payload.update(
        state=CandidateWorkState.SEALED,
        sealed_execution_record_id="execution-1",
        attempt_ids=("attempt-1",),
    )
    sealed = seal_work_contract(CandidateWork, sealed_payload, "work_digest")
    assert retry_allowed(work=sealed, receipts=(transient,)) is False


def test_unknown_failure_cannot_be_marked_retryable() -> None:
    with pytest.raises(ValueError, match="non-whitelisted"):
        receipt(1, AttemptDisposition.RETRYABLE, "mystery-error", 10)


def test_all_attempt_costs_accumulate_even_when_later_attempt_succeeds() -> None:
    first = receipt(1, AttemptDisposition.RETRYABLE, "provider-503", 100)
    second = receipt(2, AttemptDisposition.SUCCEEDED, None, 200)
    costs = total_attempt_costs((first, second))
    assert costs.agent_tokens == 300
    assert costs.elapsed_ms == 20


def test_receipt_content_is_digest_locked() -> None:
    original = receipt(1, AttemptDisposition.RETRYABLE, "provider-503", 100)
    payload = original.model_dump(mode="python")
    payload["bounded_summary"] = "silently rewritten"
    with pytest.raises(ValueError, match="digest does not match"):
        AttemptReceipt.model_validate(payload)
