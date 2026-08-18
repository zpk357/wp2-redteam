"""Pure compatibility solving for Office V2 adversarial composition."""

from __future__ import annotations

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.adversarial_conditions import (
    ConditionConstructionError,
    authoritative_absence_assertions,
    derive_direct_task,
)
from sandbox.scenarios.office_v2.attack_models import (
    AdversarialCondition,
    AttackEntryKind,
    AttackObjectiveTemplate,
    CompatibilityDecision,
    CompatibilityPurpose,
    CompatibilityReasonCode,
    CompatibilityStatus,
    ContentPlacement,
    DirectTaskCondition,
    ForgedAuthorizationCondition,
    ObjectiveBindingKind,
    ObjectiveResolvedBinding,
    ParameterSourceManipulationCondition,
    PolicyFeasibilityFact,
    ReachableAttackSurface,
    ReachableContentField,
)
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    EvidenceSourceKind,
    PrincipalKind,
    PrincipalStatus,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    SourceEvidence,
    TaskContract,
)
from sandbox.scenarios.office_v2.observation import resource_rights
from sandbox.scenarios.office_v2.policy import (
    ActionRecipient,
    ActionRequest,
    ActionResource,
    PlatformPermission,
    PlatformPermissionSource,
    evaluate_policy,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions


def solve_compatibility(
    parent: CleanCaseMaterialization,
    objective: AttackObjectiveTemplate,
    condition: AdversarialCondition,
    *,
    state: OfficeWorldState,
    surface: ReachableAttackSurface | None,
    purpose: CompatibilityPurpose = CompatibilityPurpose.REALIZED_WITNESS,
) -> CompatibilityDecision:
    """Resolve one objective/entry/case combination without changing any input."""

    identity_reason = _identity_reason(parent, objective, condition, state, surface)
    if identity_reason is not None:
        return _decision(parent, objective, condition, purpose, identity_reason)
    if condition.entry_kind not in objective.compatible_entry_kinds:
        return _decision(
            parent,
            objective,
            condition,
            purpose,
            CompatibilityReasonCode.ENTRY_KIND_NOT_SUPPORTED,
        )

    selected, field_reason = _selected_fields(condition, surface)
    if field_reason is not None:
        return _decision(parent, objective, condition, purpose, field_reason)
    semantic_reason = _entry_semantic_reason(parent, condition, state)
    if semantic_reason is not None:
        return _decision(
            parent,
            objective,
            condition,
            purpose,
            semantic_reason,
            selected=selected,
        )
    effective_task = (
        derive_direct_task(
            parent,
            state=state,
            instruction=condition.instruction_variant,
            issuer_override=condition.issuer_override,
            issuer_authentication=condition.issuer_authentication,
        )
        if isinstance(condition, DirectTaskCondition)
        else parent.task
    )

    bindings = _resolve_bindings(parent, objective, state, surface, purpose)
    if bindings is None:
        return _decision(
            parent,
            objective,
            condition,
            purpose,
            CompatibilityReasonCode.OBJECTIVE_BINDING_UNRESOLVED,
            selected=selected,
        )

    feasibility, reason = _policy_feasibility(parent, effective_task, objective, bindings, state)
    if reason is None:
        blocked = any(not item.effective_allowed for item in feasibility)
        if purpose is CompatibilityPurpose.REALIZED_WITNESS and blocked:
            if any(not item.capability_available for item in feasibility):
                reason = CompatibilityReasonCode.ACTOR_CAPABILITY_MISSING
            elif any(item.platform_allowed is False for item in feasibility):
                reason = CompatibilityReasonCode.PLATFORM_EFFECT_UNREACHABLE
            else:
                reason = CompatibilityReasonCode.POLICY_ENFORCE_BLOCKS_REQUIRED_MILESTONE
        elif purpose is CompatibilityPurpose.BLOCKED_CALIBRATION and not blocked:
            reason = CompatibilityReasonCode.PLATFORM_EFFECT_UNREACHABLE

    evidence = tuple(
        sorted(
            {
                *(ref for item in bindings for ref in item.evidence_refs),
                *(ref for item in selected for ref in item.evidence_refs),
                *(ref for item in feasibility for ref in item.evidence_refs),
            }
        )
    )
    return _decision(
        parent,
        objective,
        condition,
        purpose,
        reason or CompatibilityReasonCode.COMPATIBLE,
        bindings=bindings,
        selected=selected,
        evidence=evidence,
        feasibility=feasibility,
    )


def _identity_reason(
    parent: CleanCaseMaterialization,
    objective: AttackObjectiveTemplate,
    condition: AdversarialCondition,
    state: OfficeWorldState,
    surface: ReachableAttackSurface | None,
) -> CompatibilityReasonCode | None:
    if (
        condition.parent_case_id != parent.case_id
        or condition.objective_id != objective.objective_id
    ):
        return CompatibilityReasonCode.WORLD_OR_CATALOG_IDENTITY_MISMATCH
    frozen_state_digests = {item.world_digest for item in parent.resolved_bindings}
    if frozen_state_digests != {state.canonical_digest()}:
        return CompatibilityReasonCode.WORLD_OR_CATALOG_IDENTITY_MISMATCH
    if condition.entry_kind is AttackEntryKind.DIRECT_TASK:
        return (
            None if surface is None else CompatibilityReasonCode.WORLD_OR_CATALOG_IDENTITY_MISMATCH
        )
    if surface is None:
        return CompatibilityReasonCode.REACHABLE_FIELD_MISSING
    if (
        surface.case_id != parent.case_id
        or surface.case_digest != parent.case_digest
        or surface.world_digest != parent.base_world_digest
        or condition.reachable_surface_digest != surface.surface_digest
    ):
        return CompatibilityReasonCode.WORLD_OR_CATALOG_IDENTITY_MISMATCH
    return None


def _selected_fields(
    condition: AdversarialCondition, surface: ReachableAttackSurface | None
) -> tuple[tuple[ReachableContentField, ...], CompatibilityReasonCode | None]:
    if isinstance(condition, DirectTaskCondition):
        return (), None
    assert surface is not None
    if isinstance(condition, ForgedAuthorizationCondition):
        placements = condition.claim_placements
    elif isinstance(condition, ParameterSourceManipulationCondition):
        placements = (condition.source_placement, *condition.verification_sources)
    else:
        placements = condition.placements
    by_key = {(field.resource_ref.sort_key(), field.field_path): field for field in surface.fields}
    result = []
    for placement in placements:
        field = by_key.get(_placement_key(placement))
        if field is None:
            return (), CompatibilityReasonCode.REACHABLE_FIELD_MISSING
        if condition.entry_kind not in field.compatible_entry_kinds:
            return (), CompatibilityReasonCode.FIELD_OPERATION_INCOMPATIBLE
        result.append(field)
    unique = {item.sort_key(): item for item in result}
    return tuple(unique[key] for key in sorted(unique)), None


def _entry_semantic_reason(
    parent: CleanCaseMaterialization,
    condition: AdversarialCondition,
    state: OfficeWorldState,
) -> CompatibilityReasonCode | None:
    try:
        if isinstance(condition, DirectTaskCondition):
            derive_direct_task(
                parent,
                state=state,
                instruction=condition.instruction_variant,
                issuer_override=condition.issuer_override,
                issuer_authentication=condition.issuer_authentication,
            )
        elif isinstance(condition, ForgedAuthorizationCondition):
            authoritative_absence_assertions(
                parent,
                state=state,
                claimed_action_scope=condition.claimed_action_scope,
                claimed_resource_refs=condition.claimed_resource_refs,
                claimed_recipient_ids=condition.claimed_recipient_ids,
            )
        elif (
            isinstance(condition, ParameterSourceManipulationCondition)
            and not condition.verification_sources
        ):
            return CompatibilityReasonCode.VERIFICATION_SOURCE_MISSING
    except ConditionConstructionError as exc:
        return CompatibilityReasonCode(str(exc))
    return None


def _resolve_bindings(
    parent: CleanCaseMaterialization,
    objective: AttackObjectiveTemplate,
    state: OfficeWorldState,
    surface: ReachableAttackSurface | None,
    purpose: CompatibilityPurpose,
) -> tuple[ObjectiveResolvedBinding, ...] | None:
    candidates = _resource_candidates(parent, state, surface)
    used: set[tuple[str, str, str]] = set()
    bindings: list[ObjectiveResolvedBinding] = []
    for slot in objective.required_binding_slots:
        if slot.binding_kind is ObjectiveBindingKind.PRINCIPAL:
            principals = tuple(
                item
                for item in state.domain_graph.directory.principals
                if item.status is PrincipalStatus.ACTIVE
                and item.kind in slot.principal_kinds
                and item.principal_id != parent.actor.actor_id
            )
            principals = tuple(
                sorted(
                    principals,
                    key=lambda item: (item.kind is not PrincipalKind.EXTERNAL, item.principal_id),
                )
            )
            if not principals:
                return None
            principal = principals[0]
            bindings.append(
                ObjectiveResolvedBinding(
                    slot_id=slot.slot_id,
                    principal_ids=(principal.principal_id,),
                    evidence_refs=(f"directory.principal.{principal.principal_id}",),
                )
            )
            continue
        if not slot.required_before:
            bindings.append(
                ObjectiveResolvedBinding(
                    slot_id=slot.slot_id,
                    output_resource_kind=slot.resource_kinds[0],
                    evidence_refs=(f"objective.output.{slot.slot_id}",),
                )
            )
            continue
        required_rights = (
            _slot_required_rights(objective, slot.slot_id)
            if purpose is CompatibilityPurpose.REALIZED_WITNESS
            else frozenset({AccessRight.READ})
        )
        match = next(
            (
                ref
                for ref in candidates
                if ref.kind in slot.resource_kinds
                and ref.sort_key() not in used
                and required_rights.issubset(resource_rights(state, parent.actor, ref))
            ),
            None,
        )
        if match is None:
            return None
        used.add(match.sort_key())
        bindings.append(
            ObjectiveResolvedBinding(
                slot_id=slot.slot_id,
                resource_refs=(match,),
                evidence_refs=(f"objective.resource.{slot.slot_id}",),
            )
        )
    return tuple(bindings)


def _resource_candidates(
    parent: CleanCaseMaterialization,
    state: OfficeWorldState,
    surface: ReachableAttackSurface | None,
) -> tuple[ResourceRef, ...]:
    preferred = [ref for binding in parent.resolved_bindings for ref in binding.resource_refs]
    reachable = [] if surface is None else [item.resource_ref for item in surface.fields]
    graph = state.domain_graph
    all_refs = [
        *(
            ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id=item.message_id)
            for item in graph.mail.messages
        ),
        *(
            ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id=item.file_id)
            for item in graph.drive.files
        ),
        *(
            ResourceRef(kind=ResourceKind.CALENDAR_EVENT, resource_id=item.event_id)
            for item in graph.calendar.events
        ),
        *(
            ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=item.path)
            for item in graph.workspace.files
        ),
    ]
    ordered = (*preferred, *reachable, *sorted(all_refs, key=ResourceRef.sort_key))
    unique: dict[tuple[str, str, str], ResourceRef] = {}
    for ref in ordered:
        unique.setdefault(ref.sort_key(), ref)
    return tuple(unique.values())


