from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    build_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    IdentityDirectory,
    OfficeDomainGraph,
    Organization,
    Principal,
    PrincipalKind,
    ResourceKind,
    ResourceLink,
    ResourceRef,
    ResourceRelation,
    WorkspaceFile,
    WorkspaceStore,
)
from sandbox.scenarios.office_v2.world import (
    EpisodeWorld,
    RelationChangeOperation,
    StateObjectKind,
)

NOW = datetime(2026, 8, 6, 8, tzinfo=UTC)
SECRET_CONTENT = "Board acquisition target: Northstar"


def _empty_graph() -> OfficeDomainGraph:
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
    )
    directory = IdentityDirectory(
        organization=organization,
        principals=(
            Principal(
                principal_id="user.alice",
                kind=PrincipalKind.USER,
                display_name="Alice Example",
                email="alice@example.test",
                organization_id=organization.organization_id,
            ),
        ),
    )
    return OfficeDomainGraph(directory=directory)


def _canonical_world() -> CanonicalOfficeWorld:
    return build_canonical_world(OfficeWorldState(domain_graph=_empty_graph()))


def _workspace_file(
    path: str,
    content: str = SECRET_CONTENT,
    *,
    version: int = 1,
    updated_at: datetime = NOW,
) -> WorkspaceFile:
    return WorkspaceFile(
        path=path,
        owner_id="user.alice",
        content=content,
        media_type="text/markdown",
        version=version,
        created_at=NOW,
        updated_at=updated_at,
    )


def _graph_with_workspace(
    graph: OfficeDomainGraph,
    files: tuple[WorkspaceFile, ...],
    *,
    links: tuple[ResourceLink, ...] = (),
) -> OfficeDomainGraph:
    return OfficeDomainGraph(
        directory=graph.directory,
        mail=graph.mail,
        drive=graph.drive,
        calendar=graph.calendar,
        workspace=WorkspaceStore(files=files),
        acl_entries=graph.acl_entries,
        resource_links=links,
    )


def test_canonical_world_is_immutable_digest_locked_and_repeatable() -> None:
    first = _canonical_world()
    second = _canonical_world()

    assert first == second
    assert first.world_digest == second.world_digest
    with pytest.raises(ValidationError, match="world_digest"):
        CanonicalOfficeWorld(
            world_version=first.world_version,
            state=first.state,
            world_digest="0" * 64,
        )
    with pytest.raises(ValidationError, match="frozen"):
        first.world_version = "2.1"


def test_successful_commit_is_isolated_and_emits_created_and_relation_delta() -> None:
    canonical = _canonical_world()
    first = EpisodeWorld(canonical, episode_id="episode.alpha")
    second = EpisodeWorld(canonical, episode_id="episode.beta")
    source = _workspace_file("/workspace/private/source.md")
    output = _workspace_file("/workspace/shared/output.md", "Sanitized output")
    link = ResourceLink(
        link_id="link.output-source",
        source=ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=output.path),
        target=ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=source.path),
        relation=ResourceRelation.DERIVED_FROM,
        created_by="user.alice",
        created_at=NOW,
    )

    transaction = first.begin_transaction(action_request_id="action.write-output")
    transaction.replace_domain_graph(
        _graph_with_workspace(first.state.domain_graph, (source, output), links=(link,))
    )
    record = transaction.commit()

    created = {(item.kind, item.object_id) for item in record.state_delta.created_objects}
    assert record.committed is True
    assert record.before_state_digest != record.after_state_digest
    assert created == {
        (StateObjectKind.WORKSPACE_FILE, source.path),
        (StateObjectKind.WORKSPACE_FILE, output.path),
    }
    assert len(record.state_delta.changed_relations) == 1
    assert record.state_delta.changed_relations[0].operation is RelationChangeOperation.ADD
    assert second.state == canonical.state
    assert canonical.state.domain_graph.workspace.files == ()


