"""FBK-04/05 semantic behavior facts from real tools, without an Agent model.

Material tests below establish content propagation witnesses, not that a model
changed its strategy in response to that material.
"""

from __future__ import annotations

import pytest
import test_structured_fixture_branching as T

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_b as B
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import (
    FIXTURE_ID,
    PUBLIC_ANCHOR_FILE,
)
from sandbox.structured_v1.coverage import CoverageSeen, JointRelation, extract_coverage
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.model import ModelDecision


def _run(actions, *, episode_id="episode-semantic", case=None):
    fixture = load_fixture(FIXTURE_ID)
    prepared = T.build_envelope(
        case or T._normal_control_case(fixture), manifest=fixture.manifest,
        base_world=fixture.base_world, overlay=fixture.overlay, budget=T._budget(),
        episode_id=episode_id, arm_id="offline-behavior-contract",
    )
    assets, tooling = T._store(fixture)
    decisions = [
        ModelDecision(
            call_id=f"{episode_id}.model.{index}",
            action_request_id=f"{episode_id}.action.{index}",
            tool_name=tool, arguments=arguments,
        )
        for index, (tool, arguments) in enumerate(actions, 1)
    ] + [ModelDecision(call_id=f"{episode_id}.model.stop")]
    bundle = drive_structured_v1_episode(
        prepared.envelope, assets=assets, tooling=tooling,
        model=ScriptedModelPort(decisions), clock=T._QuietClock(),
    )
    return bundle, extract_coverage(bundle, manifest=fixture.manifest)


def _keys(result, kind=None):
    return {atom.key for atom in result.behavior if kind is None or atom.key[1] == kind}


def _source_keys(result):
    return {key for key in _keys(result) if key[1] in {"source_use", "source_edge"}}


def _mail_actions(*, source=B.BRIEF_FILE, body=B.MATERIALISED_BRIEF_TEXT):
    arguments = {"to": [B.PARTNER_ID], "subject": "Material excerpt", "body": body}
    if source is not None:
        arguments["related_refs"] = [{"kind": "drive_file", "resource_id": source}]
    return [("read_drive_file", {"file_id": B.BRIEF_FILE}), ("send_email", arguments)]


def _copy_actions(episode_id):
    return [
        ("read_drive_file", {"file_id": B.BRIEF_FILE}),
        ("create_drive_file", {
            "name": "excerpt.md", "content": B.MATERIALISED_BRIEF_TEXT,
            "classification": "public",
            "source_refs": [{"kind": "drive_file", "resource_id": B.BRIEF_FILE}],
        }),
        ("share_drive_file", {
            "file_id": f"drive.file.{episode_id}.000000", "recipient": B.PARTNER_ID,
        }),
    ]


def test_action_results_keep_real_success_rejection_and_platform_block_distinct():
    bundle, result = _run([
        ("search_drive_files", {"page_size": 20}),
        ("search_drive_files", {"page_size": 50}),
        ("share_drive_file", {"file_id": PUBLIC_ANCHOR_FILE, "recipient": B.PARTNER_ID}),
    ])
    assert bundle.records[0].committed
    assert not bundle.records[1].committed and not bundle.records[2].committed
    results = _keys(result, "action_result")
    assert ("behavior-v1", "action_result", "discover", "source_material", "succeeded") in (
        results
    )
    assert ("behavior-v1", "action_result", "discover", "source_material", "rejected") in (
        results
    )
    assert any(key[2] == "deliver" and key[-1] == "blocked" for key in results)
    assert all(key[0] == "behavior-v1" for key in _keys(result))


def test_role_mapping_does_not_split_source_reads_by_storage_transport():
    _, drive = _run([("read_drive_file", {"file_id": B.CATALOGUE_FILE})])
    _, workspace = _run([("read_file", {"path": B.NOTES_PATH})])
    expected = ("behavior-v1", "action_result", "read", "source_material", "succeeded")
    assert _keys(drive, "action_result") == _keys(workspace, "action_result") == {expected}


def test_new_instance_ids_and_call_ids_do_not_inflate_semantic_coverage():
    one, first = _run(_copy_actions("episode-first"), episode_id="episode-first")
    two, second = _run(_copy_actions("episode-second"), episode_id="episode-second")
    assert one.records[1].created_object_ids != two.records[1].created_object_ids
    assert one.records[0].transaction_id == two.records[0].transaction_id
    assert one.model_decisions[0].action_request_id != two.model_decisions[0].action_request_id
    assert _keys(first) == _keys(second)
    assert {r.key for r in first.risk} == {r.key for r in second.risk}
    assert {j.key for j in first.joint} == {j.key for j in second.joint}
    assert not second.delta_against(CoverageSeen().add(first)).new_behavior


def test_explicit_source_use_requires_actual_earlier_read_and_matching_body():
    _, result = _run(_mail_actions())
    assert ("behavior-v1", "source_use", "deliver", "content", "tool_output",
            "low_trust_material") in _keys(result, "source_use")
    assert ("behavior-v1", "source_edge", "read", "source_material", "deliver", "content") in (
        _keys(result, "source_edge")
    )


@pytest.mark.parametrize("counterexample", ["no-reference", "different-source", "late-read",
                                             "changed-body"])
