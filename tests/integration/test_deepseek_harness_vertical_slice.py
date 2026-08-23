from __future__ import annotations

import asyncio

import pytest
from app.adapter.deepseek_harness_adapter import DeepSeekHarnessAdapter

from tests.harness_support import harness_compound_request, harness_request


async def test_harness_calls_real_office_tool_consumes_result_and_submits() -> None:
    request = harness_request()
    adapter = DeepSeekHarnessAdapter()

    try:
        events = [event async for event in adapter.execute(request)]
    except Exception:
        pytest.fail(adapter.last_driver_diagnostic or "Harness driver failed without diagnostics")

    assert events[0].event_type == "execution_started"
    assert events[0].data["agent_runtime"] == "deepseek_harness"
    assert [
        event.data["name"] for event in events if event.event_type == "tool_call"
    ] == [
        "search_files",
        "read_file",
        "search_drive_files",
        "read_drive_file",
        "create_drive_file",
        "read_drive_file",
        "search_calendar_events",
        "create_calendar_event",
    ]
    assert all(
        event.data["status"] == "succeeded"
        for event in events
        if event.event_type == "tool_result"
    )
    assert any(event.event_type == "agent_submit" for event in events)
    assert events[-1].event_type == "execution_finished"
    assert adapter.last_bridge_summary is not None
    assert adapter.last_bridge_summary["initial_state_digest"] != (
        adapter.last_bridge_summary["final_state_digest"]
    )
    assert adapter.last_v2_oracle_artifact is not None
    assert adapter.last_token_usage is not None
    assert adapter.last_token_usage["completion_tokens"] > 0
    assert (
        adapter.last_v2_oracle_artifact.oracle_result.utility.disposition.value
        == "completed"
    )
    decisions = [
        event["data"]
        for event in adapter.last_driver_events
        if event["event_type"] == "model_decision"
    ]
    assert decisions[0]["kind"] == "tool_call"
    assert decisions[-2]["kind"] == "submit"
    assert decisions[-2]["prior_tool_result_sha256"].startswith("sha256:")


async def test_harness_cancellation_reaps_episode_and_marks_it_incomplete(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_G_HARNESS_EPISODE_ROOT", str(tmp_path))
    adapter = DeepSeekHarnessAdapter()

    async def consume() -> None:
        async for _ in adapter.execute(harness_request()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(100):
        if list(tmp_path.glob("trace-g-h4-*/bridge-records.ndjson")):
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert adapter.last_bridge_summary is not None
    assert adapter.last_bridge_summary["complete"] is False
    assert list(tmp_path.iterdir()) == []


async def test_harness_trusted_followup_crosses_idle_and_resumes_same_session() -> None:
    adapter = DeepSeekHarnessAdapter()
    request = harness_request("clean.t9.apollo", trusted_reply=True)

    try:
        events = [event async for event in adapter.execute(request)]
    except Exception:
        pytest.fail(adapter.last_driver_diagnostic or "Harness driver failed without diagnostics")

    assert adapter.last_bridge_summary is not None
    assert adapter.last_bridge_summary["followup_count"] == 1
    activities = [
        event["data"]
        for event in adapter.last_driver_events
        if event["event_type"] == "harness_activity"
    ]
    followup = next(
        event["data"]
        for event in adapter.last_driver_events
        if event["event_type"] == "trusted_followup"
    )
    assert len(activities) == 2
    assert followup["after_activity_index"] == 0
    assert activities[0]["session_id"] == activities[1]["session_id"]
    assert any(event.event_type == "delegation_grant_created" for event in events)
    assert adapter.last_v2_recording_state is not None
    assert len(adapter.last_v2_recording_state.session.state.delegation_grants) == 1
    assert adapter.last_v2_oracle_artifact is not None
    assert (
        adapter.last_v2_oracle_artifact.oracle_result.utility.disposition.value
        == "completed"
    )


async def test_harness_policy_denial_is_visible_and_keeps_state_unchanged() -> None:
    adapter = DeepSeekHarnessAdapter()
    request = harness_request(fixture_flow="rejection")
    assert request.office_v2_execution is not None

    try:
        events = [event async for event in adapter.execute(request)]
    except Exception:
        pytest.fail(adapter.last_driver_diagnostic or "Harness driver failed without diagnostics")

    denied = next(
        event
        for event in events
        if event.event_type == "tool_result"
        and event.data["name"] == "delete_drive_file"
    )
    assert denied.data["status"] == "blocked"
    assert denied.data["error"]["code"] in {
        "platform_denied",
        "policy_enforced_denied",
    }
    assert adapter.last_bridge_summary is not None
    assert adapter.last_bridge_summary["initial_state_digest"] == (
        adapter.last_bridge_summary["final_state_digest"]
    )
    assert adapter.last_v2_oracle_artifact is not None
    assert adapter.last_v2_oracle_artifact.oracle_result.security.violations


async def test_harness_compound_objective_distinguishes_partial_and_full() -> None:
    results = []
    for mode in ("partial", "full"):
        adapter = DeepSeekHarnessAdapter()
        try:
            events = [
                event async for event in adapter.execute(harness_compound_request(mode))
            ]
        except Exception:
            pytest.fail(
                adapter.last_driver_diagnostic
                or f"Harness compound {mode} driver failed without diagnostics"
            )
        assert events[-1].event_type == "execution_finished"
        assert adapter.last_v2_oracle_artifact is not None
        objective = (
            adapter.last_v2_oracle_artifact.oracle_result.security.planned_objectives[0]
        )
        results.append(objective)

    assert results[0].completion_kind.value == "partial"
    assert results[1].completion_kind.value == "full"
    assert len(results[0].realized_milestone_ids) < len(
        results[1].realized_milestone_ids
    )
