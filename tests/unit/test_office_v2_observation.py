from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.office_v2.canonical_world import (
    OfficeWorldState,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActorContext,
    LogicalClock,
    ResourceKind,
)
from sandbox.scenarios.office_v2.observation import (
    DriveVersionView,
    ObservationAccessLevel,
    ObservationError,
    ObservationFailureCode,
    ObservationPolicy,
    ObservationQuery,
    observe,
)

READ_CAPABILITIES = ("calendar.read", "drive.read", "mail.read", "workspace.read")


def _actor(actor_id: str, capabilities: tuple[str, ...] = READ_CAPABILITIES) -> ActorContext:
    world = load_canonical_world()
    return world.state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id=actor_id,
        session_capabilities=capabilities,
        logical_time=world.state.logical_clock.now,
    )


def test_capability_and_acl_are_both_required_before_resource_discovery() -> None:
    state = load_canonical_world().state
    without_capability = _actor("external.nadia.reed", ())

    page = observe(
        state,
        without_capability,
        ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,)),
    )

    assert page.items == ()
    assert page.has_more is False
    assert "total" not in page.model_dump()


def test_discover_only_resource_redacts_read_fields_and_hidden_matches() -> None:
    state = load_canonical_world().state
    actor = _actor("external.nadia.reed")
    discover_acl = next(
        entry
        for entry in state.domain_graph.acl_entries
        if entry.grantee_id == actor.actor_id and entry.rights == (AccessRight.DISCOVER,)
    )
    drive_file = next(
        item
        for item in state.domain_graph.drive.files
        if item.file_id == discover_acl.resource.resource_id
    )

    page = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            text=drive_file.name,
        ),
    )
    item = page.items[0]
    assert item.access_level is ObservationAccessLevel.DISCOVER
    assert item.display_name == drive_file.name
    assert item.content is None
    assert item.owner_id is None
    assert item.participant_ids == ()
    assert item.sensitivity is None
    assert item.related_refs == ()
    assert "grantee" not in item.model_dump_json()

    hidden = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            text="Cedar Restricted Delivery Roadmap",
        ),
    )
    absent = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE,), text="Nonexistent Zephyr Document"
        ),
    )
    assert hidden.items == absent.items == ()
    assert hidden.has_more is absent.has_more is False


def test_readable_drive_projection_exposes_current_content_without_acl_members() -> None:
    state = load_canonical_world().state
    actor = _actor("user.maya.chen")
    page = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            text="Apollo Q3 Review Plan",
        ),
        policy=ObservationPolicy(maximum_page_size=50),
    )

    current = next(
        item for item in page.items if item.resource.resource_id == "drive.apollo.review-plan"
    )
    assert current.access_level is ObservationAccessLevel.READ
    assert current.resource.version_id == "version.apollo.review-plan.2"
    assert "revision 2" in (current.content or "")
    assert current.owner_id == "user.jordan.lee"
    assert current.sensitivity is not None
    serialized = current.model_dump_json()
    assert "group.apollo" not in serialized
    assert "granted_by" not in serialized


def test_mail_calendar_and_workspace_views_follow_actor_relationships() -> None:
    state = load_canonical_world().state
    actor = _actor("user.maya.chen")
    query = ObservationQuery(
        resource_kinds=(
            ResourceKind.MAIL_MESSAGE,
            ResourceKind.CALENDAR_EVENT,
            ResourceKind.WORKSPACE_FILE,
        ),
        page_size=25,
    )
    first = observe(state, actor, query)
    second = observe(
        state,
        actor,
        query.model_copy(update={"page_token": first.next_page_token}),
    )
    items = (*first.items, *second.items)

    assert all(
        item.resource.resource_id.startswith(
            ("message.apollo", "event.apollo", "/workspace/apollo")
        )
        for item in items
    )
    assert any(item.resource.kind is ResourceKind.MAIL_MESSAGE for item in items)
    assert any(item.resource.kind is ResourceKind.CALENDAR_EVENT for item in items)
    assert any(item.resource.kind is ResourceKind.WORKSPACE_FILE for item in items)


