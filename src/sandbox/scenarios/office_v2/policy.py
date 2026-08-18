"""Deterministic, side-effect-free authorization decisions for Office V2."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, ValidationInfo, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    AclEntry,
    ActionKind,
    ActionScope,
    ActorContext,
    CapabilityId,
    DecisionMode,
    DelegationGrant,
    EvidenceId,
    EvidenceSourceKind,
    Identifier,
    IssuerAuthentication,
    LogicalTime,
    OfficeV2Contract,
    PolicyRuleId,
    PrincipalId,
    PrincipalKind,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    Sha256Digest,
    SourceEvidence,
    TaskContract,
    TaskDelegation,
    canonicalize_identifiers,
    canonicalize_resource_refs,
)


class PlatformPermissionSource(StrEnum):
    ACL = "acl"
    OWNERSHIP = "ownership"
    MAILBOX = "mailbox"
    ORGANIZER = "organizer"
    ATTENDANCE = "attendance"
    PUBLIC_CLASSIFICATION = "public_classification"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DecisionOutcome(StrEnum):
    ALLOWED = "allowed"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PLATFORM_DENIED = "platform_denied"
    POLICY_DENIED = "policy_denied"


class EnforcementLayer(StrEnum):
    NONE = "none"
    CAPABILITY = "capability"
    PLATFORM = "platform"
    POLICY = "policy"


class DecisionReason(StrEnum):
    ALLOWED = "allowed"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    DELEGATION_MISSING = "delegation_missing"
    PLATFORM_DENIED = "platform_denied"
    POLICY_AUDIT_DENIED = "policy_audit_denied"
    POLICY_ENFORCED_DENIED = "policy_enforced_denied"


class ActionResource(OfficeV2Contract):
    resource: ResourceRef
    sensitivity: Sensitivity
    evidence_ref: EvidenceId

    def sort_key(self) -> tuple[str, str, str]:
        return self.resource.sort_key()


class ActionRecipient(OfficeV2Contract):
    principal_id: PrincipalId
    principal_kind: PrincipalKind
    organization_id: Identifier | None = None
    evidence_ref: EvidenceId


class ActionRequest(OfficeV2Contract):
    request_id: Identifier
    sequence: int = Field(ge=0)
    actor_id: PrincipalId
    task_id: Identifier
    capability_id: CapabilityId
    action: ActionKind
    resource_kinds: tuple[ResourceKind, ...] = Field(min_length=1)
    resources: tuple[ActionResource, ...] = Field(default_factory=tuple)
    recipients: tuple[ActionRecipient, ...] = Field(default_factory=tuple)
    resource_query_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    required_platform_right: AccessRight | None = None
    logical_time: LogicalTime
    evidence_refs: tuple[EvidenceId, ...] = Field(min_length=1)
    before_state_digest: Sha256Digest

    @field_validator("resource_kinds")
    @classmethod
    def resource_kinds_are_canonical(
        cls, value: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("resources")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ActionResource, ...]
    ) -> tuple[ActionResource, ...]:
        items = tuple(value)
        canonicalize_resource_refs(item.resource for item in items)
        return tuple(sorted(items, key=ActionResource.sort_key))

    @field_validator("recipients")
    @classmethod
    def recipients_are_canonical(
        cls, value: tuple[ActionRecipient, ...]
    ) -> tuple[ActionRecipient, ...]:
        items = tuple(value)
        canonicalize_identifiers(
            (item.principal_id for item in items), field_name="recipient_ids"
        )
        return tuple(sorted(items, key=lambda item: item.principal_id))

    @field_validator("resource_query_ids", "evidence_refs")
    @classmethod
    def identifiers_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @model_validator(mode="after")
    def target_and_access_contract_is_consistent(self) -> ActionRequest:
        if any(item.resource.kind not in self.resource_kinds for item in self.resources):
            raise ValueError("resource is outside declared resource_kinds")
        expected_right = {
            ActionKind.DISCOVER: AccessRight.DISCOVER,
            ActionKind.READ: AccessRight.READ,
            ActionKind.CREATE: AccessRight.WRITE,
            ActionKind.UPDATE: AccessRight.WRITE,
            ActionKind.SHARE: AccessRight.SHARE,
            ActionKind.DELETE: AccessRight.DELETE,
            ActionKind.MANAGE_PERMISSIONS: AccessRight.MANAGE_PERMISSIONS,
        }.get(self.action)
        if self.action is ActionKind.SEND:
            expected_right = AccessRight.READ if self.resources else None
        if self.required_platform_right is not expected_right:
            raise ValueError("required_platform_right does not match action semantics")
        target_evidence = {
            *(item.evidence_ref for item in self.resources),
            *(item.evidence_ref for item in self.recipients),
        }
        if not target_evidence.issubset(self.evidence_refs):
            raise ValueError("target evidence must be included in evidence_refs")
        return self

    @property
    def resource_refs(self) -> tuple[ResourceRef, ...]:
        return tuple(item.resource for item in self.resources)

    @property
    def recipient_ids(self) -> tuple[str, ...]:
        return tuple(item.principal_id for item in self.recipients)


class PlatformPermission(OfficeV2Contract):
    permission_id: Identifier
    principal_id: PrincipalId
    resources: tuple[ResourceRef, ...] = Field(min_length=1)
    rights: tuple[AccessRight, ...] = Field(min_length=1)
    source: PlatformPermissionSource
    source_evidence: SourceEvidence
    acl_entry_id: Identifier | None = None
    valid_from: LogicalTime = 0
    valid_until: LogicalTime | None = None

    @field_validator("resources")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("rights")
    @classmethod
    def rights_are_canonical(
        cls, value: tuple[AccessRight, ...]
    ) -> tuple[AccessRight, ...]:
        if len(value) != len(set(value)):
            raise ValueError("rights must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def source_and_window_are_consistent(self) -> PlatformPermission:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.source is PlatformPermissionSource.ACL:
            if self.acl_entry_id is None:
                raise ValueError("ACL permission requires acl_entry_id")
        elif self.acl_entry_id is not None:
            raise ValueError("non-ACL permission must not define acl_entry_id")
        return self

    def is_active(self, logical_time: LogicalTime) -> bool:
        return self.valid_from <= logical_time and (
            self.valid_until is None or logical_time < self.valid_until
        )


class EnterprisePolicyRule(OfficeV2Contract):
    rule_id: PolicyRuleId
    description: str = Field(min_length=1, max_length=512)
    action_scopes: tuple[ActionScope, ...] = Field(min_length=1)
    effect: PolicyEffect
    enforcement_mode: DecisionMode
    sensitivities: tuple[Sensitivity, ...] = Field(default_factory=tuple)
    recipient_kinds: tuple[PrincipalKind, ...] = Field(default_factory=tuple)
    recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    valid_from: LogicalTime = 0
    valid_until: LogicalTime | None = None
    source_evidence: SourceEvidence

    @field_validator("action_scopes")
    @classmethod
    def scopes_are_canonical(
        cls, value: tuple[ActionScope, ...]
    ) -> tuple[ActionScope, ...]:
        items = tuple(value)
        keys = tuple(item.sort_key() for item in items)
        if len(keys) != len(set(keys)):
            raise ValueError("action_scopes must not contain duplicates")
        return tuple(sorted(items, key=ActionScope.sort_key))

    @field_validator("sensitivities", "recipient_kinds")
    @classmethod
    def enums_are_canonical(cls, value: tuple[StrEnum, ...]) -> tuple[StrEnum, ...]:
        if len(value) != len(set(value)):
            raise ValueError("policy enum constraints must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("recipient_ids")
    @classmethod
    def recipient_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="recipient_ids")

    @model_validator(mode="after")
    def source_and_window_are_consistent(self) -> EnterprisePolicyRule:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.source_evidence.source_kind is not EvidenceSourceKind.POLICY:
            raise ValueError("enterprise policy requires policy evidence")
        return self

    def is_active(self, logical_time: LogicalTime) -> bool:
        return self.valid_from <= logical_time and (
            self.valid_until is None or logical_time < self.valid_until
        )

    def matches(self, request: ActionRequest) -> bool:
        if not self.is_active(request.logical_time):
            return False
        request_kinds = set(request.resource_kinds)
        if not any(
            scope.action is request.action
            and request_kinds.issubset(scope.resource_kinds)
            for scope in self.action_scopes
        ):
            return False
        if self.sensitivities and not any(
            resource.sensitivity in self.sensitivities for resource in request.resources
        ):
            return False
        if self.recipient_kinds and not any(
            recipient.principal_kind in self.recipient_kinds
            for recipient in request.recipients
        ):
            return False
        return not self.recipient_ids or bool(
            set(request.recipient_ids).intersection(self.recipient_ids)
        )


class PolicyDecision(OfficeV2Contract):
    decision_id: Identifier
    sequence: int = Field(ge=0)
    actor_id: PrincipalId
    task_id: Identifier
    capability_id: CapabilityId
    action: ActionKind
    resource_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    capability_available: bool
    platform_allowed: bool | None
    delegation_allowed: bool | None
    policy_allowed: bool | None
    policy_enforcement_mode: DecisionMode | None
    effective_allowed: bool
    enforcement_layer: EnforcementLayer
    outcome: DecisionOutcome
    reason_codes: tuple[DecisionReason, ...] = Field(min_length=1)
    matched_acl_entries: tuple[Identifier, ...] = Field(default_factory=tuple)
    matched_platform_permissions: tuple[Identifier, ...] = Field(default_factory=tuple)
    matched_delegations_or_grants: tuple[Identifier, ...] = Field(default_factory=tuple)
    matched_policy_rules: tuple[PolicyRuleId, ...] = Field(default_factory=tuple)
    evaluated_rule_ids: tuple[PolicyRuleId, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[EvidenceId, ...] = Field(default_factory=tuple)
    before_state_digest: Sha256Digest
    decision_digest: Sha256Digest

    @field_validator("resource_refs")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator(
        "recipient_ids",
        "matched_acl_entries",
        "matched_platform_permissions",
        "matched_delegations_or_grants",
        "matched_policy_rules",
        "evaluated_rule_ids",
        "evidence_refs",
    )
    @classmethod
    def identifiers_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @field_validator("reason_codes")
    @classmethod
    def reasons_are_canonical(
        cls, value: tuple[DecisionReason, ...]
    ) -> tuple[DecisionReason, ...]:
        if len(value) != len(set(value)):
            raise ValueError("reason_codes must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def digest_and_layer_state_are_consistent(self) -> PolicyDecision:
        payload = self.model_dump(mode="json", exclude={"decision_digest"})
        if self.decision_digest != sha256_digest(payload):
            raise ValueError("decision_digest does not match decision payload")
        if not self.capability_available and any(
            value is not None
            for value in (
                self.platform_allowed,
                self.delegation_allowed,
                self.policy_allowed,
            )
        ):
            raise ValueError("unavailable capability must leave later layers unevaluated")
        return self


def permissions_from_acl(entries: Iterable[AclEntry]) -> tuple[PlatformPermission, ...]:
    permissions = []
    for entry in entries:
        digest_hex = entry.canonical_digest().removeprefix("sha256:")
        acl_entry_id = f"acl.{digest_hex}"
        permissions.append(
            PlatformPermission(
                permission_id=f"permission.{digest_hex}",
                principal_id=entry.grantee_id,
                resources=(entry.resource,),
                rights=entry.rights,
                source=PlatformPermissionSource.ACL,
                source_evidence=entry.grant_source,
                acl_entry_id=acl_entry_id,
            )
        )
    return tuple(sorted(permissions, key=lambda item: item.permission_id))


def evaluate_policy(
    request: ActionRequest,
    *,
    actor: ActorContext,
    task: TaskContract,
    platform_permissions: Iterable[PlatformPermission] = (),
    acl_entries: Iterable[AclEntry] = (),
    grants: Iterable[DelegationGrant] = (),
    policy_rules: Iterable[EnterprisePolicyRule] = (),
) -> PolicyDecision:
    """Evaluate all authority layers without mutating world or request state."""

    if request.actor_id != actor.actor_id or request.actor_id != task.actor_id:
        raise ValueError("request, actor context, and task actor must match")
    if request.task_id != task.task_id:
        raise ValueError("request task_id must match task")
    if request.logical_time != actor.logical_time:
        raise ValueError("request logical_time must match actor context")

    capability_available = request.capability_id in actor.session_capabilities
    if not capability_available:
        return _build_decision(
            request,
            capability_available=False,
            platform_allowed=None,
            delegation_allowed=None,
            policy_allowed=None,
            policy_enforcement_mode=None,
            effective_allowed=False,
            enforcement_layer=EnforcementLayer.CAPABILITY,
            outcome=DecisionOutcome.CAPABILITY_UNAVAILABLE,
            reason_codes=(DecisionReason.CAPABILITY_UNAVAILABLE,),
        )

    permissions = tuple(platform_permissions) + permissions_from_acl(acl_entries)
    permissions = tuple(sorted(permissions, key=lambda item: item.permission_id))
    principal_ids = {actor.actor_id, *actor.active_group_ids}
    required_right = request.required_platform_right
    matching_permissions = tuple(
        permission
        for permission in permissions
        if permission.principal_id in principal_ids
        and permission.is_active(request.logical_time)
        and (required_right is None or required_right in permission.rights)
        and any(
            _resource_scope_covers(granted, requested)
            for granted in permission.resources
            for requested in request.resource_refs
        )
    )
    if required_right is None or not request.resource_refs:
        platform_allowed = True
    else:
        platform_allowed = all(
            any(
                _resource_scope_covers(granted, requested)
                for permission in matching_permissions
                for granted in permission.resources
            )
            for requested in request.resource_refs
        )

    active_delegations = tuple(
        delegation
        for delegation in task.delegated_actions
        if task.issuer_authentication is IssuerAuthentication.AUTHENTICATED
        and delegation.valid_from <= request.logical_time < delegation.expires_at
        and _scope_covers_request(delegation.action_scope, request)
    )
    active_grants = tuple(
        grant
        for grant in grants
        if grant.actor_id == actor.actor_id
        and grant.is_active(request.logical_time)
        and _scope_covers_request(grant.action_scope, request)
    )
    delegation_allowed, authority_ids = _delegation_coverage(
        request, active_delegations, active_grants
    )

    rules = tuple(sorted(policy_rules, key=lambda item: item.rule_id))
    matched_rules = tuple(rule for rule in rules if rule.matches(request))
    denied_rules = tuple(rule for rule in matched_rules if rule.effect is PolicyEffect.DENY)
    policy_allowed = not denied_rules
    enforce_denials = tuple(
        rule for rule in denied_rules if rule.enforcement_mode is DecisionMode.ENFORCE
    )
    audit_denials = tuple(
        rule for rule in denied_rules if rule.enforcement_mode is DecisionMode.AUDIT
    )
    policy_enforcement_mode = (
        DecisionMode.ENFORCE
        if enforce_denials
        else DecisionMode.AUDIT
        if audit_denials
        else None
    )

    reason_codes: list[DecisionReason] = []
    if not platform_allowed:
        reason_codes.append(DecisionReason.PLATFORM_DENIED)
    if not delegation_allowed:
        reason_codes.append(DecisionReason.DELEGATION_MISSING)
    if enforce_denials:
        reason_codes.append(DecisionReason.POLICY_ENFORCED_DENIED)
    elif audit_denials:
        reason_codes.append(DecisionReason.POLICY_AUDIT_DENIED)
    if not reason_codes:
        reason_codes.append(DecisionReason.ALLOWED)

    if not platform_allowed:
        enforcement_layer = EnforcementLayer.PLATFORM
        outcome = DecisionOutcome.PLATFORM_DENIED
        effective_allowed = False
    elif enforce_denials:
        enforcement_layer = EnforcementLayer.POLICY
        outcome = DecisionOutcome.POLICY_DENIED
        effective_allowed = False
    else:
        enforcement_layer = EnforcementLayer.NONE
        outcome = DecisionOutcome.ALLOWED
        effective_allowed = True

    matched_acl_ids = tuple(
        permission.acl_entry_id
        for permission in matching_permissions
        if permission.acl_entry_id is not None
    )
    evidence_refs = {
        *request.evidence_refs,
        *(permission.source_evidence.evidence_id for permission in matching_permissions),
        *(item.source_evidence_ref for item in active_delegations),
        *(item.source_evidence.evidence_id for item in active_grants),
        *(rule.source_evidence.evidence_id for rule in matched_rules),
    }
    return _build_decision(
        request,
        capability_available=True,
        platform_allowed=platform_allowed,
        delegation_allowed=delegation_allowed,
        policy_allowed=policy_allowed,
        policy_enforcement_mode=policy_enforcement_mode,
        effective_allowed=effective_allowed,
        enforcement_layer=enforcement_layer,
        outcome=outcome,
        reason_codes=tuple(reason_codes),
        matched_acl_entries=matched_acl_ids,
        matched_platform_permissions=tuple(
            permission.permission_id for permission in matching_permissions
        ),
        matched_delegations_or_grants=authority_ids,
        matched_policy_rules=tuple(rule.rule_id for rule in matched_rules),
        evaluated_rule_ids=tuple(rule.rule_id for rule in rules),
        evidence_refs=tuple(evidence_refs),
    )


def _scope_covers_request(scope: ActionScope, request: ActionRequest) -> bool:
    return scope.action is request.action and set(request.resource_kinds).issubset(
        scope.resource_kinds
    )


def _delegation_coverage(
    request: ActionRequest,
    delegations: Iterable[TaskDelegation],
    grants: Iterable[DelegationGrant],
) -> tuple[bool, tuple[str, ...]]:
    delegation_items = tuple(delegations)
    grant_items = tuple(grants)
    covered_queries = {
        query_id for item in delegation_items for query_id in item.resource_query_ids
    }
    delegation_query_allowed = not request.resource_query_ids or set(
        request.resource_query_ids
    ).issubset(covered_queries)
    broad_delegation = any(not item.resource_query_ids for item in delegation_items)
    delegation_resource_allowed = (
        not request.resource_refs
        or broad_delegation
        or (bool(request.resource_query_ids) and delegation_query_allowed)
    )
    delegation_recipient_allowed = not request.recipient_ids or set(
        request.recipient_ids
    ).issubset(
        {
            recipient
            for item in delegation_items
            for recipient in item.recipient_ids
        }
    )
    delegation_allowed = (
        bool(delegation_items)
        and delegation_query_allowed
        and delegation_resource_allowed
        and delegation_recipient_allowed
    )

    grant_resource_allowed = not request.resource_refs or all(
        any(
            _resource_scope_covers(granted, requested)
            for item in grant_items
            for granted in item.resource_refs
        )
        for requested in request.resource_refs
    )
    grant_recipient_allowed = not request.recipient_ids or set(
        request.recipient_ids
    ).issubset(
        {recipient for item in grant_items for recipient in item.recipient_ids}
    )
    grant_allowed = (
        bool(grant_items) and grant_resource_allowed and grant_recipient_allowed
    )
    matched_ids = tuple(
        sorted(
            [item.delegation_id for item in delegation_items]
            + [item.grant_id for item in grant_items]
        )
    )
    return delegation_allowed or grant_allowed, matched_ids


def _resource_scope_covers(granted: ResourceRef, requested: ResourceRef) -> bool:
    if granted.kind is not requested.kind or granted.resource_id != requested.resource_id:
        return False
    if granted.kind is not ResourceKind.DRIVE_FILE:
        return granted == requested
    return granted.version_id is None or granted.version_id == requested.version_id


def _build_decision(
    request: ActionRequest,
    *,
    capability_available: bool,
    platform_allowed: bool | None,
    delegation_allowed: bool | None,
    policy_allowed: bool | None,
    policy_enforcement_mode: DecisionMode | None,
    effective_allowed: bool,
    enforcement_layer: EnforcementLayer,
    outcome: DecisionOutcome,
    reason_codes: tuple[DecisionReason, ...],
    matched_acl_entries: tuple[str, ...] = (),
    matched_platform_permissions: tuple[str, ...] = (),
    matched_delegations_or_grants: tuple[str, ...] = (),
    matched_policy_rules: tuple[str, ...] = (),
    evaluated_rule_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
) -> PolicyDecision:
    payload = {
        "schema_version": request.schema_version,
        "decision_id": request.request_id,
        "sequence": request.sequence,
        "actor_id": request.actor_id,
        "task_id": request.task_id,
        "capability_id": request.capability_id,
        "action": request.action,
        "resource_refs": request.resource_refs,
        "recipient_ids": request.recipient_ids,
        "capability_available": capability_available,
        "platform_allowed": platform_allowed,
        "delegation_allowed": delegation_allowed,
        "policy_allowed": policy_allowed,
        "policy_enforcement_mode": policy_enforcement_mode,
        "effective_allowed": effective_allowed,
        "enforcement_layer": enforcement_layer,
        "outcome": outcome,
        "reason_codes": tuple(sorted(reason_codes, key=lambda item: item.value)),
        "matched_acl_entries": tuple(sorted(matched_acl_entries)),
        "matched_platform_permissions": tuple(sorted(matched_platform_permissions)),
        "matched_delegations_or_grants": tuple(
            sorted(matched_delegations_or_grants)
        ),
        "matched_policy_rules": tuple(sorted(matched_policy_rules)),
        "evaluated_rule_ids": tuple(sorted(evaluated_rule_ids)),
        "evidence_refs": tuple(sorted(evidence_refs)),
        "before_state_digest": request.before_state_digest,
    }
    digest_payload = PolicyDecision.model_construct(
        **payload, decision_digest="0" * 64
    ).model_dump(mode="json", exclude={"decision_digest"})
    return PolicyDecision(**payload, decision_digest=sha256_digest(digest_payload))


__all__ = [
    "ActionRecipient",
    "ActionRequest",
    "ActionResource",
    "DecisionOutcome",
    "DecisionReason",
    "EnforcementLayer",
    "EnterprisePolicyRule",
    "PlatformPermission",
    "PlatformPermissionSource",
    "PolicyDecision",
    "PolicyEffect",
    "evaluate_policy",
    "permissions_from_acl",
]
