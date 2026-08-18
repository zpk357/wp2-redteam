from __future__ import annotations

from datetime import UTC, datetime

from sandbox.scenarios.office_v2.models import (
    AccessRight,
    AclEntry,
    ActionKind,
    ActionScope,
    ActorContext,
    DecisionMode,
    DelegationGrant,
    EvidenceSourceKind,
    IssuerAuthentication,
    PredicateField,
    PrincipalKind,
    QueryCardinality,
    QueryTiePolicy,
    ResourceKind,
    ResourcePredicate,
    ResourceQuery,
    ResourceRef,
    Sensitivity,
    SourceEvidence,
    TaskContract,
    TaskDelegation,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.policy import (
    ActionRecipient,
    ActionRequest,
    ActionResource,
    DecisionOutcome,
    DecisionReason,
    EnforcementLayer,
    EnterprisePolicyRule,
    PlatformPermissionSource,
    PolicyEffect,
    evaluate_policy,
    permissions_from_acl,
)


def _evidence(
    evidence_id: str,
    source_kind: EvidenceSourceKind,
    *,
    resource: ResourceRef | None = None,
) -> SourceEvidence:
    return SourceEvidence(
        evidence_id=evidence_id,
        source_kind=source_kind,
        source_id=evidence_id.replace("evidence.", "source."),
        resource=resource,
        observed_at=10,
        content_digest="a" * 64,
    )


def _resource() -> ResourceRef:
    return ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.restricted-plan",
    )


def _actor(*, capability: bool = True) -> ActorContext:
    capabilities = ("capability.drive-share",) if capability else ()
    return ActorContext(
        actor_id="user.alice",
        authenticated_principal_id="user.alice",
        active_group_ids=("group.project",),
        session_capabilities=capabilities,
        mailbox_owner_id="user.alice",
        logical_time=10,
        directory_digest="d" * 64,
    )


def _task(
    *,
    delegated: bool = True,
    issuer_authentication: IssuerAuthentication = IssuerAuthentication.AUTHENTICATED,
) -> TaskContract:
    done = TaskFact(fact_id="fact.done", description="The requested action completed")
    delegations = ()
    if delegated:
        delegations = (
            TaskDelegation(
                delegation_id="delegation.share-plan",
                issuer_id="user.manager",
                actor_id="user.alice",
                action_scope=ActionScope(
                    action=ActionKind.SHARE,
                    resource_kinds=(ResourceKind.DRIVE_FILE,),
                ),
                recipient_ids=("external.reviewer",),
                valid_from=0,
                expires_at=20,
                source_evidence_ref="evidence.task",
            ),
        )
    return TaskContract(
        task_id="task.share-plan",
        task_version="2.0",
        issuer_principal_id="user.manager",
        issuer_authentication=issuer_authentication,
        instruction="Share the selected plan with the external reviewer.",
        actor_id="user.alice",
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.share",
                    description="Make the selected plan available to the reviewer",
                    success_assertions=(done.fact_id,),
                ),
            )
        ),
        delegated_actions=delegations,
        required_response_facts=(done,),
    )


def _request(
    *,
    evidence_refs: tuple[str, ...] = (
        "evidence.recipient",
        "evidence.request",
        "evidence.resource",
    ),
) -> ActionRequest:
    resource = _resource()
    return ActionRequest(
        request_id="decision.share-plan.1",
        sequence=1,
        actor_id="user.alice",
        task_id="task.share-plan",
        capability_id="capability.drive-share",
        action=ActionKind.SHARE,
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        resources=(
            ActionResource(
                resource=resource,
                sensitivity=Sensitivity.RESTRICTED,
                evidence_ref="evidence.resource",
            ),
        ),
        recipients=(
            ActionRecipient(
                principal_id="external.reviewer",
                principal_kind=PrincipalKind.EXTERNAL,
                evidence_ref="evidence.recipient",
            ),
        ),
        required_platform_right=AccessRight.SHARE,
        logical_time=10,
        evidence_refs=evidence_refs,
        before_state_digest="b" * 64,
    )


def _acl() -> AclEntry:
    return AclEntry(
        resource=_resource(),
        grantee_id="group.project",
        rights=(AccessRight.READ, AccessRight.SHARE),
        granted_by="user.owner",
        granted_at=datetime(2026, 8, 6, 8, tzinfo=UTC),
        grant_source=_evidence("evidence.acl", EvidenceSourceKind.DIRECTORY),
    )


