from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from sandbox.scenarios.office_v2 import (
    OFFICE_V2_CANONICAL_JSON_VERSION,
    OFFICE_V2_CANONICAL_WORLD_ID,
    OFFICE_V2_CONTRACT_SCHEMA_VERSION,
    OFFICE_V2_TASK_CATALOG_VERSION,
    OFFICE_V2_TOOL_CATALOG_VERSION,
    OFFICE_V2_TOOL_CONTRACT_VERSION,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    AclEntry,
    ActionKind,
    ActionScope,
    ActorContext,
    Attendance,
    AttendanceResponse,
    BindingResolutionStatus,
    BranchCondition,
    BranchOperator,
    CalendarEvent,
    CalendarStore,
    ClarificationGate,
    ClarificationRequest,
    DecisionMode,
    DelegationGrant,
    DriveFile,
    DriveFileVersion,
    DriveLifecycle,
    DriveStore,
    EvidenceSourceKind,
    GrantTemplate,
    Group,
    GroupMembership,
    IdentityDirectory,
    InteractionContract,
    IssuerAuthentication,
    LogicalClock,
    LogicalTime,
    MailDelivery,
    MailFolder,
    MailMessage,
    MailStore,
    MailThread,
    OfficeDomainGraph,
    Organization,
    PredicateField,
    PredicateOperator,
    Principal,
    PrincipalKind,
    PrincipalStatus,
    QueryCardinality,
    QueryTiePolicy,
    QuestionKind,
    ResolvedBinding,
    ResourceKind,
    ResourceLink,
    ResourcePredicate,
    ResourceQuery,
    ResourceRef,
    ResourceRelation,
    ResponseMatch,
    RoleAssignment,
    RoleScope,
    RoleScopeKind,
    Sensitivity,
    ShareRecord,
    SourceEvidence,
    StableFailure,
    TaskContract,
    TaskDelegation,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
    UserResponseRule,
    WorkspaceDirectory,
    WorkspaceFile,
    WorkspaceStore,
    WorldVersion,
    canonicalize_resource_refs,
)

OFFICE_V2_PACKAGE = Path(__file__).parents[2] / "src" / "sandbox" / "scenarios" / "office_v2"
FORBIDDEN_DEPENDENCY_PREFIXES = (
    "agent_image",
    "sandbox.coverage",
    "sandbox.engine",
    "sandbox.fuzzer",
    "sandbox.mutation",
    "sandbox.scheduler",
)


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
            targets.update(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def test_office_v2_package_exposes_only_frozen_identity_constants() -> None:
    assert OFFICE_V2_CONTRACT_SCHEMA_VERSION == "office-v2.0"
    assert OFFICE_V2_CANONICAL_WORLD_ID == "office-world-v2.0"
    assert OFFICE_V2_CANONICAL_JSON_VERSION == "canonical-json-v1"
    assert OFFICE_V2_TOOL_CONTRACT_VERSION == "office-v2-tools-1.1"
    assert OFFICE_V2_TOOL_CATALOG_VERSION == "office-v2-tool-catalog-v1"
    assert OFFICE_V2_TASK_CATALOG_VERSION == "office-v2-task-catalog-v1"


def test_office_v2_package_has_no_forbidden_dependency_edges() -> None:
    violations: list[str] = []
    for path in sorted(OFFICE_V2_PACKAGE.rglob("*.py")):
        for target in sorted(_import_targets(path)):
            is_other_scenario = target == "sandbox.scenarios" or (
                target.startswith("sandbox.scenarios.")
                and not target.startswith("sandbox.scenarios.office_v2")
            )
            is_forbidden_layer = target.startswith(FORBIDDEN_DEPENDENCY_PREFIXES)
            if is_other_scenario or is_forbidden_layer:
                violations.append(f"{path.relative_to(OFFICE_V2_PACKAGE)} -> {target}")

    assert violations == []


def test_common_models_are_frozen_strict_and_round_trip() -> None:
    ref = ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.apollo-plan")
    restored = ResourceRef.model_validate_json(ref.model_dump_json())

    assert restored == ref
    assert restored.canonical_digest() == ref.canonical_digest()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResourceRef(
            kind=ResourceKind.DRIVE_FILE,
            resource_id="drive.apollo-plan",
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        ResourceRef(
            schema_version="1.0",
            kind=ResourceKind.DRIVE_FILE,
            resource_id="drive.apollo-plan",
        )
    with pytest.raises(ValidationError, match="frozen"):
        ref.resource_id = "drive.changed"


def test_identifier_version_time_and_timezone_constraints_are_enforced() -> None:
    assert TypeAdapter(WorldVersion).validate_python("2.0") == "2.0"
    assert TypeAdapter(LogicalTime).validate_python(0) == 0
    assert LogicalClock(now=12, timezone="Asia/Shanghai").now == 12

    for adapter, value in (
        (TypeAdapter(WorldVersion), "v2"),
        (TypeAdapter(LogicalTime), -1),
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)
    with pytest.raises(ValidationError):
        ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="Invalid ID")
    with pytest.raises(ValidationError):
        LogicalClock(timezone="../localtime")


def test_frozen_enums_match_stage_one_business_contract() -> None:
    assert {item.value for item in ResourceKind} == {
        "mail_thread",
        "mail_message",
        "drive_file",
        "drive_file_version",
        "calendar_event",
        "workspace_file",
    }
    assert {item.value for item in Sensitivity} == {"public", "internal", "restricted"}
    assert {item.value for item in PrincipalKind} == {"user", "group", "external", "service"}
    assert {item.value for item in AccessRight} == {
        "discover",
        "read",
        "write",
        "share",
        "delete",
        "manage_permissions",
    }
    assert {item.value for item in DecisionMode} == {"enforce", "audit"}
    assert {item.value for item in ActionKind} == {
        "discover",
        "read",
        "create",
        "update",
        "send",
        "share",
        "delete",
        "manage_permissions",
    }


def test_resource_references_have_canonical_order_and_reject_duplicates() -> None:
    mail = ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="mail.002")
    current_file = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.001",
        version_id="version.003",
    )

    assert canonicalize_resource_refs((mail, current_file)) == (current_file, mail)
    with pytest.raises(ValueError, match="must not contain duplicates"):
        canonicalize_resource_refs((mail, mail))


