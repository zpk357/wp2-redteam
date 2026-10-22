from __future__ import annotations

from sandbox.scenarios.office_v2.canonical_world import (
    OfficeWorldState,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActorContext,
    LogicalClock,
    PredicateField,
    PredicateOperator,
    QueryCardinality,
    QueryTiePolicy,
    RelationDirection,
    ResourceKind,
    ResourcePredicate,
    ResourceQuery,
    ResourceRelation,
    ResourceRelationConstraint,
)
from sandbox.scenarios.office_v2.observation import ObservationPolicy
from sandbox.scenarios.office_v2.resolution import (
    ResolutionDisposition,
    ResolutionFailureCode,
    binding_matches_state,
    resolve_clarification_selection,
    resolve_resource_query,
)

READ_CAPABILITIES = ("calendar.read", "drive.read", "mail.read", "workspace.read")


def _actor(state: OfficeWorldState, actor_id: str) -> ActorContext:
    return state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id=actor_id,
        session_capabilities=READ_CAPABILITIES,
        logical_time=state.logical_clock.now,
    )


def _query(
    query_id: str,
    kind: ResourceKind,
    *predicates: ResourcePredicate,
    access: tuple[AccessRight, ...] = (AccessRight.READ,),
    cardinality: QueryCardinality = QueryCardinality.EXACTLY_ONE,
    tie_policy: QueryTiePolicy = QueryTiePolicy.UNIQUE_REQUIRED,
    relations: tuple[ResourceRelationConstraint, ...] = (),
) -> ResourceQuery:
    return ResourceQuery(
        query_id=query_id,
        binding_name=f"binding-{query_id}",
        resource_kind=kind,
        predicates=predicates,
        actor_access=access,
        relation_constraints=relations,
        cardinality=cardinality,
        tie_policy=tie_policy,
    )


def test_unique_binding_is_stable_and_records_all_candidate_evidence() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-review-plan",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.PROJECT, value="apollo"),
        ResourcePredicate(field=PredicateField.SUBJECT, value="Apollo Q3 Review Plan"),
    )
    policy = ObservationPolicy(default_page_size=3, maximum_page_size=3)

    first = resolve_resource_query(state, actor, query, policy=policy)
    repeated = resolve_resource_query(state, actor, query, policy=policy)

    assert repeated == first
    assert first.binding is not None
    assert first.binding.resource_refs[0].resource_id == "drive.apollo.review-plan"
    assert first.binding.resource_refs[0].version_id == "version.apollo.review-plan.2"
    assert len(first.evidence) > policy.maximum_page_size
    assert first.binding.matched_fact_refs == tuple(
        item.evidence_id
        for item in first.evidence
        if item.disposition is ResolutionDisposition.MATCHED
    )


def test_ambiguous_exactly_one_returns_structured_clarification_in_stable_order() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-review-plan-choice",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(
            field=PredicateField.SUBJECT,
            operator=PredicateOperator.CONTAINS_TOKEN,
            value="Apollo Q3 Review Plan",
        ),
        tie_policy=QueryTiePolicy.CLARIFICATION_REQUIRED,
    )

    outcome = resolve_resource_query(state, actor, query)

    assert outcome.binding is None
    assert outcome.failure is None
    assert outcome.clarification is not None
    assert tuple(ref.resource_id for ref in outcome.clarification.candidate_refs) == (
        "drive.apollo.review-plan",
        "drive.apollo.review-plan-archive",
    )


def test_authenticated_clarification_selection_freezes_the_selected_version() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-review-plan-selection",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(
            field=PredicateField.SUBJECT,
            operator=PredicateOperator.CONTAINS_TOKEN,
            value="Apollo Q3 Review Plan",
        ),
        tie_policy=QueryTiePolicy.CLARIFICATION_REQUIRED,
    )
    initial = resolve_resource_query(state, actor, query)
    assert initial.clarification is not None
    selected = next(
        ref
        for ref in initial.clarification.candidate_refs
        if ref.resource_id == "drive.apollo.review-plan"
    )

    resolved = resolve_clarification_selection(
        state, actor, query, initial.clarification, selected
    )

    assert resolved.binding is not None
    assert resolved.binding.resolution_status.value == "resolved_after_clarification"
    assert resolved.binding.resource_refs == (selected,)
    assert selected.version_id == "version.apollo.review-plan.2"


def test_clarification_selection_rejects_stale_or_out_of_scope_choices() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-review-plan-selection-failure",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(
            field=PredicateField.SUBJECT,
            operator=PredicateOperator.CONTAINS_TOKEN,
            value="Apollo Q3 Review Plan",
        ),
        tie_policy=QueryTiePolicy.CLARIFICATION_REQUIRED,
    )
    initial = resolve_resource_query(state, actor, query)
    assert initial.clarification is not None
    outside = initial.clarification.candidate_refs[0].model_copy(
        update={"resource_id": "drive.apollo.non-candidate"}
    )
    rejected = resolve_clarification_selection(
        state, actor, query, initial.clarification, outside
    )
    changed_state = state.model_copy(
        update={"logical_clock": state.logical_clock.model_copy(update={"now": 1001})}
    )
    stale = resolve_clarification_selection(
        changed_state,
        _actor(changed_state, "user.maya.chen"),
        query,
        initial.clarification,
        initial.clarification.candidate_refs[0],
    )

    assert rejected.failure is not None
    assert rejected.failure.code is ResolutionFailureCode.SELECTION_OUT_OF_SCOPE
    assert stale.failure is not None
    assert stale.failure.code is ResolutionFailureCode.CLARIFICATION_STALE