def _slot_required_rights(
    objective: AttackObjectiveTemplate, slot_id: str
) -> frozenset[AccessRight]:
    rights = {
        _required_right(assertion.action_kinds[0], has_resource=True)
        for assertion in objective.attempted_assertions
        if slot_id in assertion.binding_slots
        and assertion.action_kinds
        and assertion.action_kinds[0] is not ActionKind.SEND
    }
    return frozenset(item for item in rights if item is not None)


def _policy_feasibility(
    parent: CleanCaseMaterialization,
    task: TaskContract,
    objective: AttackObjectiveTemplate,
    bindings: tuple[ObjectiveResolvedBinding, ...],
    state: OfficeWorldState,
) -> tuple[tuple[PolicyFeasibilityFact, ...], CompatibilityReasonCode | None]:
    tools = office_v2_tool_definitions()
    by_slot = {item.slot_id: item for item in bindings}
    facts = []
    for sequence, assertion in enumerate(objective.attempted_assertions):
        if not assertion.tool_names or not assertion.action_kinds:
            return (), CompatibilityReasonCode.OBJECTIVE_PRECONDITION_UNSATISFIED
        tool_name = assertion.tool_names[0]
        definition = tools.get(tool_name)
        if definition is None:
            return (), CompatibilityReasonCode.OBJECTIVE_PRECONDITION_UNSATISFIED
        action = assertion.action_kinds[0]
        refs = tuple(
            ref
            for slot_id in assertion.binding_slots
            for ref in by_slot[slot_id].resource_refs
            if ref.kind in assertion.resource_kinds
        )
        principal_ids = tuple(
            principal_id
            for slot_id in assertion.binding_slots
            for principal_id in by_slot[slot_id].principal_ids
        )
        request_id = f"compat.{objective.objective_id}.{sequence:03d}"
        resources = tuple(
            ActionResource(
                resource=ref,
                sensitivity=_sensitivity(state, ref),
                evidence_ref=f"evidence.{request_id}.resource.{index:03d}",
            )
            for index, ref in enumerate(refs)
        )
        recipients = tuple(
            _recipient(state, principal_id, request_id, index)
            for index, principal_id in enumerate(principal_ids)
        )
        evidence = tuple(
            sorted(
                {
                    f"evidence.{request_id}.compatibility",
                    *(item.evidence_ref for item in resources),
                    *(item.evidence_ref for item in recipients),
                }
            )
        )
        request = ActionRequest(
            request_id=request_id,
            sequence=sequence,
            actor_id=parent.actor.actor_id,
            task_id=task.task_id,
            capability_id=definition.capability_id,
            action=action,
            resource_kinds=assertion.resource_kinds,
            resources=resources,
            recipients=recipients,
            required_platform_right=_required_right(action, bool(resources)),
            logical_time=parent.actor.logical_time,
            evidence_refs=evidence,
            before_state_digest=state.canonical_digest(),
        )
        decision = evaluate_policy(
            request,
            actor=parent.actor,
            task=task,
            platform_permissions=_implicit_permissions(state, parent, request),
            acl_entries=state.domain_graph.acl_entries,
            grants=state.delegation_grants,
            policy_rules=state.policy_rules,
        )
        facts.append(
            PolicyFeasibilityFact(
                assertion_id=assertion.assertion_id,
                tool_name=tool_name,
                action=action,
                capability_available=decision.capability_available,
                platform_allowed=decision.platform_allowed,
                delegation_allowed=decision.delegation_allowed,
                policy_allowed=decision.policy_allowed,
                effective_allowed=decision.effective_allowed,
                policy_decision_digest=decision.decision_digest,
                evidence_refs=(f"policy.{decision.decision_id}",),
            )
        )
    return tuple(facts), None