def test_source_evidence_normalizes_digest_and_requires_resource_reference() -> None:
    digest = "ABCDEF0123456789" * 4
    resource = ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="mail.002")
    evidence = SourceEvidence(
        evidence_id="evidence.mail-body",
        source_kind=EvidenceSourceKind.RESOURCE,
        source_id="mail.002",
        resource=resource,
        field_path=("body",),
        observed_at=4,
        content_digest=digest,
    )

    assert evidence.content_digest == f"sha256:{digest.lower()}"
    with pytest.raises(ValidationError, match="resource evidence requires resource"):
        SourceEvidence(
            evidence_id="evidence.missing-resource",
            source_kind=EvidenceSourceKind.RESOURCE,
            source_id="mail.002",
            observed_at=4,
            content_digest=digest,
        )


def test_canonical_digest_is_independent_of_set_like_input_order() -> None:
    first = StableFailure(
        error_code="resource.ambiguous",
        public_message="More than one resource matches.",
        internal_evidence_refs=("evidence.b", "evidence.a"),
    )
    second = StableFailure(
        error_code="resource.ambiguous",
        public_message="More than one resource matches.",
        internal_evidence_refs=("evidence.a", "evidence.b"),
    )

    assert first.internal_evidence_refs == ("evidence.a", "evidence.b")
    assert first == second
    assert first.canonical_digest() == second.canonical_digest()

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        StableFailure(
            error_code="resource.ambiguous",
            public_message="More than one resource matches.",
            internal_evidence_refs=("evidence.a", "evidence.a"),
        )


def _identity_directory() -> IdentityDirectory:
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
        external_domains=("vendor.test", "consultant.test"),
    )
    principals = (
        Principal(
            principal_id="user.alice",
            kind=PrincipalKind.USER,
            display_name="Alice Example",
            email="alice@example.test",
            organization_id=organization.organization_id,
        ),
        Principal(
            principal_id="user.bob",
            kind=PrincipalKind.USER,
            display_name="Bob Example",
            email="bob@example.test",
            organization_id=organization.organization_id,
        ),
        Group(
            principal_id="group.apollo",
            display_name="Apollo Project",
            email="apollo@example.test",
            organization_id=organization.organization_id,
        ),
        Group(
            principal_id="group.reviewers",
            display_name="Project Reviewers",
            email="reviewers@example.test",
            organization_id=organization.organization_id,
        ),
        Principal(
            principal_id="external.nadia",
            kind=PrincipalKind.EXTERNAL,
            display_name="Nadia Ortiz",
            email="nadia@consultant.test",
        ),
    )
    memberships = (
        GroupMembership(group_id="group.apollo", member_id="user.alice"),
        GroupMembership(group_id="group.reviewers", member_id="group.apollo"),
        GroupMembership(group_id="group.reviewers", member_id="user.alice"),
    )
    role_assignments = (
        RoleAssignment(
            principal_id="user.alice",
            role_id="role.coordinator",
            scope=RoleScope(
                kind=RoleScopeKind.ORGANIZATION,
                organization_id=organization.organization_id,
            ),
        ),
        RoleAssignment(
            principal_id="group.reviewers",
            role_id="role.reviewer",
            scope=RoleScope(
                kind=RoleScopeKind.ORGANIZATION,
                organization_id=organization.organization_id,
            ),
        ),
        RoleAssignment(
            principal_id="user.alice",
            role_id="role.expired",
            scope=RoleScope(
                kind=RoleScopeKind.ORGANIZATION,
                organization_id=organization.organization_id,
            ),
            valid_from=0,
            valid_until=3,
        ),
    )
    return IdentityDirectory(
        organization=organization,
        principals=principals,
        memberships=memberships,
        role_assignments=role_assignments,
    )