def test_one_or_more_freezes_the_complete_matching_set_across_pages() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-apollo-drive-set",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.PROJECT, value="apollo"),
        cardinality=QueryCardinality.ONE_OR_MORE,
    )

    outcome = resolve_resource_query(
        state,
        actor,
        query,
        policy=ObservationPolicy(default_page_size=3, maximum_page_size=3),
    )

    assert outcome.binding is not None
    assert len(outcome.binding.resource_refs) == 10
    assert all(
        ref.resource_id.startswith("drive.apollo.")
        for ref in outcome.binding.resource_refs
    )


def test_hidden_and_nonexistent_matches_share_the_same_public_failure_code() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "external.nadia.reed")
    hidden = _query(
        "query-hidden-resource",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(
            field=PredicateField.SUBJECT,
            value="Cedar Restricted Delivery Roadmap",
        ),
    )
    absent = _query(
        "query-absent-resource",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.SUBJECT, value="Zephyr Missing Plan"),
    )

    hidden_result = resolve_resource_query(state, actor, hidden)
    absent_result = resolve_resource_query(state, actor, absent)

    assert hidden_result.failure is not None
    assert absent_result.failure is not None
    assert hidden_result.failure.code is ResolutionFailureCode.NO_VISIBLE_MATCH
    assert absent_result.failure.code is ResolutionFailureCode.NO_VISIBLE_MATCH


def test_discoverable_candidate_reports_visible_access_mismatch_without_read_fields() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "external.nadia.reed")
    discover_acl = next(
        entry
        for entry in state.domain_graph.acl_entries
        if entry.grantee_id == actor.actor_id and entry.rights == (AccessRight.DISCOVER,)
    )
    target = next(
        item
        for item in state.domain_graph.drive.files
        if item.file_id == discover_acl.resource.resource_id
    )
    query = _query(
        "query-needs-read",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.SUBJECT, value=target.name),
    )

    outcome = resolve_resource_query(state, actor, query)

    assert outcome.failure is not None
    assert outcome.failure.code is ResolutionFailureCode.VISIBLE_ACCESS_MISMATCH
    target_evidence = next(
        item for item in outcome.evidence if item.resource.resource_id == target.file_id
    )
    assert target_evidence.disposition is ResolutionDisposition.ACCESS_MISMATCH


def test_version_and_calendar_time_predicates_use_typed_observation_fields() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    old_version = _query(
        "query-old-review-plan",
        ResourceKind.DRIVE_FILE_VERSION,
        ResourcePredicate(
            field=PredicateField.SUBJECT,
            value="Apollo Q3 Review Plan [version.apollo.review-plan.1]",
        ),
        ResourcePredicate(field=PredicateField.VERSION_STATE, value="old"),
    )
    calendar = _query(
        "query-apollo-review",
        ResourceKind.CALENDAR_EVENT,
        ResourcePredicate(field=PredicateField.SUBJECT, value="Apollo review"),
        ResourcePredicate(
            field=PredicateField.START_TIME,
            operator=PredicateOperator.AFTER,
            value=1_789_000_000,
        ),
    )

    old_result = resolve_resource_query(state, actor, old_version)
    calendar_result = resolve_resource_query(state, actor, calendar)

    assert old_result.binding is not None
    assert old_result.binding.resource_refs[0].resource_id == "version.apollo.review-plan.1"
    assert calendar_result.binding is not None
    assert calendar_result.binding.resource_refs[0].resource_id == "event.apollo.1"


def test_relation_constraint_never_includes_linked_messages_hidden_from_actor() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    drive_query = _query(
        "query-related-drive",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.SUBJECT, value="Apollo Q3 Review Plan"),
    )
    drive_outcome = resolve_resource_query(state, actor, drive_query)
    assert drive_outcome.binding is not None
    mail_query = _query(
        "query-linked-mail",
        ResourceKind.MAIL_MESSAGE,
        cardinality=QueryCardinality.ONE_OR_MORE,
        relations=(
            ResourceRelationConstraint(
                relation=ResourceRelation.ATTACHMENT,
                direction=RelationDirection.OUTBOUND,
                related_query_id=drive_query.query_id,
            ),
        ),
    )

    outcome = resolve_resource_query(
        state,
        actor,
        mail_query,
        related_bindings={drive_query.query_id: drive_outcome.binding},
    )

    assert outcome.binding is not None
    assert tuple(ref.resource_id for ref in outcome.binding.resource_refs) == (
        "message.apollo.01.1",
    )
    assert not {
        "message.apollo.01.2",
        "message.apollo.01.3",
    }.intersection(ref.resource_id for ref in outcome.binding.resource_refs)


def test_frozen_binding_detects_world_drift_without_rebinding() -> None:
    state = load_canonical_world().state
    actor = _actor(state, "user.maya.chen")
    query = _query(
        "query-frozen-review-plan",
        ResourceKind.DRIVE_FILE,
        ResourcePredicate(field=PredicateField.SUBJECT, value="Apollo Q3 Review Plan"),
    )
    outcome = resolve_resource_query(state, actor, query)
    assert outcome.binding is not None
    frozen_refs = outcome.binding.resource_refs
    changed = OfficeWorldState.model_validate(
        {
            **state.model_dump(mode="python"),
            "logical_clock": LogicalClock(
                now=state.logical_clock.now + 1,
                timezone=state.logical_clock.timezone,
            ),
        }
    )

    assert binding_matches_state(outcome.binding, state) is True
    assert binding_matches_state(outcome.binding, changed) is False
    assert outcome.binding.resource_refs == frozen_refs
