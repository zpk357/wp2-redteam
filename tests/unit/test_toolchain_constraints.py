"""Tests for the recoverable constraint tables.

The negative cases are the point of this file.  A loader that quietly returns
empty tables when the data is absent would report "no constraints" and let a
later evaluator judge every run clean.  So an absent table must show up in
``missing``, a malformed row must show up in ``malformed``, and ``complete``
must be false in both cases.
"""

from __future__ import annotations

from sandbox.toolchain.constraints import (
    ACL_KEY,
    POLICY_KEY,
    PROTECTED_FACTS_KEY,
    load_world_constraints,
)


def _grant(grantee: str, resource_id: str, rights: list[str], version: str | None = None) -> dict:
    return {
        "grantee_id": grantee,
        "granted_by": "user.marcus.brown",
        "resource": {
            "kind": "drive_file",
            "resource_id": resource_id,
            "version_id": version,
            "schema_version": "office-v2.0",
        },
        "rights": rights,
        "schema_version": "office-v2.0",
    }


def _policy(rule_id: str, action: str, kinds: list[str], sensitivities: list[str]) -> dict:
    return {
        "rule_id": rule_id,
        "effect": "deny",
        "enforcement_mode": "enforce",
        "action_scopes": [
            {"action": action, "resource_kinds": kinds, "schema_version": "office-v2.0"}
        ],
        "recipient_kinds": [],
        "sensitivities": sensitivities,
        "schema_version": "office-v2.0",
    }


def _world_state(grants: list, policies: list) -> dict:
    return {
        "domain_graph": {"acl_entries": grants},
        "policy_rules": policies,
        "logical_clock": 1000,
    }


def test_both_tables_are_recovered():
    world = _world_state(
        grants=[
            _grant("group.apollo", "drive.apollo.decision-log", ["discover", "read"]),
            _grant("external.nadia.reed", "drive.apollo.decision-log", ["discover"]),
        ],
        policies=[
            _policy("policy.restricted-delete-deny", "delete", ["drive_file"], ["restricted"])
        ],
    )
    constraints = load_world_constraints(world)

    assert constraints.has_audience_table
    assert constraints.has_policy_table
    assert constraints.distinct_resources == ("drive.apollo.decision-log",)
    assert constraints.distinct_grantees == ("external.nadia.reed", "group.apollo")
    assert constraints.missing == (PROTECTED_FACTS_KEY,)
    assert constraints.malformed == ()


def test_rights_are_deduplicated_and_sorted():
    constraints = load_world_constraints(
        _world_state(
            grants=[_grant("user.a", "drive.x", ["read", "read", "discover"])],
            policies=[_policy("p1", "send", ["drive_file"], ["internal"])],
        )
    )
    assert constraints.grants[0].rights == ("discover", "read")


def test_version_is_kept_when_present():
    constraints = load_world_constraints(
        _world_state(
            grants=[_grant("user.a", "drive.x", ["read"], version="v3")],
            policies=[_policy("p1", "send", ["drive_file"], ["internal"])],
        )
    )
    assert constraints.grants[0].version_id == "v3"


def test_absent_tables_are_reported_not_silently_empty():
    constraints = load_world_constraints({"logical_clock": 1})
    assert not constraints.has_audience_table
    assert not constraints.has_policy_table
    assert not constraints.complete
    assert ACL_KEY in constraints.missing
    assert POLICY_KEY in constraints.missing


def test_empty_table_is_missing_not_present():
    constraints = load_world_constraints(_world_state(grants=[], policies=[]))
    assert not constraints.has_audience_table
    assert ACL_KEY in constraints.missing


def test_malformed_rows_are_counted_and_block_completeness():
    constraints = load_world_constraints(
        _world_state(
            grants=[
                _grant("user.a", "drive.x", ["read"]),
                {"rights": ["read"]},
                "not-a-row",
            ],
            policies=[_policy("p1", "send", ["drive_file"], ["internal"])],
        )
    )
    assert len(constraints.grants) == 1
    assert len(constraints.malformed) == 2
    assert not constraints.complete


def test_missing_grantee_is_malformed():
    constraints = load_world_constraints(
        _world_state(
            grants=[{"resource": {"kind": "drive_file", "resource_id": "drive.x"}}],
            policies=[_policy("p1", "send", ["drive_file"], ["internal"])],
        )
    )
    assert constraints.grants == ()
    assert any("no grantee_id" in note for note in constraints.malformed)


def test_policy_scopes_are_unioned():
    world = _world_state(
        grants=[_grant("user.a", "drive.x", ["read"])],
        policies=[
            {
                "rule_id": "policy.multi",
                "effect": "deny",
                "enforcement_mode": "audit",
                "action_scopes": [
                    {"action": "send", "resource_kinds": ["drive_file"]},
                    {"action": "share", "resource_kinds": ["drive_file"]},
                ],
                "recipient_kinds": ["external"],
                "sensitivities": ["internal"],
            }
        ],
    )
    rule = load_world_constraints(world).policies[0]
    assert rule.actions == ("send", "share")
    assert rule.resource_kinds == ("drive_file",)
    assert rule.recipient_kinds == ("external",)


def test_none_world_state_is_not_an_error():
    constraints = load_world_constraints(None)
    assert not constraints.complete
    assert set(constraints.missing) == {ACL_KEY, POLICY_KEY, PROTECTED_FACTS_KEY}


def test_summary_exposes_the_gap_for_reporting():
    summary = load_world_constraints(_world_state(grants=[], policies=[])).summary()
    assert summary["complete"] is False
    assert PROTECTED_FACTS_KEY in summary["missing"]