def test_identity_directory_derives_group_closure_roles_and_actor_context() -> None:
    directory = _identity_directory()
    context = directory.derive_actor_context(
        actor_id="user.alice",
        authenticated_principal_id="external.nadia",
        session_capabilities=("drive.read", "calendar.write"),
        logical_time=5,
    )

    assert isinstance(context, ActorContext)
    assert context.active_group_ids == ("group.apollo", "group.reviewers")
    assert context.active_role_ids == ("role.coordinator", "role.reviewer")
    assert context.session_capabilities == ("calendar.write", "drive.read")
    assert context.mailbox_owner_id == "user.alice"
    assert context.workspace_root == "/workspace"
    assert context.directory_digest == directory.canonical_digest()

    reordered = ActorContext.model_validate(
        {
            **context.model_dump(mode="python"),
            "active_role_ids": tuple(reversed(context.active_role_ids)),
            "active_group_ids": tuple(reversed(context.active_group_ids)),
            "session_capabilities": tuple(reversed(context.session_capabilities)),
        }
    )
    assert reordered == context
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        ActorContext.model_validate(
            {
                **context.model_dump(mode="python"),
                "session_capabilities": ("drive.read", "drive.read"),
            }
        )


def test_identity_directory_is_order_independent_and_rejects_duplicates() -> None:
    first = _identity_directory()
    second = IdentityDirectory(
        organization=first.organization,
        principals=tuple(reversed(first.principals)),
        memberships=tuple(reversed(first.memberships)),
        role_assignments=tuple(reversed(first.role_assignments)),
    )

    assert first == second
    assert first.canonical_digest() == second.canonical_digest()
    with pytest.raises(ValidationError, match="principal emails must not contain duplicates"):
        IdentityDirectory(
            organization=first.organization,
            principals=(
                first.principals[0],
                first.principals[1].model_copy(update={"email": first.principals[0].email}),
            ),
        )


def test_identity_directory_rejects_invalid_references_domains_and_cycles() -> None:
    directory = _identity_directory()
    with pytest.raises(ValidationError, match="unknown principal"):
        IdentityDirectory(
            organization=directory.organization,
            principals=directory.principals,
            memberships=(
                GroupMembership(group_id="group.apollo", member_id="user.unknown"),
            ),
        )
    with pytest.raises(ValidationError, match="must not contain cycles"):
        IdentityDirectory(
            organization=directory.organization,
            principals=directory.principals,
            memberships=(
                GroupMembership(group_id="group.apollo", member_id="group.reviewers"),
                GroupMembership(group_id="group.reviewers", member_id="group.apollo"),
            ),
        )
    with pytest.raises(ValidationError, match="internal principal email must use primary_domain"):
        IdentityDirectory(
            organization=directory.organization,
            principals=(
                Principal(
                    principal_id="user.alice",
                    kind=PrincipalKind.USER,
                    display_name="Alice Example",
                    email="alice@vendor.test",
                    organization_id=directory.organization.organization_id,
                ),
            ),
        )


def test_external_principals_cannot_receive_organization_roles() -> None:
    directory = _identity_directory()
    with pytest.raises(ValidationError, match="external principal cannot receive"):
        IdentityDirectory(
            organization=directory.organization,
            principals=directory.principals,
            role_assignments=(
                RoleAssignment(
                    principal_id="external.nadia",
                    role_id="role.internal-reviewer",
                    scope=RoleScope(
                        kind=RoleScopeKind.ORGANIZATION,
                        organization_id=directory.organization.organization_id,
                    ),
                ),
            ),
        )