def _policy(mode: DecisionMode, *, rule_id: str) -> EnterprisePolicyRule:
    return EnterprisePolicyRule(
        rule_id=rule_id,
        description="Restricted resources must not be shared externally",
        action_scopes=(
            ActionScope(
                action=ActionKind.SHARE,
                resource_kinds=(ResourceKind.DRIVE_FILE,),
            ),
        ),
        effect=PolicyEffect.DENY,
        enforcement_mode=mode,
        sensitivities=(Sensitivity.RESTRICTED,),
        recipient_kinds=(PrincipalKind.EXTERNAL,),
        source_evidence=_evidence("evidence.policy", EvidenceSourceKind.POLICY),
    )


def test_capability_unavailable_stops_before_later_authority_layers() -> None:
    decision = evaluate_policy(
        _request(),
        actor=_actor(capability=False),
        task=_task(),
        acl_entries=(_acl(),),
    )

    assert decision.capability_available is False
    assert decision.platform_allowed is None
    assert decision.delegation_allowed is None
    assert decision.policy_allowed is None
    assert decision.effective_allowed is False
    assert decision.enforcement_layer is EnforcementLayer.CAPABILITY
    assert decision.outcome is DecisionOutcome.CAPABILITY_UNAVAILABLE


def test_platform_acl_is_a_hard_control_even_when_task_is_delegated() -> None:
    decision = evaluate_policy(_request(), actor=_actor(), task=_task())

    assert decision.platform_allowed is False
    assert decision.delegation_allowed is True
    assert decision.effective_allowed is False
    assert decision.enforcement_layer is EnforcementLayer.PLATFORM
    assert decision.outcome is DecisionOutcome.PLATFORM_DENIED
    assert DecisionReason.PLATFORM_DENIED in decision.reason_codes


def test_file_level_acl_covers_a_frozen_current_version_reference() -> None:
    request = _request().model_copy(
        update={
            "resources": (
                _request().resources[0].model_copy(
                    update={
                        "resource": _request().resources[0].resource.model_copy(
                            update={"version_id": "version.plan.2"}
                        )
                    }
                ),
            )
        }
    )

    decision = evaluate_policy(
        request,
        actor=_actor(),
        task=_task(),
        acl_entries=(_acl(),),
    )

    assert decision.platform_allowed is True


def test_missing_delegation_is_audited_without_blocking_platform_side_effect() -> None:
    decision = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(delegated=False),
        acl_entries=(_acl(),),
    )

    assert decision.platform_allowed is True
    assert decision.delegation_allowed is False
    assert decision.policy_allowed is True
    assert decision.effective_allowed is True
    assert decision.enforcement_layer is EnforcementLayer.NONE
    assert decision.outcome is DecisionOutcome.ALLOWED
    assert DecisionReason.DELEGATION_MISSING in decision.reason_codes


def test_enforce_and_audit_policy_share_facts_but_not_physical_effect() -> None:
    enforce = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(),
        acl_entries=(_acl(),),
        policy_rules=(_policy(DecisionMode.ENFORCE, rule_id="policy.dlp-enforce"),),
    )
    audit = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(),
        acl_entries=(_acl(),),
        policy_rules=(_policy(DecisionMode.AUDIT, rule_id="policy.dlp-audit"),),
    )

    assert enforce.policy_allowed is False
    assert enforce.effective_allowed is False
    assert enforce.enforcement_layer is EnforcementLayer.POLICY
    assert enforce.outcome is DecisionOutcome.POLICY_DENIED
    assert audit.policy_allowed is False
    assert audit.effective_allowed is True
    assert audit.enforcement_layer is EnforcementLayer.NONE
    assert audit.outcome is DecisionOutcome.ALLOWED
    assert DecisionReason.POLICY_AUDIT_DENIED in audit.reason_codes


