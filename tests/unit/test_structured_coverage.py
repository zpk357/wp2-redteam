"""T04 coverage extraction and scope separation."""

from __future__ import annotations

from sandbox.structured_v1.coverage import (
    CoverageDelta,
    CoverageLedger,
    CoverageSeen,
    extract_coverage,
)


def _bundle(manifest, root_case):
    # The behavioral projection now requires official tool-result evidence. Drive
    # the real Office runtime instead of a scripted ToolCallReport without status.
    import test_structured_episode_rehearsal as rehearsal

    from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
    from sandbox.structured_v1.model import ModelDecision

    envelope, assets, _, _ = rehearsal._prepared(manifest, root_case)
    return drive_structured_v1_episode(
        envelope, assets=assets, tooling=rehearsal._tooling_facts(),
        clock=rehearsal.FakeClock([0, 1, 2, 60, 120]),
        model=ScriptedModelPort([
            ModelDecision(call_id="m-read", action_request_id="a-read",
                          tool_name="read_drive_file", arguments={"file_id": "f-overview"}),
            ModelDecision(call_id="m-send", action_request_id="a-send", tool_name="send_email",
                          arguments={"to": ["partner.contact"], "subject": "offline specimen",
                                     "body": manifest.registered_units[0].value}),
            ModelDecision(call_id="m-stop"),
        ]),
    )


def test_coverage_is_recomputed_from_bundle_and_is_semantically_stable(
    manifest, root_case
) -> None:
    bundle = _bundle(manifest, root_case)
    first = extract_coverage(bundle, manifest=manifest)
    second = extract_coverage(bundle, manifest=manifest)

    assert first == second
    assert first.behavior
    assert first.risk
    assert first.joint
    assert first.findings
    assert all(item.evidence_refs for item in first.behavior)
    assert all(item.evidence_refs for item in first.risk)
    assert all(item.evidence_refs for item in first.joint)


def test_local_and_global_seen_are_separate_and_global_keys_do_not_include_fixture(
    manifest, root_case
) -> None:
    result = extract_coverage(_bundle(manifest, root_case), manifest=manifest)
    ledger = CoverageLedger()

    ledger, first = ledger.apply(result)
    ledger, second = ledger.apply(result)

    assert first.local_total.behavior_count > 0
    assert first.local_total.risk_count > 0
    assert first.local_total.joint_count > 0
    # Both scopes are reported, and a repeat opportunity adds nothing in either (`SOC-FBK-06`).
    assert second.local_total == CoverageDelta()
    assert second.global_delta == CoverageDelta()
    scopes = [key for key in ledger.local if key.startswith(f"{manifest.fixture_id}|")]
    assert scopes
    local_union = frozenset().union(*(ledger.local[key].behavior for key in scopes))
    assert ledger.global_seen.behavior == local_union


def test_delta_keeps_behavior_risk_joint_and_no_new_cases_distinct(
    manifest, root_case
) -> None:
    result = extract_coverage(_bundle(manifest, root_case), manifest=manifest)
    behavior_keys = frozenset(item.key for item in result.behavior)
    risk_keys = frozenset(item.key for item in result.risk)
    joint_keys = frozenset(item.key for item in result.joint)
    only_behavior = result.delta_against(CoverageSeen(risk=risk_keys, joint=joint_keys))
    only_risk = result.delta_against(CoverageSeen(behavior=behavior_keys, joint=joint_keys))
    only_joint = result.delta_against(CoverageSeen(behavior=behavior_keys, risk=risk_keys))
    no_new = result.delta_against(
        CoverageSeen(behavior=behavior_keys, risk=risk_keys, joint=joint_keys)
    )

    assert (only_behavior.behavior_count, only_behavior.risk_count,
            only_behavior.joint_count) == (len(behavior_keys), 0, 0)
    assert (only_risk.behavior_count, only_risk.risk_count,
            only_risk.joint_count) == (0, len(risk_keys), 0)
    assert (only_joint.behavior_count, only_joint.risk_count,
            only_joint.joint_count) == (0, 0, len(joint_keys))
    assert (no_new.behavior_count, no_new.risk_count, no_new.joint_count) == (0, 0, 0)


def test_evidence_ids_do_not_change_semantic_keys(manifest, root_case) -> None:
    bundle = _bundle(manifest, root_case)
    result = extract_coverage(bundle, manifest=manifest)
    altered = tuple(
        item.model_copy(update={"evidence_refs": (f"different.{index}",)})
        for index, item in enumerate(result.behavior)
    )
    assert {item.key for item in altered} == {item.key for item in result.behavior}