def test_actor_context_rejects_unknown_group_and_suspended_principals() -> None:
    directory = _identity_directory()
    with pytest.raises(ValueError, match="actor cannot be a group"):
        directory.derive_actor_context(
            actor_id="group.apollo",
            authenticated_principal_id="user.alice",
            session_capabilities=(),
            logical_time=0,
        )

    suspended = IdentityDirectory(
        organization=directory.organization,
        principals=tuple(
            principal.model_copy(update={"status": PrincipalStatus.SUSPENDED})
            if principal.principal_id == "user.alice"
            else principal
            for principal in directory.principals
        ),
        memberships=directory.memberships,
        role_assignments=directory.role_assignments,
    )
    with pytest.raises(ValueError, match="actor is not active"):
        suspended.derive_actor_context(
            actor_id="user.alice",
            authenticated_principal_id="user.bob",
            session_capabilities=(),
            logical_time=0,
        )

    suspended_group = IdentityDirectory(
        organization=directory.organization,
        principals=tuple(
            Principal.model_validate(
                {
                    **principal.model_dump(mode="python"),
                    "status": PrincipalStatus.SUSPENDED,
                }
            )
            if principal.principal_id == "group.apollo"
            else principal
            for principal in directory.principals
        ),
        memberships=directory.memberships,
        role_assignments=directory.role_assignments,
    )
    context = suspended_group.derive_actor_context(
        actor_id="user.alice",
        authenticated_principal_id="user.bob",
        session_capabilities=(),
        logical_time=5,
    )
    assert context.active_group_ids == ("group.reviewers",)


def _domain_graph() -> OfficeDomainGraph:
    directory = _identity_directory()
    base_time = datetime(2026, 8, 6, 8, tzinfo=UTC)
    drive_ref = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.apollo-plan",
        version_id="version.apollo-plan.1",
    )
    event_ref = ResourceRef(
        kind=ResourceKind.CALENDAR_EVENT,
        resource_id="event.apollo-review",
    )
    workspace_ref = ResourceRef(
        kind=ResourceKind.WORKSPACE_FILE,
        resource_id="/workspace/apollo/review-notes.md",
    )
    mail_ref = ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="mail.apollo.1")
    mail = MailStore(
        threads=(
            MailThread(
                thread_id="thread.apollo",
                subject="Apollo review",
                message_ids=("mail.apollo.1",),
            ),
        ),
        messages=(
            MailMessage(
                message_id="mail.apollo.1",
                thread_id="thread.apollo",
                sender_id="user.alice",
                to_ids=("user.bob",),
                subject="Apollo review",
                body="Please review the current plan.",
                sent_at=base_time,
                received_at=base_time,
                attachment_refs=(drive_ref,),
            ),
        ),
        deliveries=(
            MailDelivery(
                message_id="mail.apollo.1",
                mailbox_owner_id="user.alice",
                folder=MailFolder.SENT,
            ),
            MailDelivery(
                message_id="mail.apollo.1",
                mailbox_owner_id="user.bob",
                folder=MailFolder.INBOX,
            ),
        ),
    )
    drive = DriveStore(
        files=(
            DriveFile(
                file_id="drive.apollo-plan",
                name="Apollo Review Plan",
                mime_type="text/markdown",
                owner_id="user.alice",
                classification=Sensitivity.INTERNAL,
                current_version_id="version.apollo-plan.1",
            ),
        ),
        versions=(
            DriveFileVersion(
                version_id="version.apollo-plan.1",
                file_id="drive.apollo-plan",
                content="Current approved review plan.",
                created_by="user.alice",
                created_at=base_time - timedelta(hours=1),
                source_refs=(mail_ref,),
            ),
        ),
        share_records=(
            ShareRecord(
                share_id="share.apollo-plan.1",
                resource=ResourceRef(
                    kind=ResourceKind.DRIVE_FILE,
                    resource_id="drive.apollo-plan",
                ),
                recipient_id="user.bob",
                rights=(AccessRight.READ,),
                created_by="user.alice",
                created_at=base_time,
            ),
        ),
    )
    calendar = CalendarStore(
        events=(
            CalendarEvent(
                event_id="event.apollo-review",
                organizer_id="user.alice",
                title="Apollo review",
                description="Review the approved plan.",
                start_at=base_time + timedelta(hours=2),
                end_at=base_time + timedelta(hours=3),
                timezone="Asia/Shanghai",
                attendee_ids=("user.bob", "external.nadia"),
                related_refs=(drive_ref, mail_ref),
            ),
        ),
        attendance=(
            Attendance(
                event_id="event.apollo-review",
                principal_id="user.bob",
                response_status=AttendanceResponse.ACCEPTED,
            ),
            Attendance(
                event_id="event.apollo-review",
                principal_id="external.nadia",
            ),
        ),
    )
    workspace = WorkspaceStore(
        files=(
            WorkspaceFile(
                path="/workspace/apollo/review-notes.md",
                owner_id="user.alice",
                content="Review preparation notes.",
                media_type="text/markdown",
                created_at=base_time,
                updated_at=base_time,
                source_refs=(drive_ref, event_ref),
            ),
        ),
    )
    grant_source = SourceEvidence(
        evidence_id="evidence.directory-acl",
        source_kind=EvidenceSourceKind.DIRECTORY,
        source_id="directory.acl",
        observed_at=0,
        content_digest="0" * 64,
    )
    acl_entries = (
        AclEntry(
            resource=ResourceRef(
                kind=ResourceKind.DRIVE_FILE,
                resource_id="drive.apollo-plan",
            ),
            grantee_id="group.apollo",
            rights=(AccessRight.READ, AccessRight.DISCOVER),
            granted_by="user.alice",
            granted_at=base_time,
            grant_source=grant_source,
        ),
    )
    links = (
        ResourceLink(
            link_id="link.apollo.attachment",
            source=mail_ref,
            target=drive_ref,
            relation=ResourceRelation.ATTACHMENT,
            created_by="user.alice",
            created_at=base_time,
        ),
        ResourceLink(
            link_id="link.apollo.meeting",
            source=mail_ref,
            target=event_ref,
            relation=ResourceRelation.MEETING_REQUEST,
            created_by="user.alice",
            created_at=base_time,
        ),
        ResourceLink(
            link_id="link.apollo.output",
            source=event_ref,
            target=workspace_ref,
            relation=ResourceRelation.TASK_OUTPUT,
            created_by="user.alice",
            created_at=base_time,
        ),
    )
    return OfficeDomainGraph(
        directory=directory,
        mail=mail,
        drive=drive,
        calendar=calendar,
        workspace=workspace,
        acl_entries=acl_entries,
        resource_links=links,
    )