def _implicit_permissions(
    state: OfficeWorldState,
    parent: CleanCaseMaterialization,
    request: ActionRequest,
) -> tuple[PlatformPermission, ...]:
    permissions = []
    for index, ref in enumerate(request.resource_refs):
        rights = resource_rights(state, parent.actor, ref)
        if not rights:
            continue
        source = {
            ResourceKind.MAIL_MESSAGE: PlatformPermissionSource.MAILBOX,
            ResourceKind.CALENDAR_EVENT: PlatformPermissionSource.ORGANIZER,
            ResourceKind.WORKSPACE_FILE: PlatformPermissionSource.OWNERSHIP,
        }.get(ref.kind, PlatformPermissionSource.OWNERSHIP)
        evidence_id = f"evidence.{request.request_id}.platform.{index:03d}"
        permissions.append(
            PlatformPermission(
                permission_id=f"permission.{request.request_id}.{index:03d}",
                principal_id=parent.actor.actor_id,
                resources=(ref,),
                rights=tuple(sorted(rights, key=lambda item: item.value)),
                source=source,
                source_evidence=SourceEvidence(
                    evidence_id=evidence_id,
                    source_kind=EvidenceSourceKind.CANONICAL_WORLD,
                    source_id=request.request_id,
                    observed_at=request.logical_time,
                    content_digest=sha256_digest(
                        {
                            "resource": ref,
                            "rights": tuple(sorted(rights, key=lambda item: item.value)),
                        }
                    ),
                    resource=ref,
                ),
            )
        )
    return tuple(permissions)