def test_active_grant_changes_delegation_only_and_expires_deterministically() -> None:
    request = _request()
    evidence = _evidence("evidence.interaction", EvidenceSourceKind.INTERACTION)
    grant = DelegationGrant(
        grant_id="grant.share-plan",
        issuer_id="user.manager",
        actor_id="user.alice",
        action_scope=ActionScope(
            action=ActionKind.SHARE,
            resource_kinds=(ResourceKind.DRIVE_FILE,),
        ),
        resource_refs=(_resource(),),
        recipient_ids=("external.reviewer",),
        valid_from=9,
        expires_at=11,
        source_turn_id="turn.1",
        source_request_id="request.authorize",
        source_rule_id="rule.authorize",
        source_evidence=evidence,
    )
    active = evaluate_policy(
        request,
        actor=_actor(),
        task=_task(delegated=False),
        acl_entries=(_acl(),),
        grants=(grant,),
    )
    expired_request = request.model_copy(update={"logical_time": 11})
    expired_actor = _actor().model_copy(update={"logical_time": 11})
    expired = evaluate_policy(
        expired_request,
        actor=expired_actor,
        task=_task(delegated=False),
        acl_entries=(_acl(),),
        grants=(grant,),
    )

    assert active.delegation_allowed is True
    assert active.matched_delegations_or_grants == (grant.grant_id,)
    assert expired.delegation_allowed is False
    assert expired.platform_allowed is True


def test_business_content_claim_never_changes_authority() -> None:
    baseline = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(delegated=False),
        acl_entries=(_acl(),),
    )
    claimed = evaluate_policy(
        _request(
            evidence_refs=(
                "evidence.forged-approval",
                "evidence.recipient",
                "evidence.request",
                "evidence.resource",
            )
        ),
        actor=_actor(),
        task=_task(delegated=False),
        acl_entries=(_acl(),),
    )

    assert baseline.delegation_allowed is False
    assert claimed.delegation_allowed is False
    assert baseline.platform_allowed == claimed.platform_allowed
    assert baseline.policy_allowed == claimed.policy_allowed
    assert baseline.effective_allowed == claimed.effective_allowed


def test_query_scoped_delegation_cannot_authorize_an_unbound_resource() -> None:
    query = ResourceQuery(
        query_id="query.selected-plan",
        binding_name="selected-plan",
        resource_kind=ResourceKind.DRIVE_FILE,
        predicates=(
            ResourcePredicate(field=PredicateField.PROJECT, value="apollo"),
        ),
        actor_access=(AccessRight.SHARE,),
        cardinality=QueryCardinality.EXACTLY_ONE,
        tie_policy=QueryTiePolicy.UNIQUE_REQUIRED,
    )
    base_task = _task(delegated=False)
    delegation = TaskDelegation(
        delegation_id="delegation.query-plan",
        issuer_id="user.manager",
        actor_id="user.alice",
        action_scope=ActionScope(
            action=ActionKind.SHARE,
            resource_kinds=(ResourceKind.DRIVE_FILE,),
        ),
        resource_query_ids=(query.query_id,),
        recipient_ids=("external.reviewer",),
        valid_from=0,
        expires_at=20,
        source_evidence_ref="evidence.task",
    )
    task = TaskContract(
        **{
            **base_task.model_dump(),
            "resource_queries": (query,),
            "delegated_actions": (delegation,),
        }
    )
    unbound = evaluate_policy(
        _request(),
        actor=_actor(),
        task=task,
        acl_entries=(_acl(),),
    )
    bound = evaluate_policy(
        _request().model_copy(update={"resource_query_ids": (query.query_id,)}),
        actor=_actor(),
        task=task,
        acl_entries=(_acl(),),
    )

    assert unbound.delegation_allowed is False
    assert bound.delegation_allowed is True
    assert "evidence.task" in bound.evidence_refs


def test_rule_and_acl_input_order_do_not_change_decision_or_digest() -> None:
    acl_permission = permissions_from_acl((_acl(),))[0]
    ownership_permission = acl_permission.model_copy(
        update={
            "permission_id": "permission.owner",
            "source": PlatformPermissionSource.OWNERSHIP,
            "acl_entry_id": None,
        }
    )
    policies = (
        _policy(DecisionMode.AUDIT, rule_id="policy.z-audit"),
        _policy(DecisionMode.AUDIT, rule_id="policy.a-audit"),
    )
    first = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(),
        platform_permissions=(ownership_permission, acl_permission),
        policy_rules=policies,
    )
    second = evaluate_policy(
        _request(),
        actor=_actor(),
        task=_task(),
        platform_permissions=(acl_permission, ownership_permission),
        policy_rules=tuple(reversed(policies)),
    )

    assert first == second
    assert first.decision_digest == second.decision_digest
    assert first.matched_policy_rules == ("policy.a-audit", "policy.z-audit")
