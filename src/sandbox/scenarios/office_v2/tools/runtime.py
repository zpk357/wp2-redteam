"""Single authorization, transaction, provenance, and result pipeline for Office V2."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, JsonValue, ValidationError

from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CONTRACT_VERSION
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    ActorContext,
    EvidenceSourceKind,
    ResolvedBinding,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    SourceEvidence,
    TaskContract,
)
from sandbox.scenarios.office_v2.observation import (
    DriveVersionView,
    ObservationPolicy,
    ObservationQuery,
    ObservedResource,
    observe,
    resource_rights,
)
from sandbox.scenarios.office_v2.policy import (
    ActionRecipient,
    ActionRequest,
    ActionResource,
    DecisionOutcome,
    PlatformPermission,
    PlatformPermissionSource,
    evaluate_policy,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    OfficeToolInvocation,
    OfficeToolResult,
    OutputEvidence,
    ToolFailureCode,
    ToolResultStatus,
    build_tool_result,
)
from sandbox.scenarios.office_v2.tools.provenance import EvidenceLedger, ProvenanceError
from sandbox.scenarios.office_v2.world import EpisodeTransaction, EpisodeWorld


class ToolRejected(ValueError):
    def __init__(self, code: ToolFailureCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class PreparedAction:
    resources: tuple[ResourceRef, ...] = ()
    recipient_ids: tuple[str, ...] = ()
    resource_query_ids: tuple[str, ...] = ()
    action: ActionKind | None = None
    resource_kinds: tuple[ResourceKind, ...] | None = None


PrepareHandler = Callable[["OfficeV2ToolRuntime", BaseModel], PreparedAction]
ExecuteHandler = Callable[
    ["OfficeV2ToolRuntime", BaseModel, EpisodeTransaction | None],
    dict[str, JsonValue],
]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    arguments_model: type[BaseModel]
    action: ActionKind
    capability_id: str
    resource_kinds: tuple[ResourceKind, ...]
    prepare: PrepareHandler
    execute: ExecuteHandler
    writes_state: bool = False


class OfficeV2ToolRuntime:
    """Own one deterministic tool session for one isolated Episode."""

    def __init__(
        self,
        *,
        episode: EpisodeWorld,
        actor: ActorContext,
        task: TaskContract,
        definitions: Mapping[str, ToolDefinition],
        bindings: Iterable[ResolvedBinding] = (),
        binding_world_digest: str | None = None,
        observation_policy: ObservationPolicy | None = None,
    ) -> None:
        if actor.actor_id != task.actor_id:
            raise ValueError("actor and task actor must match")
        if actor.logical_time != episode.state.logical_clock.now:
            raise ValueError("actor logical_time must match episode state")
        if actor.directory_digest != episode.state.domain_graph.directory.canonical_digest():
            raise ValueError("actor directory digest does not match episode state")
        self.episode = episode
        self.actor = actor
        self.task = task
        self.definitions = dict(definitions)
        self.observation_policy = observation_policy or ObservationPolicy()
        self.evidence = EvidenceLedger()
        self._invocations: list[OfficeToolInvocation] = []
        self._results: list[OfficeToolResult] = []
        self._time_origin = _latest_business_time(episode.state)
        expected_binding_world_digest = binding_world_digest or episode.state.canonical_digest()
        for binding in bindings:
            if binding.world_digest != expected_binding_world_digest:
                raise ValueError("binding is stale for episode state")
            self.evidence.seed_binding(binding)

    @property
    def state(self) -> OfficeWorldState:
        return self.episode.state

    @property
    def invocations(self) -> tuple[OfficeToolInvocation, ...]:
        return tuple(self._invocations)

    @property
    def results(self) -> tuple[OfficeToolResult, ...]:
        return tuple(self._results)

    @property
    def logical_datetime(self) -> datetime:
        return self._time_origin + timedelta(seconds=len(self._invocations) + 1)

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        argument_sources: tuple[ArgumentSource, ...] = (),
        tool_contract_version: str = OFFICE_V2_TOOL_CONTRACT_VERSION,
    ) -> OfficeToolResult:
        invocation = self._new_invocation(
            tool_name,
            arguments,
            argument_sources=argument_sources,
            tool_contract_version=tool_contract_version,
        )
        definition = self.definitions.get(tool_name)
        if definition is None:
            return self._finish(
                build_tool_result(
                    invocation=invocation,
                    status=ToolResultStatus.REJECTED,
                    failure_code=ToolFailureCode.UNKNOWN_TOOL,
                )
            )
        if tool_contract_version != OFFICE_V2_TOOL_CONTRACT_VERSION:
            return self._finish(
                build_tool_result(
                    invocation=invocation,
                    status=ToolResultStatus.REJECTED,
                    failure_code=ToolFailureCode.UNSUPPORTED_TOOL_CONTRACT_VERSION,
                )
            )
        try:
            self.evidence.verify_sources(invocation)
            parsed = definition.arguments_model.model_validate(arguments)
        except ProvenanceError as exc:
            return self._rejected(invocation, exc.code)
        except (ValidationError, ValueError, TypeError):
            return self._rejected(invocation, ToolFailureCode.INVALID_ARGUMENTS)

        if definition.capability_id not in self.actor.session_capabilities:
            request = self._action_request(invocation, definition, PreparedAction())
            decision = evaluate_policy(
                request,
                actor=self.actor,
                task=self.task,
                grants=self.state.delegation_grants,
                policy_rules=self.state.policy_rules,
            )
            return self._blocked(invocation, decision)

        try:
            target = definition.prepare(self, parsed)
        except ToolRejected as exc:
            return self._rejected(invocation, exc.code)
        except (ValidationError, ValueError, TypeError):
            return self._rejected(invocation, ToolFailureCode.INVALID_ARGUMENTS)

        request = self._action_request(invocation, definition, target)
        try:
            decision = evaluate_policy(
                request,
                actor=self.actor,
                task=self.task,
                platform_permissions=self._implicit_permissions(request),
                acl_entries=self.state.domain_graph.acl_entries,
                grants=self.state.delegation_grants,
                policy_rules=self.state.policy_rules,
            )
        except Exception:
            return self._failed(invocation, ToolFailureCode.INTERNAL_INTEGRITY_ERROR)
        if not decision.effective_allowed:
            return self._blocked(invocation, decision)

        transaction: EpisodeTransaction | None = None
        try:
            if definition.writes_state:
                transaction = self.episode.begin_transaction(
                    action_request_id=request.request_id,
                    policy_decision_id=decision.decision_id,
                )
            output = definition.execute(self, parsed, transaction)
            transition = transaction.commit() if transaction is not None else None
        except ToolRejected as exc:
            if transaction is not None:
                transition = transaction.rollback(exc.code.value)
                return self._failed(invocation, exc.code, decision, transition)
            return self._rejected(invocation, exc.code, policy_decision=decision)
        except RuntimeError:
            if transaction is not None:
                try:
                    transition = transaction.rollback(ToolFailureCode.TRANSACTION_CONFLICT.value)
                except RuntimeError:
                    transition = None
                return self._failed(
                    invocation,
                    ToolFailureCode.TRANSACTION_CONFLICT,
                    decision,
                    transition,
                )
            return self._failed(invocation, ToolFailureCode.TRANSACTION_CONFLICT, decision)
        except Exception:
            transition = None
            if transaction is not None:
                if self.episode.history and (
                    self.episode.history[-1].transaction_id == transaction.transaction_id
                ):
                    transition = self.episode.history[-1]
                else:
                    try:
                        transition = transaction.rollback(
                            ToolFailureCode.TRANSACTION_VALIDATION_FAILED.value
                        )
                    except RuntimeError:
                        transition = None
            return self._failed(
                invocation,
                ToolFailureCode.TRANSACTION_VALIDATION_FAILED,
                decision,
                transition,
            )

        after_digest = self.state.canonical_digest()
        evidence = _output_evidence(invocation, output)
        result = build_tool_result(
            invocation=invocation,
            status=ToolResultStatus.SUCCEEDED,
            visible_output=output,
            output_evidence=evidence,
            policy_decision=decision,
            state_transition=transition,
            after_state_digest=after_digest,
        )
        self.evidence.add(evidence)
        return self._finish(result)

    def visible_resources(
        self,
        resource_kinds: tuple[ResourceKind, ...],
        *,
        text: str | None = None,
        drive_version_view: DriveVersionView = DriveVersionView.CURRENT,
    ) -> tuple[ObservedResource, ...]:
        items: list[ObservedResource] = []
        token: str | None = None
        while True:
            page = observe(
                self.state,
                self.actor,
                ObservationQuery(
                    resource_kinds=resource_kinds,
                    text=text,
                    drive_version_view=drive_version_view,
                    page_size=self.observation_policy.maximum_page_size,
                    page_token=token,
                ),
                policy=self.observation_policy,
            )
            items.extend(page.items)
            token = page.next_page_token
            if token is None:
                return tuple(items)

    def visible_resource(self, resource: ResourceRef) -> ObservedResource:
        if resource.kind is ResourceKind.DRIVE_FILE and resource.version_id is not None:
            self.visible_resource(
                ResourceRef(
                    kind=ResourceKind.DRIVE_FILE,
                    resource_id=resource.resource_id,
                )
            )
            return self.visible_resource(
                ResourceRef(
                    kind=ResourceKind.DRIVE_FILE_VERSION,
                    resource_id=resource.version_id,
                )
            )
        version_view = (
            DriveVersionView.ALL
            if resource.kind is ResourceKind.DRIVE_FILE_VERSION or resource.version_id is not None
            else DriveVersionView.CURRENT
        )
        for item in self.visible_resources((resource.kind,), drive_version_view=version_view):
            if item.resource == resource:
                return item
            if (
                resource.kind is ResourceKind.DRIVE_FILE
                and item.resource.kind is ResourceKind.DRIVE_FILE
                and item.resource.resource_id == resource.resource_id
                and resource.version_id is None
            ):
                return item
        raise ToolRejected(ToolFailureCode.RESOURCE_NOT_FOUND_OR_HIDDEN)

    def require_visible_refs(
        self, resources: tuple[ResourceRef, ...]
    ) -> tuple[ObservedResource, ...]:
        return tuple(self.visible_resource(item) for item in resources)

    def resolve_principal(self, value: str) -> str:
        normalized = value.casefold()
        matches = tuple(
            principal.principal_id
            for principal in self.state.domain_graph.directory.principals
            if principal.principal_id.casefold() == normalized
            or principal.email.casefold() == normalized
        )
        if len(matches) != 1:
            raise ToolRejected(ToolFailureCode.RECIPIENT_NOT_RESOLVABLE)
        return matches[0]

    def replace_graph(self, transaction: EpisodeTransaction, **changes: object) -> None:
        graph = transaction.staged_state.domain_graph.model_copy(update=changes)
        transaction.replace_domain_graph(graph)

    def paginate(
        self,
        items: tuple[JsonValue, ...],
        *,
        query_payload: dict[str, JsonValue],
        page_size: int,
        page_token: str | None,
    ) -> dict[str, JsonValue]:
        if page_size < 1 or page_size > self.observation_policy.maximum_page_size:
            raise ToolRejected(ToolFailureCode.INVALID_ARGUMENTS)
        state_digest = self.state.canonical_digest()
        actor_digest = self.actor.canonical_digest()
        query_digest = sha256_digest(query_payload)
        offset = 0
        if page_token is not None:
            try:
                padding = "=" * (-len(page_token) % 4)
                raw = base64.b64decode(page_token + padding, altchars=b"-_", validate=True)
                envelope = json.loads(raw)
                payload = envelope["payload"]
                if envelope["payload_digest"] != sha256_digest(payload):
                    raise ValueError("page token digest mismatch")
                if (
                    payload["state_digest"] != state_digest
                    or payload["actor_digest"] != actor_digest
                    or payload["query_digest"] != query_digest
                ):
                    raise ValueError("page token context mismatch")
                offset = int(payload["offset"])
            except Exception as exc:
                raise ToolRejected(ToolFailureCode.INVALID_OR_STALE_PAGE_TOKEN) from exc
        page_items = items[offset : offset + page_size]
        next_offset = offset + len(page_items)
        next_token = None
        if next_offset < len(items):
            payload = {
                "state_digest": state_digest,
                "actor_digest": actor_digest,
                "query_digest": query_digest,
                "offset": next_offset,
            }
            envelope = {
                "payload": payload,
                "payload_digest": sha256_digest(payload),
            }
            next_token = (
                base64.urlsafe_b64encode(canonical_json_bytes(envelope)).decode("ascii").rstrip("=")
            )
        return {
            "items": list(page_items),
            "has_more": next_token is not None,
            "next_page_token": next_token,
        }

    def _new_invocation(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        argument_sources: tuple[ArgumentSource, ...],
        tool_contract_version: str,
    ) -> OfficeToolInvocation:
        sequence = len(self._invocations)
        invocation = OfficeToolInvocation(
            invocation_id=f"invocation.{self.episode.episode_id}.{sequence:06d}",
            sequence=sequence,
            tool_name=tool_name,
            tool_contract_version=tool_contract_version,
            actor_id=self.actor.actor_id,
            task_id=self.task.task_id,
            logical_time=self.actor.logical_time,
            arguments=arguments,
            arguments_digest=sha256_digest(arguments),
            argument_sources=argument_sources,
            before_state_digest=self.state.canonical_digest(),
        )
        self._invocations.append(invocation)
        return invocation

    def _action_request(
        self,
        invocation: OfficeToolInvocation,
        definition: ToolDefinition,
        target: PreparedAction,
    ) -> ActionRequest:
        evidence_refs = [f"evidence.{invocation.invocation_id}.request"]
        resources: list[ActionResource] = []
        for index, resource in enumerate(target.resources):
            evidence_id = f"evidence.{invocation.invocation_id}.resource.{index:03d}"
            evidence_refs.append(evidence_id)
            resources.append(
                ActionResource(
                    resource=resource,
                    sensitivity=self._resource_sensitivity(resource),
                    evidence_ref=evidence_id,
                )
            )
        recipients: list[ActionRecipient] = []
        directory = self.state.domain_graph.directory
        principals = {item.principal_id: item for item in directory.principals}
        for index, principal_id in enumerate(target.recipient_ids):
            principal = principals[principal_id]
            evidence_id = f"evidence.{invocation.invocation_id}.recipient.{index:03d}"
            evidence_refs.append(evidence_id)
            recipients.append(
                ActionRecipient(
                    principal_id=principal_id,
                    principal_kind=principal.kind,
                    organization_id=principal.organization_id,
                    evidence_ref=evidence_id,
                )
            )
        return ActionRequest(
            request_id=f"decision.{self.episode.episode_id}.{invocation.sequence:06d}",
            sequence=invocation.sequence,
            actor_id=self.actor.actor_id,
            task_id=self.task.task_id,
            capability_id=definition.capability_id,
            action=target.action or definition.action,
            resource_kinds=target.resource_kinds or definition.resource_kinds,
            resources=tuple(resources),
            recipients=tuple(recipients),
            resource_query_ids=target.resource_query_ids,
            required_platform_right=_required_right(
                target.action or definition.action, bool(resources)
            ),
            logical_time=self.actor.logical_time,
            evidence_refs=tuple(evidence_refs),
            before_state_digest=invocation.before_state_digest,
        )

    def _resource_sensitivity(self, resource: ResourceRef) -> Sensitivity:
        if resource.kind in {ResourceKind.DRIVE_FILE, ResourceKind.DRIVE_FILE_VERSION}:
            graph = self.state.domain_graph
            file_id = resource.resource_id
            if resource.kind is ResourceKind.DRIVE_FILE_VERSION:
                version = next(
                    item for item in graph.drive.versions if item.version_id == resource.resource_id
                )
                file_id = version.file_id
            return next(
                item.classification for item in graph.drive.files if item.file_id == file_id
            )
        return Sensitivity.INTERNAL

    def _implicit_permissions(self, request: ActionRequest) -> tuple[PlatformPermission, ...]:
        permissions: list[PlatformPermission] = []
        for index, resource in enumerate(request.resource_refs):
            rights = resource_rights(self.state, self.actor, resource)
            if not rights:
                continue
            evidence_id = f"evidence.{request.request_id}.platform.{index:03d}"
            if resource.kind in {ResourceKind.DRIVE_FILE, ResourceKind.DRIVE_FILE_VERSION}:
                drive = self.state.domain_graph.drive
                if resource.kind is ResourceKind.DRIVE_FILE:
                    file = next(
                        (item for item in drive.files if item.file_id == resource.resource_id),
                        None,
                    )
                else:
                    version = next(
                        (
                            item
                            for item in drive.versions
                            if item.version_id == resource.resource_id
                        ),
                        None,
                    )
                    file = (
                        next(
                            (item for item in drive.files if item.file_id == version.file_id),
                            None,
                        )
                        if version is not None
                        else None
                    )
                if file is None or file.classification is not Sensitivity.PUBLIC:
                    continue
                rights = frozenset(
                    right
                    for right in rights
                    if right in {AccessRight.DISCOVER, AccessRight.READ}
                )
                source = PlatformPermissionSource.PUBLIC_CLASSIFICATION
            else:
                source = {
                    ResourceKind.MAIL_MESSAGE: PlatformPermissionSource.MAILBOX,
                    ResourceKind.MAIL_THREAD: PlatformPermissionSource.MAILBOX,
                    ResourceKind.CALENDAR_EVENT: PlatformPermissionSource.ORGANIZER,
                    ResourceKind.WORKSPACE_FILE: PlatformPermissionSource.OWNERSHIP,
                }[resource.kind]
            permissions.append(
                PlatformPermission(
                    permission_id=f"permission.{request.request_id}.{index:03d}",
                    principal_id=self.actor.actor_id,
                    resources=(resource,),
                    rights=tuple(sorted(rights, key=lambda item: item.value)),
                    source=source,
                    source_evidence=SourceEvidence(
                        evidence_id=evidence_id,
                        source_kind=EvidenceSourceKind.CANONICAL_WORLD,
                        source_id=request.request_id,
                        observed_at=request.logical_time,
                        content_digest=sha256_digest(
                            {"resource": resource, "rights": sorted(rights)}
                        ),
                    ),
                )
            )
        return tuple(permissions)

    def _rejected(
        self,
        invocation: OfficeToolInvocation,
        code: ToolFailureCode,
        *,
        policy_decision: Any = None,
    ) -> OfficeToolResult:
        return self._finish(
            build_tool_result(
                invocation=invocation,
                status=ToolResultStatus.REJECTED,
                policy_decision=policy_decision,
                failure_code=code,
            )
        )

    def _blocked(self, invocation: OfficeToolInvocation, decision: Any) -> OfficeToolResult:
        code = {
            DecisionOutcome.CAPABILITY_UNAVAILABLE: ToolFailureCode.CAPABILITY_UNAVAILABLE,
            DecisionOutcome.PLATFORM_DENIED: ToolFailureCode.PLATFORM_DENIED,
            DecisionOutcome.POLICY_DENIED: ToolFailureCode.POLICY_ENFORCED_DENIED,
        }[decision.outcome]
        return self._finish(
            build_tool_result(
                invocation=invocation,
                status=ToolResultStatus.BLOCKED,
                policy_decision=decision,
                failure_code=code,
            )
        )

    def _failed(
        self,
        invocation: OfficeToolInvocation,
        code: ToolFailureCode,
        policy_decision: Any = None,
        transition: Any = None,
    ) -> OfficeToolResult:
        return self._finish(
            build_tool_result(
                invocation=invocation,
                status=ToolResultStatus.FAILED,
                policy_decision=policy_decision,
                state_transition=transition,
                after_state_digest=self.state.canonical_digest(),
                failure_code=code,
            )
        )

    def _finish(self, result: OfficeToolResult) -> OfficeToolResult:
        self._results.append(result)
        return result


def _required_right(action: ActionKind, has_resources: bool) -> AccessRight | None:
    if action is ActionKind.SEND:
        return AccessRight.READ if has_resources else None
    return {
        ActionKind.DISCOVER: AccessRight.DISCOVER,
        ActionKind.READ: AccessRight.READ,
        ActionKind.CREATE: AccessRight.WRITE,
        ActionKind.UPDATE: AccessRight.WRITE,
        ActionKind.SHARE: AccessRight.SHARE,
        ActionKind.DELETE: AccessRight.DELETE,
        ActionKind.MANAGE_PERMISSIONS: AccessRight.MANAGE_PERMISSIONS,
    }[action]


def _latest_business_time(state: OfficeWorldState) -> datetime:
    graph = state.domain_graph
    values = [
        *(item.sent_at for item in graph.mail.messages),
        *(item.created_at for item in graph.drive.versions),
        *(item.start_at for item in graph.calendar.events),
        *(item.updated_at for item in graph.workspace.files),
    ]
    return max(values, default=datetime(2026, 1, 1, tzinfo=UTC))


def _output_evidence(
    invocation: OfficeToolInvocation, output: dict[str, JsonValue]
) -> tuple[OutputEvidence, ...]:
    records: list[OutputEvidence] = []
    root_resource: ResourceRef | None = None
    if "resource" in output:
        try:
            root_resource = ResourceRef.model_validate(output["resource"])
        except Exception:
            root_resource = None

    def walk(value: JsonValue, path: tuple[str, ...], resource: ResourceRef | None) -> None:
        current_resource = resource
        if isinstance(value, dict):
            try:
                current_resource = ResourceRef.model_validate(value)
            except Exception:
                if "resource" in value:
                    try:
                        current_resource = ResourceRef.model_validate(value["resource"])
                    except Exception:
                        current_resource = resource
        if path:
            records.append(
                OutputEvidence(
                    evidence_id=(f"evidence.{invocation.invocation_id}.field.{len(records):04d}"),
                    invocation_id=invocation.invocation_id,
                    invocation_sequence=invocation.sequence,
                    field_path=path,
                    resource_ref=current_resource,
                    value_digest=sha256_digest(value),
                )
            )
        if isinstance(value, dict):
            for key in sorted(value):
                walk(value[key], (*path, key), current_resource)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)), current_resource)

    for key in sorted(output):
        walk(output[key], (key,), root_resource)
    return tuple(records)


__all__ = [
    "OfficeV2ToolRuntime",
    "PreparedAction",
    "ToolDefinition",
    "ToolRejected",
]
