"""Deterministic, atomic episode transactions and state-difference evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
)
from sandbox.scenarios.office_v2.models import (
    DelegationGrant,
    FieldPathSegment,
    Identifier,
    LogicalClock,
    OfficeDomainGraph,
    OfficeV2Contract,
    ResourceLink,
    Sha256Digest,
)
from sandbox.scenarios.office_v2.policy import EnterprisePolicyRule

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_SHA256_ADAPTER = TypeAdapter(Sha256Digest)


class StateObjectKind(StrEnum):
    ORGANIZATION = "organization"
    PRINCIPAL = "principal"
    GROUP_MEMBERSHIP = "group_membership"
    ROLE_ASSIGNMENT = "role_assignment"
    MAIL_THREAD = "mail_thread"
    MAIL_MESSAGE = "mail_message"
    MAIL_DELIVERY = "mail_delivery"
    DRIVE_FILE = "drive_file"
    DRIVE_FILE_VERSION = "drive_file_version"
    ACL_ENTRY = "acl_entry"
    SHARE_RECORD = "share_record"
    CALENDAR_EVENT = "calendar_event"
    ATTENDANCE = "attendance"
    WORKSPACE_FILE = "workspace_file"
    POLICY_RULE = "policy_rule"
    DELEGATION_GRANT = "delegation_grant"
    STATE_META = "state_meta"


class StateObjectRef(OfficeV2Contract):
    kind: StateObjectKind
    object_id: str = Field(min_length=1, max_length=512)

    def sort_key(self) -> tuple[str, str]:
        return (self.kind.value, self.object_id)


class StateChangeOperation(StrEnum):
    UPDATE = "update"


class RelationChangeOperation(StrEnum):
    ADD = "add"
    REMOVE = "remove"


class StateFieldChange(OfficeV2Contract):
    object_ref: StateObjectRef
    operation: StateChangeOperation = StateChangeOperation.UPDATE
    field_path: tuple[FieldPathSegment, ...] = Field(min_length=1)
    before_value_digest: Sha256Digest
    after_value_digest: Sha256Digest

    def sort_key(self) -> tuple[str, str, tuple[str, ...]]:
        return (*self.object_ref.sort_key(), self.field_path)


class StateRelationChange(OfficeV2Contract):
    operation: RelationChangeOperation
    link_id: Identifier
    relation: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=1024)
    target: str = Field(min_length=1, max_length=1024)

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.operation.value,
            self.relation,
            self.source,
            self.target,
            self.link_id,
        )


class StateDelta(OfficeV2Contract):
    changed_fields: tuple[StateFieldChange, ...] = Field(default_factory=tuple)
    created_objects: tuple[StateObjectRef, ...] = Field(default_factory=tuple)
    removed_objects: tuple[StateObjectRef, ...] = Field(default_factory=tuple)
    changed_relations: tuple[StateRelationChange, ...] = Field(default_factory=tuple)

    @field_validator("changed_fields")
    @classmethod
    def fields_are_canonical(
        cls, value: tuple[StateFieldChange, ...]
    ) -> tuple[StateFieldChange, ...]:
        return _unique_sorted(value, StateFieldChange.sort_key, "changed_fields")

    @field_validator("created_objects", "removed_objects")
    @classmethod
    def objects_are_canonical(
        cls, value: tuple[StateObjectRef, ...]
    ) -> tuple[StateObjectRef, ...]:
        return _unique_sorted(value, StateObjectRef.sort_key, "state objects")

    @field_validator("changed_relations")
    @classmethod
    def relations_are_canonical(
        cls, value: tuple[StateRelationChange, ...]
    ) -> tuple[StateRelationChange, ...]:
        return _unique_sorted(value, StateRelationChange.sort_key, "changed_relations")

    @model_validator(mode="after")
    def object_sets_do_not_overlap(self) -> Self:
        created = {item.sort_key() for item in self.created_objects}
        removed = {item.sort_key() for item in self.removed_objects}
        if created & removed:
            raise ValueError("created_objects and removed_objects must not overlap")
        return self

    def is_empty(self) -> bool:
        return not (
            self.changed_fields
            or self.created_objects
            or self.removed_objects
            or self.changed_relations
        )


class StateTransitionRecord(OfficeV2Contract):
    transaction_id: Identifier
    action_request_id: Identifier | None = None
    policy_decision_id: Identifier | None = None
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    committed: bool
    failure_code: Identifier | None = None
    state_delta: StateDelta
    transition_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"transition_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def outcome_and_digest_are_consistent(self) -> Self:
        if self.committed and self.failure_code is not None:
            raise ValueError("committed transition cannot have failure_code")
        if not self.committed:
            if self.failure_code is None:
                raise ValueError("failed transition requires failure_code")
            if self.before_state_digest != self.after_state_digest:
                raise ValueError("failed transition cannot change state digest")
            if not self.state_delta.is_empty():
                raise ValueError("failed transition must have an empty state_delta")
        if self.transition_digest != sha256_digest(self.digest_payload()):
            raise ValueError("transition_digest does not match transition payload")
        return self


def _unique_sorted(values: tuple[Any, ...], key: Any, field_name: str) -> tuple[Any, ...]:
    keys = tuple(key(item) for item in values)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(values, key=key))


def _composite_id(kind: StateObjectKind, payload: object) -> str:
    return f"{kind.value}.{sha256_digest(payload).removeprefix('sha256:')[:24]}"


def _dump_object(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude={"schema_version"}, exclude_none=False)


def _add_objects(
    objects: dict[tuple[str, str], tuple[StateObjectRef, dict[str, Any]]],
    kind: StateObjectKind,
    values: tuple[BaseModel, ...],
    id_getter: Any,
) -> None:
    for value in values:
        ref = StateObjectRef(kind=kind, object_id=id_getter(value))
        objects[ref.sort_key()] = (ref, _dump_object(value))


def _state_objects(
    state: OfficeWorldState,
) -> dict[tuple[str, str], tuple[StateObjectRef, dict[str, Any]]]:
    graph = state.domain_graph
    result: dict[tuple[str, str], tuple[StateObjectRef, dict[str, Any]]] = {}
    _add_objects(
        result,
        StateObjectKind.ORGANIZATION,
        (graph.directory.organization,),
        lambda item: item.organization_id,
    )
    _add_objects(
        result,
        StateObjectKind.PRINCIPAL,
        graph.directory.principals,
        lambda item: item.principal_id,
    )
    _add_objects(
        result,
        StateObjectKind.GROUP_MEMBERSHIP,
        graph.directory.memberships,
        lambda item: _composite_id(StateObjectKind.GROUP_MEMBERSHIP, item.sort_key()),
    )
    _add_objects(
        result,
        StateObjectKind.ROLE_ASSIGNMENT,
        graph.directory.role_assignments,
        lambda item: _composite_id(StateObjectKind.ROLE_ASSIGNMENT, item.sort_key()),
    )
    _add_objects(
        result, StateObjectKind.MAIL_THREAD, graph.mail.threads, lambda item: item.thread_id
    )
    _add_objects(
        result, StateObjectKind.MAIL_MESSAGE, graph.mail.messages, lambda item: item.message_id
    )
    _add_objects(
        result,
        StateObjectKind.MAIL_DELIVERY,
        graph.mail.deliveries,
        lambda item: _composite_id(StateObjectKind.MAIL_DELIVERY, item.sort_key()),
    )
    _add_objects(result, StateObjectKind.DRIVE_FILE, graph.drive.files, lambda item: item.file_id)
    _add_objects(
        result,
        StateObjectKind.DRIVE_FILE_VERSION,
        graph.drive.versions,
        lambda item: item.version_id,
    )
    _add_objects(
        result,
        StateObjectKind.ACL_ENTRY,
        graph.acl_entries,
        lambda item: _composite_id(StateObjectKind.ACL_ENTRY, item.sort_key()),
    )
    _add_objects(
        result,
        StateObjectKind.SHARE_RECORD,
        graph.drive.share_records,
        lambda item: item.share_id,
    )
    _add_objects(
        result,
        StateObjectKind.CALENDAR_EVENT,
        graph.calendar.events,
        lambda item: item.event_id,
    )
    _add_objects(
        result,
        StateObjectKind.ATTENDANCE,
        graph.calendar.attendance,
        lambda item: _composite_id(StateObjectKind.ATTENDANCE, item.sort_key()),
    )
    _add_objects(
        result,
        StateObjectKind.WORKSPACE_FILE,
        graph.workspace.files,
        lambda item: item.path,
    )
    _add_objects(
        result,
        StateObjectKind.POLICY_RULE,
        state.policy_rules,
        lambda item: item.rule_id,
    )
    _add_objects(
        result,
        StateObjectKind.DELEGATION_GRANT,
        state.delegation_grants,
        lambda item: item.grant_id,
    )
    meta_ref = StateObjectRef(kind=StateObjectKind.STATE_META, object_id="episode")
    result[meta_ref.sort_key()] = (
        meta_ref,
        {
            "logical_clock": state.logical_clock.model_dump(mode="json"),
            "next_id_sequence": state.next_id_sequence,
        },
    )
    return result


def _link_locator(link: ResourceLink, endpoint: str) -> str:
    ref = link.source if endpoint == "source" else link.target
    return ":".join((ref.kind.value, ref.resource_id, ref.version_id or ""))


def diff_states(before: OfficeWorldState, after: OfficeWorldState) -> StateDelta:
    """Return canonical metadata-only evidence for a state transition."""

    before_objects = _state_objects(before)
    after_objects = _state_objects(after)
    before_keys = set(before_objects)
    after_keys = set(after_objects)
    created = tuple(after_objects[key][0] for key in after_keys - before_keys)
    removed = tuple(before_objects[key][0] for key in before_keys - after_keys)
    fields: list[StateFieldChange] = []
    for key in before_keys & after_keys:
        ref, before_value = before_objects[key]
        after_value = after_objects[key][1]
        for field_name in sorted(set(before_value) | set(after_value)):
            old = before_value.get(field_name)
            new = after_value.get(field_name)
            if old != new:
                fields.append(
                    StateFieldChange(
                        object_ref=ref,
                        field_path=(field_name,),
                        before_value_digest=sha256_digest(old),
                        after_value_digest=sha256_digest(new),
                    )
                )

    before_links = {item.link_id: item for item in before.domain_graph.resource_links}
    after_links = {item.link_id: item for item in after.domain_graph.resource_links}
    relation_changes: list[StateRelationChange] = []
    for link_id in sorted(set(before_links) | set(after_links)):
        old = before_links.get(link_id)
        new = after_links.get(link_id)
        if old == new:
            continue
        if old is not None:
            relation_changes.append(_relation_change(RelationChangeOperation.REMOVE, old))
        if new is not None:
            relation_changes.append(_relation_change(RelationChangeOperation.ADD, new))

    return StateDelta(
        changed_fields=tuple(fields),
        created_objects=created,
        removed_objects=removed,
        changed_relations=tuple(relation_changes),
    )


def _relation_change(operation: RelationChangeOperation, link: ResourceLink) -> StateRelationChange:
    return StateRelationChange(
        operation=operation,
        link_id=link.link_id,
        relation=link.relation.value,
        source=_link_locator(link, "source"),
        target=_link_locator(link, "target"),
    )


def _transition_record(
    *,
    transaction_id: str,
    action_request_id: str | None,
    policy_decision_id: str | None,
    before_digest: str,
    after_digest: str,
    committed: bool,
    failure_code: str | None,
    delta: StateDelta,
) -> StateTransitionRecord:
    payload = {
        "schema_version": "office-v2.0",
        "transaction_id": transaction_id,
        "action_request_id": action_request_id,
        "policy_decision_id": policy_decision_id,
        "before_state_digest": before_digest,
        "after_state_digest": after_digest,
        "committed": committed,
        "failure_code": failure_code,
        "state_delta": delta.model_dump(mode="json", exclude_none=False),
    }
    return StateTransitionRecord(**payload, transition_digest=sha256_digest(payload))


class EpisodeWorld:
    """An isolated deterministic working copy of one canonical world."""

    def __init__(self, canonical_world: CanonicalOfficeWorld, *, episode_id: Identifier):
        self.episode_id = _IDENTIFIER_ADAPTER.validate_python(episode_id)
        self.base_world_digest = canonical_world.world_digest
        self._state = OfficeWorldState.model_validate(
            canonical_world.state.model_dump(mode="python", exclude_none=False)
        )
        self._history: list[StateTransitionRecord] = []
        self._active_transaction: EpisodeTransaction | None = None

    @classmethod
    def restore(
        cls,
        *,
        episode_id: Identifier,
        base_world_digest: Sha256Digest,
        state: OfficeWorldState,
        history: tuple[StateTransitionRecord, ...] = (),
        initial_state_digest: Sha256Digest | None = None,
    ) -> EpisodeWorld:
        """Restore one isolated V2 world after validating its transaction chain."""

        restored_state = OfficeWorldState.model_validate(
            state.model_dump(mode="python", exclude_none=False)
        )
        restored_history = tuple(
            StateTransitionRecord.model_validate(
                item.model_dump(mode="python", exclude_none=False)
            )
            for item in history
        )
        current = (
            _SHA256_ADAPTER.validate_python(initial_state_digest)
            if initial_state_digest is not None
            else None
        )
        transaction_ids: set[str] = set()
        for record in restored_history:
            if record.transaction_id in transaction_ids:
                raise ValueError("episode history contains duplicate transaction id")
            transaction_ids.add(record.transaction_id)
            if current is not None and record.before_state_digest != current:
                raise ValueError("episode history is not contiguous")
            current = record.after_state_digest
        state_digest = restored_state.canonical_digest()
        if current is not None and current != state_digest:
            raise ValueError("episode history final digest does not match state")

        episode = cls.__new__(cls)
        episode.episode_id = _IDENTIFIER_ADAPTER.validate_python(episode_id)
        episode.base_world_digest = _SHA256_ADAPTER.validate_python(base_world_digest)
        episode._state = restored_state
        episode._history = list(restored_history)
        episode._active_transaction = None
        return episode

    @property
    def state(self) -> OfficeWorldState:
        return self._state

    @property
    def state_digest(self) -> str:
        return self._state.canonical_digest()

    @property
    def history(self) -> tuple[StateTransitionRecord, ...]:
        return tuple(self._history)

    def begin_transaction(
        self,
        *,
        action_request_id: Identifier | None = None,
        policy_decision_id: Identifier | None = None,
    ) -> EpisodeTransaction:
        if self._active_transaction is not None:
            raise RuntimeError("episode already has an active transaction")
        transaction_id = f"transaction.{self.episode_id}.{len(self._history):06d}"
        transaction = EpisodeTransaction(
            episode=self,
            transaction_id=transaction_id,
            action_request_id=action_request_id,
            policy_decision_id=policy_decision_id,
        )
        self._active_transaction = transaction
        return transaction

    def _finish(
        self,
        transaction: EpisodeTransaction,
        state: OfficeWorldState,
        record: StateTransitionRecord,
    ) -> None:
        if transaction is not self._active_transaction:
            raise RuntimeError("transaction is not active for this episode")
        self._state = state
        self._history.append(record)
        self._active_transaction = None


class EpisodeTransaction:
    """Single-use staging area that commits a complete validated state atomically."""

    def __init__(
        self,
        *,
        episode: EpisodeWorld,
        transaction_id: Identifier,
        action_request_id: Identifier | None,
        policy_decision_id: Identifier | None,
    ):
        self._episode = episode
        self.transaction_id = _IDENTIFIER_ADAPTER.validate_python(transaction_id)
        self.action_request_id = action_request_id
        self.policy_decision_id = policy_decision_id
        self._before = episode.state
        self._staged = OfficeWorldState.model_validate(
            episode.state.model_dump(mode="python", exclude_none=False)
        )
        self._closed = False

    @property
    def staged_state(self) -> OfficeWorldState:
        return self._staged

    def replace_domain_graph(self, domain_graph: OfficeDomainGraph) -> None:
        self._ensure_open()
        self._staged = self._staged.model_copy(update={"domain_graph": domain_graph})

    def replace_policy_rules(self, policy_rules: tuple[EnterprisePolicyRule, ...]) -> None:
        self._ensure_open()
        self._staged = self._staged.model_copy(update={"policy_rules": policy_rules})

    def replace_delegation_grants(
        self, delegation_grants: tuple[DelegationGrant, ...]
    ) -> None:
        self._ensure_open()
        self._staged = self._staged.model_copy(
            update={"delegation_grants": delegation_grants}
        )

    def advance_clock(self, ticks: int = 1) -> None:
        self._ensure_open()
        if ticks <= 0:
            raise ValueError("ticks must be greater than zero")
        clock = LogicalClock(
            now=self._staged.logical_clock.now + ticks,
            timezone=self._staged.logical_clock.timezone,
        )
        self._staged = self._staged.model_copy(update={"logical_clock": clock})

    def allocate_id(self, namespace: Identifier) -> str:
        self._ensure_open()
        normalized_namespace = _IDENTIFIER_ADAPTER.validate_python(namespace)
        sequence = self._staged.next_id_sequence
        allocated = f"{normalized_namespace}.{self._episode.episode_id}.{sequence:06d}"
        _IDENTIFIER_ADAPTER.validate_python(allocated)
        self._staged = self._staged.model_copy(update={"next_id_sequence": sequence + 1})
        return allocated

    def commit(self) -> StateTransitionRecord:
        self._ensure_open()
        try:
            validated = OfficeWorldState.model_validate(
                self._staged.model_dump(mode="python", exclude_none=False)
            )
        except Exception:
            self._rollback_internal("transaction_validation_failed")
            raise
        before_digest = self._before.canonical_digest()
        after_digest = validated.canonical_digest()
        record = _transition_record(
            transaction_id=self.transaction_id,
            action_request_id=self.action_request_id,
            policy_decision_id=self.policy_decision_id,
            before_digest=before_digest,
            after_digest=after_digest,
            committed=True,
            failure_code=None,
            delta=diff_states(self._before, validated),
        )
        self._closed = True
        self._episode._finish(self, validated, record)
        return record

    def rollback(
        self, failure_code: Identifier = "transaction_rolled_back"
    ) -> StateTransitionRecord:
        self._ensure_open()
        return self._rollback_internal(_IDENTIFIER_ADAPTER.validate_python(failure_code))

    def _rollback_internal(self, failure_code: str) -> StateTransitionRecord:
        digest = self._before.canonical_digest()
        record = _transition_record(
            transaction_id=self.transaction_id,
            action_request_id=self.action_request_id,
            policy_decision_id=self.policy_decision_id,
            before_digest=digest,
            after_digest=digest,
            committed=False,
            failure_code=failure_code,
            delta=StateDelta(),
        )
        self._closed = True
        self._episode._finish(self, self._before, record)
        return record

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("transaction is already closed")