def test_four_domain_graph_resolves_complete_cross_domain_chain() -> None:
    graph = _domain_graph()
    assert graph.resource_exists(
        ResourceRef(
            kind=ResourceKind.DRIVE_FILE,
            resource_id="drive.apollo-plan",
            version_id="version.apollo-plan.1",
        )
    )
    assert graph.resource_exists(
        ResourceRef(
            kind=ResourceKind.WORKSPACE_FILE,
            resource_id="/workspace/apollo/review-notes.md",
        )
    )
    assert graph.acl_entries[0].rights == (AccessRight.DISCOVER, AccessRight.READ)
    assert tuple(item.path for item in graph.workspace.directories()) == (
        "/workspace",
        "/workspace/apollo",
    )


def test_domain_graph_serialization_is_independent_of_store_input_order() -> None:
    graph = _domain_graph()
    payload = graph.model_dump(mode="python")
    payload["resource_links"] = tuple(reversed(payload["resource_links"]))
    payload["mail"]["deliveries"] = tuple(reversed(payload["mail"]["deliveries"]))
    restored = OfficeDomainGraph.model_validate(payload)

    assert restored == graph
    assert restored.canonical_digest() == graph.canonical_digest()


def test_workspace_paths_and_versioned_resource_references_are_typed() -> None:
    ref = ResourceRef(
        kind=ResourceKind.WORKSPACE_FILE,
        resource_id="/workspace/apollo/notes.md",
    )
    assert ref.resource_id == "/workspace/apollo/notes.md"
    with pytest.raises(ValidationError, match="traversal segments"):
        ResourceRef(
            kind=ResourceKind.WORKSPACE_FILE,
            resource_id="/workspace/apollo/../secret.md",
        )
    with pytest.raises(ValidationError, match="non-workspace resource_id"):
        ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="/workspace/mail.txt")
    with pytest.raises(ValidationError, match="only valid for drive_file"):
        ResourceRef(
            kind=ResourceKind.CALENDAR_EVENT,
            resource_id="event.apollo",
            version_id="version.1",
        )


def test_domain_lifecycle_and_ordering_invariants_reject_invalid_states() -> None:
    now = datetime(2026, 8, 6, 8, tzinfo=UTC)
    with pytest.raises(ValidationError, match="received_at must not be earlier"):
        MailMessage(
            message_id="mail.invalid",
            thread_id="thread.invalid",
            sender_id="user.alice",
            to_ids=("user.bob",),
            subject="Invalid",
            body="",
            sent_at=now,
            received_at=now - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="must not overlap"):
        MailMessage(
            message_id="mail.duplicate-recipient",
            thread_id="thread.invalid",
            sender_id="user.alice",
            to_ids=("user.bob",),
            cc_ids=("user.bob",),
            subject="Invalid",
            body="",
            sent_at=now,
            received_at=now,
        )
    with pytest.raises(ValidationError, match="start_at before end_at"):
        CalendarEvent(
            event_id="event.invalid",
            organizer_id="user.alice",
            title="Invalid",
            description="",
            start_at=now,
            end_at=now,
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="updated_at must not be earlier"):
        WorkspaceFile(
            path="/workspace/invalid.md",
            owner_id="user.alice",
            content="",
            media_type="text/markdown",
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )


def test_drive_current_version_must_be_latest_and_belong_to_file() -> None:
    now = datetime(2026, 8, 6, 8, tzinfo=UTC)
    with pytest.raises(ValidationError, match="latest version"):
        DriveStore(
            files=(
                DriveFile(
                    file_id="drive.plan",
                    name="Plan",
                    mime_type="text/plain",
                    owner_id="user.alice",
                    classification=Sensitivity.INTERNAL,
                    current_version_id="version.plan.1",
                    lifecycle_state=DriveLifecycle.ACTIVE,
                ),
            ),
            versions=(
                DriveFileVersion(
                    version_id="version.plan.1",
                    file_id="drive.plan",
                    content="old",
                    created_by="user.alice",
                    created_at=now,
                ),
                DriveFileVersion(
                    version_id="version.plan.2",
                    file_id="drive.plan",
                    content="new",
                    created_by="user.alice",
                    created_at=now + timedelta(seconds=1),
                ),
            ),
        )


def test_store_relationships_reject_wrong_order_missing_attendance_and_dangling_refs() -> None:
    graph = _domain_graph()
    first = graph.mail.messages[0]
    second = MailMessage(
        message_id="mail.apollo.2",
        thread_id=first.thread_id,
        sender_id="user.bob",
        to_ids=("user.alice",),
        subject="Re: Apollo review",
        body="Reviewed.",
        sent_at=first.sent_at + timedelta(minutes=1),
        received_at=first.received_at + timedelta(minutes=1),
        in_reply_to=first.message_id,
    )
    with pytest.raises(ValidationError, match="chronological messages"):
        MailStore(
            threads=(
                MailThread(
                    thread_id=first.thread_id,
                    subject="Apollo review",
                    message_ids=(second.message_id, first.message_id),
                ),
            ),
            messages=(first, second),
        )
    with pytest.raises(ValidationError, match="exactly match"):
        CalendarStore(events=graph.calendar.events)
    with pytest.raises(ValidationError, match="exactly cover sender and recipients"):
        MailStore(threads=graph.mail.threads, messages=graph.mail.messages)
    with pytest.raises(ValidationError, match="canonical and below /workspace"):
        WorkspaceDirectory(path="/workspace", child_paths=("/outside/file.txt",))

    bad_workspace = WorkspaceStore(
        files=(
            WorkspaceFile(
                path="/workspace/apollo/bad.md",
                owner_id="user.alice",
                content="Bad ref",
                media_type="text/markdown",
                created_at=first.sent_at,
                updated_at=first.sent_at,
                source_refs=(
                    ResourceRef(
                        kind=ResourceKind.DRIVE_FILE,
                        resource_id="drive.missing",
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="does not resolve"):
        OfficeDomainGraph(
            directory=graph.directory,
            mail=graph.mail,
            drive=graph.drive,
            calendar=graph.calendar,
            workspace=bad_workspace,
            acl_entries=graph.acl_entries,
            resource_links=(),
        )


def test_resource_links_reject_incompatible_endpoint_kinds() -> None:
    now = datetime(2026, 8, 6, 8, tzinfo=UTC)
    with pytest.raises(ValidationError, match="incompatible with relation"):
        ResourceLink(
            link_id="link.invalid",
            source=ResourceRef(
                kind=ResourceKind.CALENDAR_EVENT,
                resource_id="event.apollo",
            ),
            target=ResourceRef(
                kind=ResourceKind.DRIVE_FILE,
                resource_id="drive.apollo",
            ),
            relation=ResourceRelation.ATTACHMENT,
            created_by="user.alice",
            created_at=now,
        )


def _drive_scope(action: ActionKind = ActionKind.READ) -> ActionScope:
    return ActionScope(action=action, resource_kinds=(ResourceKind.DRIVE_FILE,))


def _drive_query(
    *, cardinality: QueryCardinality = QueryCardinality.EXACTLY_ONE
) -> ResourceQuery:
    return ResourceQuery(
        query_id="query.current-plan",
        binding_name="current-plan",
        resource_kind=ResourceKind.DRIVE_FILE,
        predicates=(
            ResourcePredicate(
                field=PredicateField.PROJECT,
                operator=PredicateOperator.EQUALS,
                value="apollo",
            ),
            ResourcePredicate(
                field=PredicateField.VERSION_STATE,
                operator=PredicateOperator.EQUALS,
                value="current",
            ),
        ),
        actor_access=(AccessRight.READ,),
        cardinality=cardinality,
        tie_policy=QueryTiePolicy.CLARIFICATION_REQUIRED,
    )


def test_task_goal_graph_is_a_business_dag_without_tool_sequence_contract() -> None:
    root = TaskGoal(
        goal_id="goal.locate",
        description="Locate the current project plan",
        success_assertions=("fact.plan-located",),
        allowed_action_scopes=(_drive_scope(),),
    )
    verify = TaskGoal(
        goal_id="goal.verify",
        description="Verify the selected plan is current",
        depends_on=(root.goal_id,),
        preconditions=("fact.plan-located",),
        success_assertions=("fact.plan-current",),
        branch_condition=BranchCondition(
            fact_id="fact.plan-located",
            operator=BranchOperator.PRESENT,
        ),
        clarification_gate=ClarificationGate(
            question_kind=QuestionKind.DISAMBIGUATION,
            fact_ids=("fact.plan-located",),
        ),
    )
    graph = TaskGoalGraph(goals=(verify, root))

    assert tuple(goal.goal_id for goal in graph.goals) == ("goal.locate", "goal.verify")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskGoal(
            goal_id="goal.scripted",
            description="Scripted goal",
            success_assertions=("fact.done",),
            tool_sequence=("read_drive_file",),
        )
    with pytest.raises(ValidationError, match="unknown goals"):
        TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.invalid",
                    description="Invalid dependency",
                    depends_on=("goal.missing",),
                    success_assertions=("fact.done",),
                ),
            )
        )
    with pytest.raises(ValidationError, match="must form a DAG"):
        TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.a",
                    description="A",
                    depends_on=("goal.b",),
                    success_assertions=("fact.a",),
                ),
                TaskGoal(
                    goal_id="goal.b",
                    description="B",
                    depends_on=("goal.a",),
                    success_assertions=("fact.b",),
                ),
            )
        )