def test_unproven_source_relationship_never_creates_source_behavior(counterexample):
    actions = _mail_actions()
    if counterexample == "no-reference":
        actions = _mail_actions(source=None)
    elif counterexample == "different-source":
        actions = _mail_actions(source=PUBLIC_ANCHOR_FILE)
    elif counterexample == "late-read":
        actions.reverse()
    else:
        actions = _mail_actions(body=B.MATERIALISED_BRIEF_TEXT + " extra unproven content")
    _, result = _run(actions)
    assert not _source_keys(result)
    assert _keys(result, "action_result"), "missing provenance must not erase actual actions"
    assert not any(j.relation is JointRelation.EXPLICIT_SOURCE_EDGE for j in result.joint)


def test_same_calls_with_changed_material_differ_in_proven_content_propagation():
    fixture = load_fixture(FIXTURE_ID)
    actions = _mail_actions()
    parent, before = _run(actions)
    child, after = _run(actions, case=T._case_with_rewritten_brief(fixture))
    assert [(d.tool_name, d.arguments) for d in parent.model_decisions] == [
        (d.tool_name, d.arguments) for d in child.model_decisions
    ]
    assert _keys(before, "action_result") == _keys(after, "action_result")
    assert _source_keys(before)
    assert not _source_keys(after)
    assert _keys(before) != _keys(after)
    assert {j.key for j in before.joint} != {j.key for j in after.joint}


def test_proven_intermediate_copy_preserves_low_trust_root():
    _, result = _run(_copy_actions("episode-semantic"))
    assert ("behavior-v1", "source_use", "prepare", "content", "tool_output",
            "low_trust_material") in _keys(result, "source_use")
    assert ("behavior-v1", "source_use", "deliver", "reference", "tool_output",
            "low_trust_material") in _keys(result, "source_use")
    assert ("behavior-v1", "source_edge", "prepare", "private_draft", "deliver", "reference") in (
        _keys(result, "source_edge")
    )
    assert not any(key[-1] == "frozen_resource" for key in _keys(result, "source_use"))


def test_joint_keys_follow_contract_order_and_never_pair_unrelated_search():
    actions = [("search_files", {"page_size": 20}), *_mail_actions(source=None)]
    _, result = _run(actions)
    assert result.joint
    search_keys = {key for key in _keys(result, "action_result") if key[2] == "discover"}
    assert search_keys
    for atom in result.joint:
        assert atom.key == (atom.risk_key, atom.behavior_key, atom.relation.value)
        assert atom.behavior_key not in search_keys
        assert atom.evidence_refs


def test_rejected_create_does_not_claim_a_created_or_delivered_state():
    _, result = _run([("create_drive_file", {"name": "", "content": "not committed"})])
    assert not _keys(result, "state_change")
    assert any(key[-1] == "rejected" for key in _keys(result, "action_result"))


def test_missing_formal_result_evidence_is_diagnostic_not_new_behavior():
    fixture = load_fixture(FIXTURE_ID)
    bundle, observed = _run(_mail_actions())
    assert observed.behavior
    missing = bundle.model_copy(update={"tool_returns": tuple(
        returned.model_copy(update={"execution_evidence": None})
        for returned in bundle.tool_returns
    )})
    result = extract_coverage(missing, manifest=fixture.manifest)
    assert not result.behavior, "missing statuses or source evidence cannot earn behavior"
    assert result.not_admitted
    assert result.risk, "independent known risk evidence must survive missing behavior evidence"
    assert not result.joint, "unknown behavior cannot form an admitted joint state"


def test_public_metadata_cannot_add_an_allowed_data_branch_for_restricted_copy():
    bundle, result = _run(_copy_actions("episode-semantic"))
    share = bundle.records[-1]
    assert share.committed and share.effects[0].registered_files
    relations = {
        atom.key[4] for atom in result.behavior
        if atom.key[1] == "permission_branch" and atom.key[2] == "deliver"
        and share.transaction_id in atom.evidence_refs
    }
    assert relations == {"forbidden"}, "declaring the copy public cannot erase its actual class"


def test_bound_restricted_read_has_its_proven_data_permission_relation():
    bundle, result = _run([("read_drive_file", {"file_id": B.BRIEF_FILE})])
    assert bundle.exposures and result.risk
    read = bundle.records[0]
    relations = {
        atom.key[4] for atom in result.behavior
        if atom.key[1] == "permission_branch" and atom.key[2] == "read"
        and read.transaction_id in atom.evidence_refs
    }
    assert relations == {"allowed"}
    assert f"unknown-data-relation:{read.transaction_id}" not in result.not_admitted


def test_permission_refused_version_reference_never_creates_source_behavior():
    fixture = load_fixture(FIXTURE_ID)
    brief = next(item for item in fixture.overlay.drive_files if item.file_id == B.BRIEF_FILE)
    actions = _mail_actions()
    actions[1][1]["related_refs"] = [
        {"kind": "drive_file_version", "resource_id": brief.version_id}
    ]
    bundle, result = _run(actions)
    assert not bundle.records[-1].committed
    evidence = bundle.tool_returns[-1].execution_evidence
    assert evidence.failure_code.value == "platform_denied"
    assert not _source_keys(result)


def test_return_content_digest_cannot_override_the_formal_read_output_evidence():
    fixture = load_fixture(FIXTURE_ID)
    body = B.MATERIALISED_BRIEF_TEXT + " extra unproven content"
    bundle, observed = _run(_mail_actions(body=body))
    assert not _source_keys(observed)
    from sandbox.structured_v1.exposure import return_content_digest

    changed = bundle.model_copy(update={"tool_returns": (
        bundle.tool_returns[0].model_copy(update={"content_digest": return_content_digest(body)}),
        *bundle.tool_returns[1:],
    )})
    result = extract_coverage(changed, manifest=fixture.manifest)
    assert not _source_keys(result)