def test_pagination_is_stable_complete_and_has_no_duplicates() -> None:
    state = load_canonical_world().state
    actor = _actor("user.maya.chen")
    query = ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,), page_size=3)

    first = observe(state, actor, query)
    repeated = observe(state, actor, query)
    assert repeated == first
    assert first.next_page_token is not None
    assert not first.next_page_token.isdigit()

    seen: list[tuple[str, str, str]] = []
    page = first
    while True:
        seen.extend(item.resource.sort_key() for item in page.items)
        if not page.has_more:
            break
        page = observe(
            state,
            actor,
            query.model_copy(update={"page_token": page.next_page_token}),
        )

    all_at_once = observe(
        state,
        actor,
        ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,), page_size=25),
    )
    assert len(seen) == len(set(seen))
    assert seen == [item.resource.sort_key() for item in all_at_once.items]


def test_page_token_rejects_actor_query_sort_tampering_and_stale_state() -> None:
    state = load_canonical_world().state
    maya = _actor("user.maya.chen")
    jordan = _actor("user.jordan.lee")
    query = ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,), page_size=2)
    token = observe(state, maya, query).next_page_token
    assert token is not None

    cases = (
        (
            jordan,
            query,
            ObservationPolicy(),
            ObservationFailureCode.ACTOR_MISMATCH,
            state,
        ),
        (
            maya,
            ObservationQuery(
                resource_kinds=(ResourceKind.DRIVE_FILE,),
                text="apollo",
                page_size=2,
            ),
            ObservationPolicy(),
            ObservationFailureCode.QUERY_MISMATCH,
            state,
        ),
        (
            maya,
            query,
            ObservationPolicy(sort_version="observation-sort-v2"),
            ObservationFailureCode.SORT_VERSION_MISMATCH,
            state,
        ),
        (
            maya,
            query,
            ObservationPolicy(),
            ObservationFailureCode.STALE_PAGE_TOKEN,
            OfficeWorldState.model_validate(
                {
                    **state.model_dump(mode="python"),
                    "logical_clock": LogicalClock(
                        now=state.logical_clock.now + 1,
                        timezone=state.logical_clock.timezone,
                    ),
                }
            ),
        ),
    )
    for actor, changed_query, policy, expected, target_state in cases:
        with pytest.raises(ObservationError) as caught:
            observe(
                target_state,
                actor,
                changed_query.model_copy(update={"page_token": token}),
                policy=policy,
            )
        assert caught.value.code is expected

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ObservationError) as caught:
        observe(state, maya, query.model_copy(update={"page_token": tampered}))
    assert caught.value.code is ObservationFailureCode.INVALID_PAGE_TOKEN


def test_drive_version_view_separates_current_from_history() -> None:
    state = load_canonical_world().state
    actor = _actor("user.maya.chen")
    current = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE_VERSION,),
            text="Apollo",
            page_size=25,
        ),
    )
    history = observe(
        state,
        actor,
        ObservationQuery(
            resource_kinds=(ResourceKind.DRIVE_FILE_VERSION,),
            text="Apollo",
            drive_version_view=DriveVersionView.ALL,
            page_size=25,
        ),
    )
    current_ids = {item.resource.resource_id for item in current.items}
    expected_current = {
        item.current_version_id
        for item in state.domain_graph.drive.files
        if item.file_id.startswith("drive.apollo")
    }

    assert current_ids == expected_current
    assert len(history.items) > len(current.items)
    assert "version.apollo.review-plan.1" in {
        item.resource.resource_id for item in history.items
    }


def test_page_size_limit_and_frozen_output_are_enforced() -> None:
    state = load_canonical_world().state
    actor = _actor("user.maya.chen")
    with pytest.raises(ObservationError) as caught:
        observe(
            state,
            actor,
            ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,), page_size=6),
            policy=ObservationPolicy(default_page_size=5, maximum_page_size=5),
        )
    assert caught.value.code is ObservationFailureCode.PAGE_SIZE_EXCEEDED

    item = observe(
        state,
        actor,
        ObservationQuery(resource_kinds=(ResourceKind.DRIVE_FILE,), page_size=1),
    ).items[0]
    with pytest.raises(ValidationError, match="frozen"):
        item.display_name = "changed"
