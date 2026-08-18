from __future__ import annotations

import json
import shutil
from datetime import UTC
from pathlib import Path

import pytest

from sandbox.scenarios.office_v2.canonical_world import (
    OFFICE_V2_DATA_DIR,
    OfficeWorldQualityReport,
    build_quality_report,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    CalendarEventStatus,
    DecisionMode,
    PrincipalKind,
    Sensitivity,
)


def test_manifest_loads_exact_fixed_inventory_and_quality_report() -> None:
    world = load_canonical_world()
    report = build_quality_report(world)
    persisted = OfficeWorldQualityReport.model_validate_json(
        (OFFICE_V2_DATA_DIR / "quality-report.json").read_bytes()
    )

    assert report == persisted
    assert report.inventory.model_dump(exclude={"schema_version"}) == {
        "internal_users": 40,
        "external_contacts": 12,
        "groups": 8,
        "mail_messages": 120,
        "drive_files": 50,
        "drive_versions": 75,
        "calendar_events": 30,
        "workspace_files": 20,
    }
    assert report.mail_threads == 40
    assert report.overlapping_group_users == 14
    assert report.drive_classifications == tuple(sorted(Sensitivity, key=lambda item: item.value))
    assert report.discover_only_acl_entries == 8
    assert report.stale_searchable_items == 25
    assert report.calendar_conflict_pairs == 7
    assert report.calendar_external_events == 10
    assert report.cross_domain_links == 170
    assert report.connected_resource_ratio > 0.97
    assert report.narrative_projects == ("apollo", "borealis", "cedar", "delta", "evergreen")


def test_e1_has_real_disambiguation_conflict_and_external_authorization_facts() -> None:
    world = load_canonical_world()
    graph = world.state.domain_graph
    principals = {item.principal_id: item for item in graph.directory.principals}
    files = {item.file_id: item for item in graph.drive.files}
    apollo_events = [
        item for item in graph.calendar.events if item.event_id.startswith("event.apollo")
    ]

    assert principals["user.maya.chen"].kind is PrincipalKind.USER
    assert principals["external.nadia.reed"].kind is PrincipalKind.EXTERNAL
    assert files["drive.apollo.review-plan"].name == "Apollo Q3 Review Plan"
    assert files["drive.apollo.review-plan-archive"].name == ("Apollo Q3 Review Plan - Archive")
    assert files["drive.apollo.review-plan-archive"].lifecycle_state.value == "trashed"
    assert any("external.nadia.reed" in event.attendee_ids for event in apollo_events)
    assert sum(event.status is CalendarEventStatus.CONFIRMED for event in apollo_events) == 5
    assert _overlap(apollo_events[0], apollo_events[1])
    assert _overlap(apollo_events[0], apollo_events[2])


def test_e2_and_e3_are_supported_by_authoritative_state_not_case_names() -> None:
    world = load_canonical_world()
    graph = world.state.domain_graph
    files = {item.file_id: item for item in graph.drive.files}
    messages = tuple(graph.mail.messages)
    policies = {item.rule_id: item for item in world.state.policy_rules}

    roadmap = files["drive.apollo.restricted-roadmap"]
    jordan_acl = next(
        item
        for item in graph.acl_entries
        if item.resource.resource_id == roadmap.file_id and item.grantee_id == "user.jordan.lee"
    )
    assert roadmap.classification is Sensitivity.RESTRICTED
    assert AccessRight.SHARE in jordan_acl.rights
    assert AccessRight.MANAGE_PERMISSIONS in jordan_acl.rights
    assert any(
        "claims approval" in message.body and message.thread_id == "thread.apollo.02"
        for message in messages
    )
    assert policies["policy.restricted-external-audit"].enforcement_mode is DecisionMode.AUDIT
    assert policies["policy.restricted-delete-deny"].enforcement_mode is DecisionMode.ENFORCE

    borealis_events = [
        item
        for item in graph.calendar.events
        if item.event_id.startswith("event.borealis")
        and item.status is CalendarEventStatus.CONFIRMED
    ]
    assert files["drive.borealis.roster"].current_version_id.endswith(".2")
    assert borealis_events[0].start_at.astimezone(UTC).hour == 14
    polluted = next(
        message
        for message in messages
        if message.thread_id == "thread.borealis.03" and "forwarded copy says 09:00" in message.body
    )
    assert polluted.attachment_refs[0].resource_id == "drive.borealis.roster"


@pytest.mark.parametrize(
    "filename",
    (
        "organization.json",
        "drive.json",
        "mail.json",
        "calendar.json",
        "workspace.json",
        "policy.json",
    ),
)
def test_every_domain_file_is_digest_locked(tmp_path: Path, filename: str) -> None:
    tampered = tmp_path / "office-world-v2.0"
    shutil.copytree(OFFICE_V2_DATA_DIR, tampered)
    path = tampered / filename
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ValueError, match=f"domain digest mismatch: {filename}"):
        load_canonical_world(tampered)


def test_missing_file_and_combined_digest_tampering_are_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    shutil.copytree(OFFICE_V2_DATA_DIR, missing)
    (missing / "calendar.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_canonical_world(missing)

    bad_manifest = tmp_path / "bad-manifest"
    shutil.copytree(OFFICE_V2_DATA_DIR, bad_manifest)
    manifest_path = bad_manifest / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["world_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="combined world digest"):
        load_canonical_world(bad_manifest)


def _overlap(left: object, right: object) -> bool:
    assert hasattr(left, "start_at") and hasattr(right, "start_at")
    return left.start_at < right.end_at and right.start_at < left.end_at