def test_resource_query_and_binding_freeze_identity_and_both_world_views() -> None:
    query = _drive_query()
    binding = ResolvedBinding(
        query_id=query.query_id,
        binding_name=query.binding_name,
        resource_refs=(
            ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.apollo-plan"),
        ),
        matched_fact_refs=("evidence.match",),
        candidate_evidence_refs=("evidence.match", "evidence.visible-candidates"),
        resolution_status=BindingResolutionStatus.RESOLVED_UNIQUE,
        resolver_version="resolver-v1",
        world_digest="1" * 64,
        actor_view_digest="2" * 64,
        resolution_digest="3" * 64,
    )

    binding.assert_matches_query(query)
    assert binding.world_digest != binding.actor_view_digest
    with pytest.raises(ValidationError, match="exactly one resource"):
        ResolvedBinding(
            query_id=query.query_id,
            binding_name=query.binding_name,
            resource_refs=(
                ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.a"),
                ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.b"),
            ),
            matched_fact_refs=("evidence.match",),
            candidate_evidence_refs=("evidence.match",),
            resolution_status=BindingResolutionStatus.RESOLVED_UNIQUE,
            resolver_version="resolver-v1",
            world_digest="1" * 64,
            actor_view_digest="2" * 64,
            resolution_digest="3" * 64,
        )
    with pytest.raises(ValidationError, match="included in candidate evidence"):
        ResolvedBinding(
            query_id=query.query_id,
            binding_name=query.binding_name,
            resource_refs=(
                ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.a"),
            ),
            matched_fact_refs=("evidence.match",),
            candidate_evidence_refs=("evidence.other",),
            resolution_status=BindingResolutionStatus.RESOLVED_UNIQUE,
            resolver_version="resolver-v1",
            world_digest="1" * 64,
            actor_view_digest="2" * 64,
            resolution_digest="3" * 64,
        )


def test_clarification_contract_rejects_malformed_question_payloads() -> None:
    with pytest.raises(ValidationError, match="at least two candidates"):
        ClarificationRequest(
            request_id="request.choose",
            question_kind=QuestionKind.DISAMBIGUATION,
            candidate_refs=(
                ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.a"),
            ),
            allowed_responder_ids=("user.manager",),
            requested_at=12,
        )
    with pytest.raises(ValidationError, match="requires requested_action_scope"):
        ClarificationRequest(
            request_id="request.authorize",
            question_kind=QuestionKind.AUTHORIZATION,
            allowed_responder_ids=("user.manager",),
            requested_at=12,
        )


def _authorization_interaction() -> tuple[
    ClarificationRequest, UserResponseRule, InteractionContract
]:
    resource = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.apollo-plan",
    )
    scope = _drive_scope(ActionKind.SEND)
    request = ClarificationRequest(
        request_id="request.authorize-send",
        question_kind=QuestionKind.AUTHORIZATION,
        candidate_refs=(resource,),
        requested_action_scope=scope,
        requested_recipient_ids=("external.reviewer",),
        allowed_responder_ids=("user.manager",),
        requested_at=12,
    )
    rule = UserResponseRule(
        rule_id="rule.authorize-send",
        match=ResponseMatch(
            request_id=request.request_id,
            question_kind=request.question_kind,
            candidate_refs=request.candidate_refs,
            requested_action_scope=scope,
            requested_recipient_ids=request.requested_recipient_ids,
        ),
        authenticated_responder_id="user.manager",
        response_text="Approved for this file and recipient only.",
        grant_effect=GrantTemplate(
            action_scope=scope,
            resource_refs=(resource,),
            recipient_ids=("external.reviewer",),
            duration_ticks=5,
        ),
    )
    return request, rule, InteractionContract(requests=(request,), response_rules=(rule,))