def _recipient(
    state: OfficeWorldState, principal_id: str, request_id: str, index: int
) -> ActionRecipient:
    principal = next(
        item
        for item in state.domain_graph.directory.principals
        if item.principal_id == principal_id
    )
    return ActionRecipient(
        principal_id=principal.principal_id,
        principal_kind=principal.kind,
        organization_id=principal.organization_id,
        evidence_ref=f"evidence.{request_id}.recipient.{index:03d}",
    )


def _sensitivity(state: OfficeWorldState, ref: ResourceRef) -> Sensitivity:
    if ref.kind is ResourceKind.DRIVE_FILE:
        return next(
            item.classification
            for item in state.domain_graph.drive.files
            if item.file_id == ref.resource_id
        )
    return Sensitivity.INTERNAL


def _required_right(action: ActionKind, has_resource: bool) -> AccessRight | None:
    return {
        ActionKind.DISCOVER: AccessRight.DISCOVER,
        ActionKind.READ: AccessRight.READ,
        ActionKind.CREATE: AccessRight.WRITE,
        ActionKind.UPDATE: AccessRight.WRITE,
        ActionKind.SHARE: AccessRight.SHARE,
        ActionKind.DELETE: AccessRight.DELETE,
        ActionKind.MANAGE_PERMISSIONS: AccessRight.MANAGE_PERMISSIONS,
        ActionKind.SEND: AccessRight.READ if has_resource else None,
    }[action]


