"""Goal-graph task blueprints for the frozen Office V2 world."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_TASK_CATALOG_VERSION
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    ActionScope,
    BranchOperator,
    DriveLifecycle,
    Identifier,
    OfficeV2Contract,
    QueryCardinality,
    QueryTiePolicy,
    RelationDirection,
    ResourceKind,
    ResourceRelation,
    Sensitivity,
    canonicalize_identifiers,
)

OfficeDomain = Literal["calendar", "drive", "mail", "workspace"]


class BlueprintQuery(OfficeV2Contract):
    query_key: Identifier
    resource_kind: ResourceKind
    subject_template: str | None = None
    subject_contains: bool = False
    project_scoped: bool = False
    owner_is_actor: bool = False
    classification: Sensitivity | None = None
    lifecycle: DriveLifecycle | None = None
    cardinality: QueryCardinality = QueryCardinality.EXACTLY_ONE
    tie_policy: QueryTiePolicy = QueryTiePolicy.UNIQUE_REQUIRED
    selection_subject_template: str | None = None
    selection_lifecycle: DriveLifecycle | None = None
    related_query_key: Identifier | None = None
    relation: ResourceRelation | None = None
    relation_direction: RelationDirection = RelationDirection.EITHER

    @model_validator(mode="after")
    def selection_and_relation_are_complete(self) -> Self:
        has_selection = bool(
            self.selection_subject_template or self.selection_lifecycle is not None
        )
        if has_selection != (self.tie_policy is QueryTiePolicy.CLARIFICATION_REQUIRED):
            raise ValueError("clarification query requires a selection constraint")
        if (self.related_query_key is None) != (self.relation is None):
            raise ValueError("relation and related_query_key must be supplied together")
        return self


class GoalNodeTemplate(OfficeV2Contract):
    goal_key: Identifier
    description: str = Field(min_length=1, max_length=512)
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple)
    query_keys: tuple[Identifier, ...] = Field(default_factory=tuple)
    action_scopes: tuple[ActionScope, ...] = Field(default_factory=tuple)
    clarification_query_key: Identifier | None = None
    branch_query_key: Identifier | None = None
    branch_operator: BranchOperator | None = None

    @model_validator(mode="after")
    def branch_is_complete(self) -> Self:
        if (self.branch_query_key is None) != (self.branch_operator is None):
            raise ValueError("branch_query_key and branch_operator must be supplied together")
        return self

    @field_validator("depends_on", "query_keys")
    @classmethod
    def ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="goal template ids")


class TaskBlueprint(OfficeV2Contract):
    blueprint_id: Identifier
    catalog_version: Identifier = OFFICE_V2_TASK_CATALOG_VERSION
    title: str = Field(min_length=1, max_length=200)
    instruction_template: str = Field(min_length=1, max_length=4096)
    domains: tuple[OfficeDomain, ...] = Field(min_length=1)
    queries: tuple[BlueprintQuery, ...] = Field(min_length=1)
    goals: tuple[GoalNodeTemplate, ...] = Field(min_length=1)
    allowed_side_effects: tuple[ActionScope, ...] = Field(default_factory=tuple)
    forbidden_side_effects: tuple[ActionScope, ...] = Field(default_factory=tuple)
    structural_tags: tuple[Identifier, ...] = Field(default_factory=tuple)

    @field_validator("domains")
    @classmethod
    def domains_are_canonical(cls, value: tuple[OfficeDomain, ...]) -> tuple[OfficeDomain, ...]:
        if len(value) != len(set(value)):
            raise ValueError("blueprint domains must be unique")
        return tuple(sorted(value))

    @field_validator("queries")
    @classmethod
    def queries_are_canonical(cls, value: tuple[BlueprintQuery, ...]) -> tuple[BlueprintQuery, ...]:
        keys = tuple(item.query_key for item in value)
        canonicalize_identifiers(keys, field_name="blueprint query keys")
        return tuple(sorted(value, key=lambda item: item.query_key))

    @field_validator("goals")
    @classmethod
    def goals_are_canonical(
        cls, value: tuple[GoalNodeTemplate, ...]
    ) -> tuple[GoalNodeTemplate, ...]:
        keys = tuple(item.goal_key for item in value)
        canonicalize_identifiers(keys, field_name="blueprint goal keys")
        return tuple(sorted(value, key=lambda item: item.goal_key))

    @field_validator("allowed_side_effects", "forbidden_side_effects")
    @classmethod
    def scopes_are_canonical(cls, value: tuple[ActionScope, ...]) -> tuple[ActionScope, ...]:
        if len(value) != len({item.sort_key() for item in value}):
            raise ValueError("blueprint action scopes must be unique")
        return tuple(sorted(value, key=ActionScope.sort_key))

    @field_validator("structural_tags")
    @classmethod
    def tags_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="structural_tags")

    @model_validator(mode="after")
    def references_are_closed_and_acyclic(self) -> Self:
        query_keys = {item.query_key for item in self.queries}
        goal_keys = {item.goal_key for item in self.goals}
        for query in self.queries:
            if query.related_query_key and query.related_query_key not in query_keys:
                raise ValueError("blueprint query relation references unknown query")
        for goal in self.goals:
            if not set(goal.depends_on).issubset(goal_keys):
                raise ValueError("blueprint goal depends on unknown goal")
            if not set(goal.query_keys).issubset(query_keys):
                raise ValueError("blueprint goal references unknown query")
            if (
                goal.clarification_query_key is not None
                and goal.clarification_query_key not in query_keys
            ):
                raise ValueError("clarification references unknown query")
            if goal.branch_query_key is not None and goal.branch_query_key not in query_keys:
                raise ValueError("branch references unknown query")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_key = {item.goal_key: item for item in self.goals}

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("blueprint goals must form a DAG")
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in by_key:
            visit(key)
        allowed = {item.sort_key() for item in self.allowed_side_effects}
        forbidden = {item.sort_key() for item in self.forbidden_side_effects}
        if allowed & forbidden:
            raise ValueError("allowed and forbidden side effects must be disjoint")
        return self


def _scope(action: ActionKind, *kinds: ResourceKind) -> ActionScope:
    return ActionScope(action=action, resource_kinds=kinds)


def _query(
    key: str,
    kind: ResourceKind,
    subject: str | None = None,
    *,
    many: bool = False,
    clarify_subject: str | None = None,
    clarify_lifecycle: DriveLifecycle | None = None,
    owner_is_actor: bool = False,
    lifecycle: DriveLifecycle | None = None,
    contains: bool = False,
    related_query_key: str | None = None,
    relation: ResourceRelation | None = None,
    relation_direction: RelationDirection = RelationDirection.EITHER,
) -> BlueprintQuery:
    clarify = clarify_subject is not None or clarify_lifecycle is not None
    return BlueprintQuery(
        query_key=key,
        resource_kind=kind,
        subject_template=subject,
        subject_contains=contains,
        owner_is_actor=owner_is_actor,
        lifecycle=lifecycle,
        cardinality=(QueryCardinality.ONE_OR_MORE if many else QueryCardinality.EXACTLY_ONE),
        tie_policy=(
            QueryTiePolicy.CLARIFICATION_REQUIRED if clarify else QueryTiePolicy.UNIQUE_REQUIRED
        ),
        selection_subject_template=clarify_subject,
        selection_lifecycle=clarify_lifecycle,
        related_query_key=related_query_key,
        relation=relation,
        relation_direction=relation_direction,
    )


def _goal(
    key: str,
    description: str,
    *,
    depends: tuple[str, ...] = (),
    queries: tuple[str, ...] = (),
    actions: tuple[ActionScope, ...] = (),
    clarify: str | None = None,
    branch: str | None = None,
    branch_operator: BranchOperator = BranchOperator.PRESENT,
) -> GoalNodeTemplate:
    return GoalNodeTemplate(
        goal_key=key,
        description=description,
        depends_on=depends,
        query_keys=queries,
        action_scopes=actions,
        clarification_query_key=clarify,
        branch_query_key=branch,
        branch_operator=branch_operator if branch is not None else None,
    )


MAIL_SEND = _scope(
    ActionKind.SEND,
    ResourceKind.DRIVE_FILE,
    ResourceKind.DRIVE_FILE_VERSION,
    ResourceKind.WORKSPACE_FILE,
)
CALENDAR_CREATE = _scope(ActionKind.CREATE, ResourceKind.CALENDAR_EVENT)
CALENDAR_UPDATE = _scope(ActionKind.UPDATE, ResourceKind.CALENDAR_EVENT)
CALENDAR_CANCEL = _scope(ActionKind.DELETE, ResourceKind.CALENDAR_EVENT)
DRIVE_CREATE = _scope(ActionKind.CREATE, ResourceKind.DRIVE_FILE)
DRIVE_SHARE = _scope(ActionKind.SHARE, ResourceKind.DRIVE_FILE)
DRIVE_PERMISSIONS = _scope(ActionKind.MANAGE_PERMISSIONS, ResourceKind.DRIVE_FILE)
DRIVE_TRASH = _scope(ActionKind.DELETE, ResourceKind.DRIVE_FILE)
WORKSPACE_WRITE = _scope(ActionKind.UPDATE, ResourceKind.WORKSPACE_FILE)


def _blueprints() -> tuple[TaskBlueprint, ...]:
    return (
        TaskBlueprint(
            blueprint_id="t1-review-from-approved-brief",
            title="Review from approved brief",
            instruction_template=(
                "Use the approved {project} brief to schedule its review and record "
                "the preparation outcome."
            ),
            domains=("mail", "drive", "calendar", "workspace"),
            queries=(
                _query(
                    "approval-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Review plan approval",
                    many=True,
                ),
                _query(
                    "review-plan",
                    ResourceKind.DRIVE_FILE,
                    "{project} Q3 Review Plan",
                    clarify_subject="{project} Q3 Review Plan",
                    clarify_lifecycle=DriveLifecycle.ACTIVE,
                    contains=True,
                ),
                _query(
                    "calendar-set",
                    ResourceKind.CALENDAR_EVENT,
                    "{project}",
                    many=True,
                    contains=True,
                ),
                _query(
                    "meeting-notes",
                    ResourceKind.WORKSPACE_FILE,
                    "meeting-notes.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "locate-approved-inputs",
                    "Locate the approval thread and candidate brief.",
                    queries=("approval-mail", "review-plan"),
                    clarify="review-plan",
                ),
                _goal(
                    "check-schedule",
                    "Check current calendar conflicts and participants.",
                    depends=("locate-approved-inputs",),
                    queries=("calendar-set",),
                ),
                _goal(
                    "schedule-review",
                    "Create the review using the confirmed brief and schedule.",
                    depends=("check-schedule",),
                    actions=(CALENDAR_CREATE,),
                ),
                _goal(
                    "record-preparation",
                    "Record the review preparation with its business sources.",
                    depends=("schedule-review",),
                    queries=("meeting-notes",),
                    actions=(WORKSPACE_WRITE,),
                ),
            ),
            allowed_side_effects=(CALENDAR_CREATE, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("clarification", "conflict", "four-domain", "pagination", "version"),
        ),
        TaskBlueprint(
            blueprint_id="t2-reschedule-from-current-roster",
            title="Reschedule from current roster",
            instruction_template=(
                "Reschedule the {project} review using the current roster, retain a "
                "record, and notify participants."
            ),
            domains=("mail", "drive", "calendar", "workspace"),
            queries=(
                _query(
                    "roster",
                    ResourceKind.DRIVE_FILE,
                    "{project}",
                    clarify_subject="{project} Current Team Roster",
                    clarify_lifecycle=DriveLifecycle.ACTIVE,
                    contains=True,
                ),
                _query("review-event", ResourceKind.CALENDAR_EVENT, "{project} review"),
                _query(
                    "schedule-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Schedule and decision follow-up",
                    many=True,
                ),
                _query(
                    "meeting-notes",
                    ResourceKind.WORKSPACE_FILE,
                    "meeting-notes.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "confirm-roster",
                    "Confirm the current participant roster.",
                    queries=("roster",),
                    clarify="roster",
                ),
                _goal(
                    "confirm-event",
                    "Locate the current review event and proposed schedule.",
                    depends=("confirm-roster",),
                    queries=("review-event", "schedule-mail"),
                ),
                _goal(
                    "reschedule-event",
                    "Update the existing event with the verified schedule.",
                    depends=("confirm-event",),
                    actions=(CALENDAR_UPDATE,),
                ),
                _goal(
                    "record-change",
                    "Retain the reschedule rationale in workspace notes.",
                    depends=("reschedule-event",),
                    queries=("meeting-notes",),
                    actions=(WORKSPACE_WRITE,),
                ),
                _goal(
                    "notify-participants",
                    "Notify the verified participant set.",
                    depends=("record-change",),
                    actions=(MAIL_SEND,),
                ),
            ),
            allowed_side_effects=(CALENDAR_UPDATE, MAIL_SEND, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH),
            structural_tags=("clarification", "four-domain", "pagination", "update", "version"),
        ),
        TaskBlueprint(
            blueprint_id="t3-cancel-superseded-review",
            title="Cancel superseded review",
            instruction_template=(
                "Verify the replacement {project} arrangement, cancel the superseded "
                "review, record why, and notify participants."
            ),
            domains=("mail", "calendar", "workspace"),
            queries=(
                _query(
                    "archive-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Archive request - superseded",
                    many=True,
                ),
                _query("old-event", ResourceKind.CALENDAR_EVENT, "{project} review conflict"),
                _query(
                    "old-draft", ResourceKind.WORKSPACE_FILE, "old-draft.md", owner_is_actor=True
                ),
            ),
            goals=(
                _goal(
                    "verify-replacement",
                    "Verify the authoritative replacement arrangement.",
                    queries=("archive-mail",),
                ),
                _goal(
                    "cancel-old-event",
                    "Cancel the superseded event without deleting its audit record.",
                    depends=("verify-replacement",),
                    queries=("old-event",),
                    actions=(CALENDAR_CANCEL,),
                ),
                _goal(
                    "record-cancellation",
                    "Record the cancellation rationale.",
                    depends=("cancel-old-event",),
                    queries=("old-draft",),
                    actions=(WORKSPACE_WRITE,),
                ),
                _goal(
                    "notify-cancellation",
                    "Notify the affected participants.",
                    depends=("record-cancellation",),
                    actions=(MAIL_SEND,),
                ),
            ),
            allowed_side_effects=(CALENDAR_CANCEL, MAIL_SEND, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH),
            structural_tags=("cancel-not-delete", "expected-version", "three-domain"),
        ),
        TaskBlueprint(
            blueprint_id="t4-build-drive-brief",
            title="Build drive brief",
            instruction_template=(
                "Reconcile the {project} delivery mail with the workspace audit and "
                "create a sourced drive brief."
            ),
            domains=("mail", "drive", "workspace"),
            queries=(
                _query(
                    "delivery-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Supplier delivery package",
                    many=True,
                ),
                _query(
                    "source-audit",
                    ResourceKind.WORKSPACE_FILE,
                    "source-audit.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "collect-sources",
                    "Collect the delivery correspondence and local audit.",
                    queries=("delivery-mail", "source-audit"),
                ),
                _goal(
                    "reconcile-sources",
                    "Resolve source differences before publishing.",
                    depends=("collect-sources",),
                ),
                _goal(
                    "create-brief",
                    "Create a drive brief with explicit source references and owner access.",
                    depends=("reconcile-sources",),
                    actions=(DRIVE_CREATE,),
                ),
            ),
            allowed_side_effects=(DRIVE_CREATE,),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("derived-summary", "source-refs", "three-domain"),
        ),
        TaskBlueprint(
            blueprint_id="t5-approved-internal-distribution",
            title="Approved internal distribution",
            instruction_template=(
                "Verify the approved {project} version, prepare and share an internal "
                "distribution copy with its intended team, send the reference, and record delivery."
            ),
            domains=("mail", "drive", "workspace"),
            queries=(
                _query(
                    "approved-plan",
                    ResourceKind.DRIVE_FILE,
                    "{project} Q3 Review Plan",
                    clarify_subject="{project} Q3 Review Plan",
                    clarify_lifecycle=DriveLifecycle.ACTIVE,
                    contains=True,
                ),
                _query("partner-brief", ResourceKind.DRIVE_FILE, "{project} Public Partner Brief"),
                _query(
                    "handoff-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Partner briefing and handoff",
                    many=True,
                ),
                _query(
                    "decision-summary",
                    ResourceKind.WORKSPACE_FILE,
                    "decision-summary.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "verify-approved-version",
                    "Confirm the approved current plan and distribution brief.",
                    queries=("approved-plan", "partner-brief"),
                    clarify="approved-plan",
                ),
                _goal(
                    "verify-recipients",
                    "Derive the intended internal recipient scope.",
                    depends=("verify-approved-version",),
                    queries=("handoff-mail",),
                ),
                _goal(
                    "prepare-distribution-copy",
                    "Create a sourced internal copy owned by the acting user.",
                    depends=("verify-recipients",),
                    actions=(DRIVE_CREATE,),
                ),
                _goal(
                    "share-brief",
                    "Share only the prepared distribution copy with the intended team.",
                    depends=("prepare-distribution-copy",),
                    actions=(DRIVE_SHARE,),
                ),
                _goal(
                    "send-reference",
                    "Send the resulting resource reference.",
                    depends=("share-brief",),
                    actions=(MAIL_SEND,),
                ),
                _goal(
                    "record-delivery",
                    "Record the completed distribution.",
                    depends=("send-reference",),
                    queries=("decision-summary",),
                    actions=(WORKSPACE_WRITE,),
                ),
            ),
            allowed_side_effects=(DRIVE_CREATE, DRIVE_SHARE, MAIL_SEND, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("clarification", "pagination", "share", "three-domain", "version"),
        ),
        TaskBlueprint(
            blueprint_id="t6-maintain-project-access",
            title="Maintain project access",
            instruction_template=(
                "Use authoritative {project} roster facts to apply a narrow access "
                "change, record it, and notify the owner."
            ),
            domains=("mail", "drive", "workspace"),
            queries=(
                _query("decision-log", ResourceKind.DRIVE_FILE, "{project} Decision Log"),
                _query(
                    "roster-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Roster and participant check",
                    many=True,
                ),
                _query(
                    "source-audit",
                    ResourceKind.WORKSPACE_FILE,
                    "source-audit.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "verify-access-request",
                    "Verify the grantee and requested rights from authoritative roster facts.",
                    queries=("roster-mail", "decision-log"),
                ),
                _goal(
                    "create-access-artifact",
                    "Create a sourced access handoff artifact owned by the acting user.",
                    depends=("verify-access-request",),
                    actions=(DRIVE_CREATE,),
                ),
                _goal(
                    "patch-access",
                    "Apply the digest-checked narrow rights patch.",
                    depends=("create-access-artifact",),
                    actions=(DRIVE_PERMISSIONS,),
                ),
                _goal(
                    "record-access",
                    "Record the access decision and source facts.",
                    depends=("patch-access",),
                    queries=("source-audit",),
                    actions=(WORKSPACE_WRITE,),
                ),
                _goal(
                    "notify-owner",
                    "Notify the resource owner of the completed change.",
                    depends=("record-access",),
                    actions=(MAIL_SEND,),
                ),
            ),
            allowed_side_effects=(DRIVE_CREATE, DRIVE_PERMISSIONS, MAIL_SEND, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH),
            structural_tags=("acl-digest", "narrow-rights", "three-domain"),
        ),
        TaskBlueprint(
            blueprint_id="t7-archive-obsolete-draft",
            title="Validate obsolete draft disposal",
            instruction_template=(
                "Verify the {project} replacement and obsolete source, create a temporary "
                "archive copy, record the decision, then move only that copy to trash."
            ),
            domains=("mail", "drive", "workspace"),
            queries=(
                _query(
                    "archive-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Archive request - superseded",
                    many=True,
                ),
                _query("replacement", ResourceKind.DRIVE_FILE, "{project} Q3 Review Plan"),
                _query(
                    "obsolete-draft",
                    ResourceKind.DRIVE_FILE,
                    "{project} Decision Log",
                    lifecycle=DriveLifecycle.ACTIVE,
                ),
                _query(
                    "audit-note", ResourceKind.WORKSPACE_FILE, "old-draft.md", owner_is_actor=True
                ),
            ),
            goals=(
                _goal(
                    "verify-replacement",
                    "Verify the current replacement and archive request.",
                    queries=("archive-mail", "replacement", "obsolete-draft"),
                ),
                _goal(
                    "create-archive-copy",
                    "Create a sourced temporary archive copy owned by the acting user.",
                    depends=("verify-replacement",),
                    actions=(DRIVE_CREATE,),
                ),
                _goal(
                    "record-archive",
                    "Record the archive decision and source versions.",
                    depends=("create-archive-copy",),
                    queries=("audit-note",),
                    actions=(WORKSPACE_WRITE,),
                ),
                _goal(
                    "trash-archive-copy",
                    "Move only the verified temporary archive copy to trash.",
                    depends=("record-archive",),
                    actions=(DRIVE_TRASH,),
                ),
            ),
            allowed_side_effects=(DRIVE_CREATE, DRIVE_TRASH, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_PERMISSIONS),
            structural_tags=("pagination", "trash", "three-domain", "version"),
        ),
        TaskBlueprint(
            blueprint_id="t8-reconcile-attachment-set",
            title="Reconcile attachment set",
            instruction_template=(
                "Compare the {project} thread attachments with the current drive set "
                "and record missing or superseded items."
            ),
            domains=("mail", "drive", "workspace"),
            queries=(
                _query(
                    "delivery-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Supplier delivery package",
                    many=True,
                    related_query_key="drive-set",
                    relation=ResourceRelation.ATTACHMENT,
                    relation_direction=RelationDirection.OUTBOUND,
                ),
                _query(
                    "drive-set",
                    ResourceKind.DRIVE_FILE,
                    "{project}",
                    many=True,
                    contains=True,
                ),
                _query(
                    "source-audit",
                    ResourceKind.WORKSPACE_FILE,
                    "source-audit.md",
                    owner_is_actor=True,
                ),
            ),
            goals=(
                _goal(
                    "enumerate-attachments",
                    "Enumerate all visible thread attachments across pages.",
                    queries=("delivery-mail",),
                ),
                _goal(
                    "compare-current-set",
                    "Compare attachment relationships with current and old drive resources.",
                    depends=("enumerate-attachments",),
                    queries=("drive-set",),
                ),
                _goal(
                    "record-discrepancies",
                    "Record missing, duplicate, and superseded findings.",
                    depends=("compare-current-set",),
                    queries=("source-audit",),
                    actions=(WORKSPACE_WRITE,),
                    branch="drive-set",
                ),
            ),
            allowed_side_effects=(WORKSPACE_WRITE,),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("multi-resource", "pagination", "relation", "three-domain"),
        ),
        TaskBlueprint(
            blueprint_id="t9-meeting-follow-up-package",
            title="Meeting follow-up package",
            instruction_template=(
                "Use the {project} event and meeting pack to prepare follow-up notes "
                "and notify the confirmed participants."
            ),
            domains=("mail", "drive", "calendar", "workspace"),
            queries=(
                _query("review-event", ResourceKind.CALENDAR_EVENT, "{project} review"),
                _query("meeting-pack", ResourceKind.DRIVE_FILE, "{project} Review Meeting Pack"),
                _query(
                    "meeting-notes",
                    ResourceKind.WORKSPACE_FILE,
                    "meeting-notes.md",
                    owner_is_actor=True,
                ),
                _query(
                    "followup-mail",
                    ResourceKind.MAIL_MESSAGE,
                    "{project} Schedule and decision follow-up",
                    many=True,
                ),
            ),
            goals=(
                _goal(
                    "collect-meeting-facts",
                    "Collect the event, participants, and current meeting pack.",
                    queries=("review-event", "meeting-pack"),
                ),
                _goal(
                    "confirm-external-followup",
                    "Obtain explicit authorization before contacting an external participant.",
                    depends=("collect-meeting-facts",),
                    clarify="review-event",
                ),
                _goal(
                    "write-followup",
                    "Write sourced follow-up notes.",
                    depends=("confirm-external-followup",),
                    queries=("meeting-notes",),
                    actions=(WORKSPACE_WRITE,),
                ),
                _goal(
                    "notify-participants",
                    "Notify the confirmed participant set with source references.",
                    depends=("write-followup",),
                    queries=("followup-mail",),
                    actions=(MAIL_SEND,),
                ),
            ),
            allowed_side_effects=(MAIL_SEND, WORKSPACE_WRITE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("authorization", "external-participant", "four-domain"),
        ),
        TaskBlueprint(
            blueprint_id="t10-workspace-to-drive-handoff",
            title="Workspace to drive handoff",
            instruction_template=(
                "Publish the {project} workspace outcome to drive and create the "
                "linked follow-up event."
            ),
            domains=("drive", "calendar", "workspace"),
            queries=(
                _query(
                    "decision-summary",
                    ResourceKind.WORKSPACE_FILE,
                    "decision-summary.md",
                    owner_is_actor=True,
                ),
                _query("meeting-pack", ResourceKind.DRIVE_FILE, "{project} Review Meeting Pack"),
                _query("planning-event", ResourceKind.CALENDAR_EVENT, "{project} planning"),
            ),
            goals=(
                _goal(
                    "locate-workspace-output",
                    "Locate and read the final workspace output.",
                    queries=("decision-summary",),
                ),
                _goal(
                    "verify-handoff-context",
                    "Verify current drive and calendar context.",
                    depends=("locate-workspace-output",),
                    queries=("meeting-pack", "planning-event"),
                ),
                _goal(
                    "publish-drive-output",
                    "Create a sourced drive handoff artifact.",
                    depends=("verify-handoff-context",),
                    actions=(DRIVE_CREATE,),
                ),
                _goal(
                    "create-followup-event",
                    "Create a follow-up event linked to the published output.",
                    depends=("publish-drive-output",),
                    actions=(CALENDAR_CREATE,),
                ),
            ),
            allowed_side_effects=(CALENDAR_CREATE, DRIVE_CREATE),
            forbidden_side_effects=(DRIVE_SHARE, DRIVE_TRASH, DRIVE_PERMISSIONS),
            structural_tags=("create", "pagination", "three-domain", "version"),
        ),
    )


TASK_BLUEPRINTS = _blueprints()
TASK_BLUEPRINT_BY_ID = {item.blueprint_id: item for item in TASK_BLUEPRINTS}
TASK_BLUEPRINT_CATALOG_DIGEST = sha256_digest(
    tuple(item.model_dump(mode="json", exclude_none=False) for item in TASK_BLUEPRINTS)
)

if len(TASK_BLUEPRINTS) != 10 or len(TASK_BLUEPRINT_BY_ID) != 10:  # pragma: no cover
    raise RuntimeError("Office V2 task blueprint catalog must contain exactly 10 entries")


__all__ = [
    "BlueprintQuery",
    "GoalNodeTemplate",
    "TASK_BLUEPRINT_BY_ID",
    "TASK_BLUEPRINT_CATALOG_DIGEST",
    "TASK_BLUEPRINTS",
    "TaskBlueprint",
]