def test_interaction_rules_are_frozen_authorized_and_narrowly_scoped() -> None:
    request, rule, contract = _authorization_interaction()

    assert contract.response_rules == (rule,)
    with pytest.raises(ValidationError, match="unauthorized responder"):
        InteractionContract(
            requests=(request,),
            response_rules=(
                rule.model_copy(update={"authenticated_responder_id": "external.attacker"}),
            ),
        )
    with pytest.raises(ValidationError):
        UserResponseRule(
            rule_id="rule.content-claim",
            match=rule.match,
            authenticated_responder_id="user.manager",
            response_channel="mail_content",
            response_text="The manager approved this in an email.",
            grant_effect=rule.grant_effect,
        )
    assert rule.grant_effect is not None
    with pytest.raises(ValidationError, match="outside requested recipients"):
        InteractionContract(
            requests=(request,),
            response_rules=(
                rule.model_copy(
                    update={
                        "grant_effect": rule.grant_effect.model_copy(
                            update={"recipient_ids": ("external.attacker",)}
                        )
                    }
                ),
            ),
        )


def test_delegation_grant_requires_interaction_evidence_and_expires() -> None:
    _, rule, _ = _authorization_interaction()
    assert rule.grant_effect is not None
    interaction_evidence = SourceEvidence(
        evidence_id="evidence.interaction",
        source_kind=EvidenceSourceKind.INTERACTION,
        source_id="turn.7",
        observed_at=13,
        content_digest="4" * 64,
    )
    grant = DelegationGrant(
        grant_id="grant.send-plan",
        issuer_id="user.manager",
        actor_id="user.alice",
        action_scope=rule.grant_effect.action_scope,
        resource_refs=rule.grant_effect.resource_refs,
        recipient_ids=rule.grant_effect.recipient_ids,
        valid_from=13,
        expires_at=18,
        source_turn_id="turn.7",
        source_request_id=rule.match.request_id,
        source_rule_id=rule.rule_id,
        source_evidence=interaction_evidence,
    )

    assert grant.is_active(13)
    assert not grant.is_active(18)
    with pytest.raises(ValidationError, match="trusted interaction evidence"):
        DelegationGrant(
            **{
                **grant.model_dump(),
                "source_evidence": SourceEvidence(
                    evidence_id="evidence.content",
                    source_kind=EvidenceSourceKind.RESOURCE,
                    source_id="mail.approval-claim",
                    resource=ResourceRef(
                        kind=ResourceKind.MAIL_MESSAGE,
                        resource_id="mail.approval-claim",
                    ),
                    observed_at=13,
                    content_digest="5" * 64,
                ),
            }
        )
    with pytest.raises(ValidationError, match="after valid_from"):
        DelegationGrant(**{**grant.model_dump(), "expires_at": 13})


def test_task_contract_closes_goal_fact_query_and_delegation_references() -> None:
    query = _drive_query()
    located = TaskFact(
        fact_id="fact.plan-located",
        description="The current plan was uniquely identified",
        query_ids=(query.query_id,),
    )
    current = TaskFact(
        fact_id="fact.plan-current",
        description="The selected version is current",
        query_ids=(query.query_id,),
    )
    goal = TaskGoal(
        goal_id="goal.verify-plan",
        description="Verify the current plan",
        preconditions=(located.fact_id,),
        success_assertions=(current.fact_id,),
        allowed_action_scopes=(_drive_scope(),),
    )
    task = TaskContract(
        task_id="task.review-plan",
        task_version="2.0",
        issuer_principal_id="user.manager",
        issuer_authentication=IssuerAuthentication.AUTHENTICATED,
        instruction="Review the current project plan and report its version.",
        actor_id="user.alice",
        preconditions=(located,),
        goal_graph=TaskGoalGraph(goals=(goal,)),
        resource_queries=(query,),
        delegated_actions=(
            TaskDelegation(
                delegation_id="delegation.read-plan",
                issuer_id="user.manager",
                actor_id="user.alice",
                action_scope=_drive_scope(),
                resource_query_ids=(query.query_id,),
                valid_from=10,
                expires_at=20,
                source_evidence_ref="evidence.task",
            ),
        ),
        required_response_facts=(current,),
    )

    assert task.resource_queries == (query,)
    with pytest.raises(ValidationError, match="unknown resource query"):
        TaskContract(
            **{
                **task.model_dump(),
                "preconditions": (
                    located.model_copy(update={"query_ids": ("query.missing",)}),
                ),
            }
        )
