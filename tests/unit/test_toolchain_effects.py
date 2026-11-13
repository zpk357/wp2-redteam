"""Tests for the raw-effect extractor.

The extractor's contract is narrow on purpose: it reads ``trace.steps`` and
nothing else.  Two tests defend that boundary.

* A step whose ``committed`` is null must produce no effect.  A blocked attack
  and a successful one differ by exactly this field, so an extractor that counts
  attempted calls would erase the distinction the whole project rests on.
* A delivery is recognised by the shape of its committed record, not by tool
  name.  The renamed-tool case below is the proof.
"""

from __future__ import annotations

from sandbox.toolchain.effects import ChangeKind, extract_effects


def _step(
    tool: str,
    *,
    sequence: int,
    committed=None,
    arguments=None,
) -> dict:
    return {
        "committed": committed,
        "invocation": {
            "tool_name": tool,
            "sequence": sequence,
            "arguments": arguments or {},
            "argument_sources": [],
        },
        "request": {"accepted": True},
        "result": {},
        "schema_version": "office-v2.0",
    }


def _delivery_record(audience: list[str], payload: str, classification: str = "internal") -> dict:
    return {"audience": audience, "payload": payload, "classification": classification}


def test_read_only_steps_produce_no_effect():
    trace = {"steps": [_step("read_file", sequence=0), _step("search_docs", sequence=1)]}
    effects = extract_effects(trace)
    assert not effects.any_effect
    assert effects.deliveries == ()
    assert effects.changes == ()


def test_blocked_step_with_null_committed_produces_no_effect():
    trace = {"steps": [_step("send_email", sequence=0, committed=None)]}
    assert not extract_effects(trace).any_effect


def test_committed_record_with_audience_and_payload_is_a_delivery():
    trace = {
        "steps": [
            _step(
                "send_email",
                sequence=3,
                committed=_delivery_record(["partner.contact"], "brief body"),
            )
        ]
    }
    effects = extract_effects(trace)
    assert len(effects.deliveries) == 1
    delivery = effects.deliveries[0]
    assert delivery.recipients == ("partner.contact",)
    assert delivery.payload == "brief body"
    assert delivery.classification == "internal"
    assert effects.changes == ()


def test_a_renamed_delivery_verb_is_still_a_delivery():
    """Verb family, not tool name: a new hand-off tool needs no table edit."""

    trace = {
        "steps": [
            _step("forward_brief", sequence=0, committed=_delivery_record(["user.a"], "x"))
        ]
    }
    assert len(extract_effects(trace).deliveries) == 1


def test_creating_a_file_with_an_empty_audience_is_not_a_delivery():
    """Measured case: `create_drive_file` commits audience and payload but hands nothing over."""

    trace = {
        "steps": [_step("create_drive_file", sequence=0, committed=_delivery_record([], "body"))]
    }
    effects = extract_effects(trace)
    assert effects.deliveries == ()
    assert [change.kind for change in effects.changes] == [ChangeKind.MUTATION]


def test_a_permission_change_carrying_an_audience_is_not_a_delivery():
    """Measured case: `update_drive_permissions` writes an audience nobody was sent anything by."""

    trace = {
        "steps": [
            _step(
                "update_drive_permissions",
                sequence=0,
                committed=_delivery_record(["group.operations", "user.a"], "body"),
            )
        ]
    }
    effects = extract_effects(trace)
    assert effects.deliveries == ()
    assert [change.kind for change in effects.changes] == [ChangeKind.MUTATION]


def test_a_hand_off_verb_with_no_recipient_is_not_a_delivery():
    trace = {"steps": [_step("send_email", sequence=0, committed=_delivery_record([], "body"))]}
    assert extract_effects(trace).deliveries == ()


def test_disposal_and_mutation_are_classified_by_verb():
    trace = {
        "steps": [
            _step("delete_drive_file", sequence=0, committed={"id": "x"}),
            _step("cancel_calendar_event", sequence=1, committed={"id": "y"}),
            _step("update_drive_permissions", sequence=2, committed={"id": "z"}),
            _step("create_drive_file", sequence=3, committed={"id": "w"}),
            _step("inspect_ledger", sequence=4, committed={"id": "q"}),
        ]
    }
    kinds = [change.kind for change in extract_effects(trace).changes]
    assert kinds == [
        ChangeKind.DISPOSAL,
        ChangeKind.DISPOSAL,
        ChangeKind.MUTATION,
        ChangeKind.MUTATION,
        ChangeKind.OTHER,
    ]


def test_events_are_ordered_by_sequence_not_by_list_position():
    trace = {
        "steps": [
            _step("send_email", sequence=5, committed=_delivery_record(["b"], "second")),
            _step("send_email", sequence=2, committed=_delivery_record(["a"], "first")),
        ]
    }
    effects = extract_effects(trace)
    assert [item.sequence for item in effects.deliveries] == [2, 5]
    assert effects.deliveries[0].payload == "first"


def test_object_reference_is_taken_from_arguments():
    trace = {
        "steps": [
            _step(
                "share_drive_file",
                sequence=0,
                committed=_delivery_record(["user.a"], "body"),
                arguments={"file_id": "drive.123", "recipient": "user.a"},
            )
        ]
    }
    assert extract_effects(trace).deliveries[0].object_ref == "drive.123"


def test_recipient_set_is_deduplicated_and_sorted():
    trace = {
        "steps": [
            _step("send_email", sequence=0, committed=_delivery_record(["b", "a"], "1")),
            _step("send_email", sequence=1, committed=_delivery_record(["a"], "2")),
        ]
    }
    assert extract_effects(trace).recipient_set() == ("a", "b")


def test_missing_or_malformed_trace_is_empty_not_an_error():
    assert extract_effects(None).deliveries == ()
    assert extract_effects({}).deliveries == ()
    assert extract_effects({"steps": "nope"}).deliveries == ()
    assert extract_effects({"steps": [None, 42]}).deliveries == ()
