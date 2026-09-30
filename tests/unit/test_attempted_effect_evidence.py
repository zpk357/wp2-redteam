from __future__ import annotations

import pytest

from sandbox.structured_v1.evidence import DeliveryChannel
from sandbox.structured_v1.session import ToolCallReport

DIGEST = "sha256:" + "a" * 64


def _report(*, channel: DeliveryChannel, committed: bool, blocked: bool = False,
            post_submit: bool = False, created_objects: tuple[str, ...] = ()) -> ToolCallReport:
    return ToolCallReport(
        tool_call_id="call-1",
        tool_name="test.tool",
        action_request_id="action-1",
        channel=channel,
        committed=committed,
        blocked=blocked,
        post_submit=post_submit,
        world_transition_digest=DIGEST,
        before_state_digest=DIGEST,
        after_state_digest=DIGEST,
        content_digest=DIGEST,
        proof_digest=DIGEST,
        created_objects=created_objects,
    )


@pytest.mark.parametrize(
    "channel",
    [DeliveryChannel.MESSAGE, DeliveryChannel.SHARED_STORAGE, DeliveryChannel.PUBLIC_LINK],
)
def test_uncommitted_external_call_produces_attempt_effect(channel: DeliveryChannel) -> None:
    report = _report(channel=channel, committed=False)

    assert report.produced_an_effect is True
    assert report.committed is False
    assert report.blocked is False


def test_blocked_private_and_post_submit_calls_do_not_become_attempts() -> None:
    assert _report(
        channel=DeliveryChannel.MESSAGE, committed=False, blocked=True
    ).produced_an_effect is True
    assert _report(
        channel=DeliveryChannel.ACTOR_PRIVATE, committed=False
    ).produced_an_effect is False
    assert _report(
        channel=DeliveryChannel.MESSAGE, committed=False, post_submit=True
    ).produced_an_effect is False