def test_field_delta_contains_digests_and_paths_but_not_sensitive_values() -> None:
    episode = EpisodeWorld(_canonical_world(), episode_id="episode.fields")
    initial = _workspace_file("/workspace/private/plan.md")
    create = episode.begin_transaction()
    create.replace_domain_graph(_graph_with_workspace(episode.state.domain_graph, (initial,)))
    create.commit()

    revised_content = "Confidential successor plan"
    revised = _workspace_file(
        initial.path,
        revised_content,
        version=2,
        updated_at=NOW + timedelta(minutes=5),
    )
    update = episode.begin_transaction()
    update.replace_domain_graph(_graph_with_workspace(episode.state.domain_graph, (revised,)))
    record = update.commit()

    changed_paths = {change.field_path for change in record.state_delta.changed_fields}
    serialized = record.model_dump_json()
    assert changed_paths == {("content",), ("updated_at",), ("version",)}
    assert SECRET_CONTENT not in serialized
    assert revised_content not in serialized
    assert all(
        change.before_value_digest.startswith("sha256:")
        and change.after_value_digest.startswith("sha256:")
        for change in record.state_delta.changed_fields
    )


def test_explicit_rollback_discards_staged_state_and_emits_empty_delta() -> None:
    episode = EpisodeWorld(_canonical_world(), episode_id="episode.rollback")
    before = episode.state_digest
    transaction = episode.begin_transaction()
    transaction.replace_domain_graph(
        _graph_with_workspace(
            episode.state.domain_graph,
            (_workspace_file("/workspace/private/staged.md"),),
        )
    )

    record = transaction.rollback("operator_cancelled")

    assert record.committed is False
    assert record.before_state_digest == record.after_state_digest == before
    assert record.state_delta.is_empty()
    assert episode.state.domain_graph.workspace.files == ()
    with pytest.raises(RuntimeError, match="closed"):
        transaction.commit()


def test_validation_failure_rolls_back_without_partial_state() -> None:
    episode = EpisodeWorld(_canonical_world(), episode_id="episode.invalid")
    invalid_file = _workspace_file("/workspace/private/invalid-owner.md").model_copy(
        update={"owner_id": "user.missing"}
    )
    invalid_graph = episode.state.domain_graph.model_copy(
        update={"workspace": WorkspaceStore(files=(invalid_file,))}
    )
    transaction = episode.begin_transaction()
    transaction.replace_domain_graph(invalid_graph)

    with pytest.raises(ValidationError, match="unknown owner"):
        transaction.commit()

    record = episode.history[-1]
    assert record.committed is False
    assert record.failure_code == "transaction_validation_failed"
    assert record.state_delta.is_empty()
    assert episode.state.domain_graph.workspace.files == ()


def test_deterministic_ids_digests_removals_and_single_active_transaction() -> None:
    canonical = _canonical_world()
    episodes = (
        EpisodeWorld(canonical, episode_id="episode.deterministic"),
        EpisodeWorld(canonical, episode_id="episode.deterministic"),
    )
    records = []
    for episode in episodes:
        transaction = episode.begin_transaction()
        assert transaction.allocate_id("workspace") == (
            "workspace.episode.deterministic.000000"
        )
        transaction.advance_clock(3)
        records.append(transaction.commit())

    assert records[0] == records[1]
    assert episodes[0].state_digest == episodes[1].state_digest

    episode = episodes[0]
    active = episode.begin_transaction()
    with pytest.raises(RuntimeError, match="active transaction"):
        episode.begin_transaction()
    active.rollback()

    add = episode.begin_transaction()
    added_file = _workspace_file("/workspace/private/remove-me.md")
    add.replace_domain_graph(_graph_with_workspace(episode.state.domain_graph, (added_file,)))
    add.commit()
    remove = episode.begin_transaction()
    remove.replace_domain_graph(_graph_with_workspace(episode.state.domain_graph, ()))
    removal_record = remove.commit()

    assert removal_record.state_delta.removed_objects == (
        removal_record.state_delta.removed_objects[0],
    )
    assert removal_record.state_delta.removed_objects[0].object_id == added_file.path
