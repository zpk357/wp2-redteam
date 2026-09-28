"""Cost classification for recorded Agent token usage (ER-06 / ER-07).

The chain under test is: recorded decisions -> classification -> sealed receipt
marker. Nothing here needs Docker or a real model.
"""

from __future__ import annotations

import json

import pytest

from sandbox.fuzzer.v2_real_episode import (
    TOKEN_USAGE_INCOMPLETE_MARKER,
    _recorded_agent_tokens,
)
from sandbox.fuzzer.v2_real_runtime import _successful_receipt
from sandbox.replay.models import RECORDED_MODEL_TOKEN_USAGE_KEY

DIGEST = "sha256:" + "0" * 64


def decision(*, usage: dict[str, object] | None = None, index: int = 0) -> str:
    action: dict[str, object] = {"kind": "call"}
    if usage is not None:
        action[RECORDED_MODEL_TOKEN_USAGE_KEY] = usage
    return json.dumps(
        {
            "decision_id": f"decision-{index}",
            "sequence": index,
            "decision_index": index,
            "before_checkpoint_id": f"checkpoint-{index}",
            "input_digest": DIGEST,
            "output_digest": DIGEST,
            "action": action,
            "model_name": "qwen3.5:27b-q4_K_M",
            "model_version": "test",
        }
    )


def payload(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_complete_usage_sums_and_a_legal_zero_is_not_missing() -> None:
    usage = _recorded_agent_tokens(
        payload(
            decision(usage={"prompt_tokens": 10, "completion_tokens": 5}),
            decision(usage={"prompt_tokens": 0, "completion_tokens": 0}, index=1),
        )
    )

    assert usage.total == 15
    assert usage.decisions == 2
    assert usage.missing_decisions == 0
    assert usage.complete is True
    assert usage.unbounded is False


def test_missing_usage_is_partial_and_keeps_the_known_total() -> None:
    usage = _recorded_agent_tokens(
        payload(
            decision(usage={"prompt_tokens": 7, "completion_tokens": 3}),
            decision(index=1),
        )
    )

    assert usage.total == 10
    assert usage.decisions == 2
    assert usage.missing_decisions == 1
    assert usage.complete is False
    assert usage.unbounded is False


def test_usage_missing_everywhere_is_unbounded_but_not_an_error() -> None:
    usage = _recorded_agent_tokens(payload(decision(), decision(index=1)))

    assert usage.total == 0
    assert usage.missing_decisions == 2
    assert usage.complete is False
    assert usage.unbounded is True


@pytest.mark.parametrize(
    "bad",
    (
        {"prompt_tokens": -1, "completion_tokens": 0},
        {"prompt_tokens": "10", "completion_tokens": 0},
        {"prompt_tokens": 1, "completion_tokens": True},
    ),
)
def test_invalid_usage_stays_a_hard_error(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="token usage is invalid"):
        _recorded_agent_tokens(payload(decision(usage=bad)))


def test_a_recording_without_decisions_stays_a_hard_error() -> None:
    with pytest.raises(ValueError, match="no model decisions"):
        _recorded_agent_tokens(b"")


def test_incomplete_cost_is_marked_on_the_sealed_receipt() -> None:
    complete = _successful_receipt(
        work_id="work.abcdef",
        attempt_number=1,
        manifest_digest=DIGEST,
        agent_tokens=10,
        elapsed_ms=20,
    )
    assert complete.bounded_summary == "sealed Office V2 recording"
    assert complete.costs.agent_tokens == 10

    marked = _successful_receipt(
        work_id="work.abcdef",
        attempt_number=1,
        manifest_digest=DIGEST,
        agent_tokens=10,
        elapsed_ms=20,
        token_usage_missing=(1, 3),
    )
    # The Episode still settles as a success: only the cost is flagged, so the
    # behaviour evidence is never traded away for an accounting detail.
    assert marked.disposition is complete.disposition
    assert marked.bounded_summary.endswith(f"{TOKEN_USAGE_INCOMPLETE_MARKER}:1/3")
    assert marked.costs == complete.costs
    assert marked.receipt_digest != complete.receipt_digest