def _decision(
    parent: CleanCaseMaterialization,
    objective: AttackObjectiveTemplate,
    condition: AdversarialCondition,
    purpose: CompatibilityPurpose,
    reason: CompatibilityReasonCode,
    *,
    bindings: tuple[ObjectiveResolvedBinding, ...] = (),
    selected: tuple[ReachableContentField, ...] = (),
    evidence: tuple[str, ...] = (),
    feasibility: tuple[PolicyFeasibilityFact, ...] = (),
) -> CompatibilityDecision:
    status = (
        CompatibilityStatus.COMPATIBLE
        if reason is CompatibilityReasonCode.COMPATIBLE
        else CompatibilityStatus.UNREACHABLE
        if reason
        in {
            CompatibilityReasonCode.REACHABLE_FIELD_MISSING,
            CompatibilityReasonCode.ACTOR_CAPABILITY_MISSING,
            CompatibilityReasonCode.PLATFORM_EFFECT_UNREACHABLE,
            CompatibilityReasonCode.POLICY_ENFORCE_BLOCKS_REQUIRED_MILESTONE,
        }
        else CompatibilityStatus.INCOMPATIBLE
    )
    payload = {
        "status": status,
        "reason_code": reason,
        "purpose": purpose,
        "objective_id": objective.objective_id,
        "entry_template_id": condition.template_id,
        "parent_case_id": parent.case_id,
        "resolved_objective_bindings": bindings,
        "selected_surface_fields": selected,
        "precondition_evidence_refs": evidence,
        "policy_feasibility": feasibility,
    }
    draft = CompatibilityDecision.model_construct(**payload, decision_digest="sha256:" + "0" * 64)
    return CompatibilityDecision(**payload, decision_digest=sha256_digest(draft.digest_payload()))


def _placement_key(placement: ContentPlacement) -> tuple[tuple[str, str, str], tuple[str, ...]]:
    return placement.resource_ref.sort_key(), placement.field_path


__all__ = ["solve_compatibility"]
