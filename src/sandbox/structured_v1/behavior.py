"""Finite, evidence-backed FBK-04 projection; IDs stay outside semantic keys.

The registry below is frozen with the coverage version. Unknown tools and missing
formal results are diagnostics, never a fallback to tool names as novelty rewards.
Source support is deliberately exact: a named resource plus proven body propagation,
or consumption of the exact version created by a proven upstream call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.coverage import (
    BehaviorAtom,
    _audience_relation,
    _exposure_risk_facts,
    _recipient_relation,
)
from sandbox.structured_v1.evidence import DeliveryChannel
from sandbox.structured_v1.exposure import return_content_digest
from sandbox.structured_v1.obligations import delivered_classes

if TYPE_CHECKING:
    from sandbox.structured_v1.bundle import EpisodeBundle
    from sandbox.structured_v1.fixture import StructuredFixtureManifest


BEHAVIOR_VERSION = "behavior-v1"

# Semantic roles and classes, not tool-name or instance keys. Calendar objects are
# shared artifacts; mailbox entries are messages, and initial files are source material.
TOOL_ROLES = {
    "search_email": ("discover", "message"),
    "read_email": ("read", "message"),
    "send_email": ("deliver", "message"),
    "search_calendar_events": ("discover", "shared_artifact"),
    "create_calendar_event": ("deliver", "shared_artifact"),
    "update_calendar_event": ("transform", "shared_artifact"),
    "cancel_calendar_event": ("transform", "shared_artifact"),
    "search_drive_files": ("discover", "source_material"),
    "read_drive_file": ("read", "source_material"),
    "create_drive_file": ("prepare", "private_draft"),
    "share_drive_file": ("deliver", "shared_artifact"),
    "update_drive_permissions": ("transform", "shared_artifact"),
    "delete_drive_file": ("transform", "source_material"),
    "list_directory": ("discover", "source_material"),
    "search_files": ("discover", "source_material"),
    "read_file": ("read", "source_material"),
    "write_file": ("prepare", "private_draft"),
}


def _atom(kind, *dimensions, refs):
    return BehaviorAtom(key=(BEHAVIOR_VERSION, kind, *dimensions), evidence_refs=tuple(refs))


@dataclass
class BehaviorProjection:
    by_call: dict[str, list[BehaviorAtom]] = field(default_factory=dict)
    source_edges: dict[str, list[BehaviorAtom]] = field(default_factory=dict)
    not_admitted: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ContentOrigin:
    digest: str
    value_digest: str
    role: str
    object_class: str
    root: str
    refs: tuple[str, ...]


def _resource_key(resource):
    return resource.kind.value, resource.resource_id, resource.version_id


def _flag(value):
    return "not_evaluated" if value is None else "allowed" if value else "denied"


def _permissions(record, evidence, role, manifest, read_classes):
    policy = evidence.policy_decision
    if policy is None:
        return [], [f"missing-policy:{record.transaction_id}"]
    labels = set(read_classes)
    unresolved = False
    for effect in record.effects:
        classes, missing = delivered_classes(effect, manifest)
        labels.update(item.value for item in classes)
        unresolved |= bool(missing)
    # Public is a real policy observation; nonmatching private content is never
    # relabelled public just because the exact registered value disappeared.
    if (not labels and not unresolved and policy.resource_sensitivities
            and all(item.value == "public" for item in policy.resource_sensitivities)):
        labels.add("public")
    if not labels:
        if role == "discover" or not policy.resource_refs and role in {"check", "clarify"}:
            labels.add("not_applicable")
        else:
            unresolved = True
    atoms = []
    principals = policy.recipient_ids or (policy.actor_id,)
    for label in sorted(labels):
        for principal in principals:
            recipient = _recipient_relation(manifest, principal, DeliveryChannel.ACTOR_PRIVATE)
            audience = _audience_relation(manifest, label, principal)
            atoms.append(_atom(
                "permission_branch", role, recipient, audience, policy.outcome.value,
                _flag(policy.platform_allowed), _flag(policy.delegation_allowed),
                _flag(policy.policy_allowed),
                policy.policy_enforcement_mode.value if policy.policy_enforcement_mode else "none",
                refs=(record.transaction_id, policy.decision_id),
            ))
    return atoms, [f"unknown-data-relation:{record.transaction_id}"] if unresolved else []


def _state_changes(record, evidence, object_class):
    transition = evidence.state_transition
    if transition is None or not transition.committed:
        return []
    delta = transition.state_delta
    kinds = {
        "mail_message": "message", "mail_thread": "message", "mail_delivery": "message",
        "drive_file": object_class, "drive_file_version": object_class,
        "workspace_file": object_class, "calendar_event": "shared_artifact",
    }
    atoms = []
    for operation, objects in (("created", delta.created_objects),
                               ("deleted", delta.removed_objects)):
        for obj in objects:
            category = kinds.get(obj.kind.value)
            if category:
                atoms.append(_atom("state_change", category, operation,
                                   refs=(record.transaction_id,)))
            elif obj.kind.value in {"acl_entry", "share_record"}:
                atoms.append(_atom("state_change", "shared_artifact", "modified",
                                   refs=(record.transaction_id,)))
    for change in delta.changed_fields:
        category = kinds.get(change.object_ref.kind.value)
        if category and change.before_value_digest != change.after_value_digest:
            atoms.append(_atom("state_change", category, "modified",
                               refs=(record.transaction_id,)))
        elif (change.object_ref.kind.value in {"acl_entry", "share_record"}
              and change.before_value_digest != change.after_value_digest):
            atoms.append(_atom("state_change", "shared_artifact", "modified",
                               refs=(record.transaction_id,)))
    for effect in record.effects:
        if effect.is_delivery():
            atoms.append(_atom("state_change", object_class, "delivered",
                               refs=(record.transaction_id, effect.effect_id)))
    return atoms


def project_behavior(
    bundle: EpisodeBundle, manifest: StructuredFixtureManifest,
) -> BehaviorProjection:
    """Recompute B solely from correctly bound formal result evidence."""
    from sandbox.structured_v1.execution_evidence import (
        ToolExecutionEvidence,
        validate_execution_binding,
    )

    result = BehaviorProjection()
    decisions = {d.action_request_id: d for d in bundle.model_decisions if d.action_request_id}
    returns = {r.tool_call_id: r for r in bundle.tool_returns}
    reads: dict[tuple, _ContentOrigin] = {}
    produced: dict[tuple, _ContentOrigin] = {}
    created_ids: set[str] = set()
    shared_ids: set[str] = set()
    read_classes: dict[str, set[str]] = {}
    for exposure in bundle.exposures:
        facts, _ = _exposure_risk_facts(exposure, bundle, manifest)
        read_classes.setdefault(exposure.tool_call_id, set()).update(f.key[1] for f in facts)
    for record in sorted(bundle.records, key=lambda r: r.sequence):
        call = record.transaction_id
        decision = decisions.get(record.action_request_id)
        returned = returns.get(call)
        evidence = returned.execution_evidence if returned is not None else None
        if evidence is None or decision is None:
            result.not_admitted.append(f"missing-execution-evidence:{call}")
            continue
        try:
            evidence = ToolExecutionEvidence.model_validate(evidence.model_dump())
            validate_execution_binding(evidence, tool_name=decision.tool_name,
                                       arguments=decision.arguments,
                                       before_state_digest=record.before_state_digest,
                                       after_state_digest=record.after_state_digest)
            if (evidence.tool_name != returned.tool_name
                    or evidence.tool_name != decision.tool_name
                    or evidence.arguments_digest != sha256_digest(decision.arguments)
                    or evidence.before_state_digest != record.before_state_digest
                    or evidence.after_state_digest != record.after_state_digest
                    or evidence.sequence != record.sequence - 1
                    or any(effect.proof_digest != evidence.execution_fact_digest
                           for effect in record.effects)
                    or (evidence.status.value == "succeeded") != record.committed):
                raise ValueError("call/result mismatch")
            if evidence.state_transition is not None and (
                evidence.state_transition.transition_digest != record.world_transition_digest
            ):
                raise ValueError("transition mismatch")
        except ValueError:
            result.not_admitted.append(f"inconsistent-execution-evidence:{call}")
            continue
        mapping = TOOL_ROLES.get(decision.tool_name)
        if mapping is None:
            result.not_admitted.append(f"unmapped-tool:{call}:{decision.tool_name}")
            continue
        role, object_class = mapping
        resource = evidence.read_resource
        target_id = (resource.resource_id if resource is not None
                     else decision.arguments.get("file_id") or decision.arguments.get("path"))
        if decision.tool_name in {"read_drive_file", "read_file", "delete_drive_file"}:
            if target_id in shared_ids:
                object_class = "shared_artifact"
            elif target_id in created_ids:
                object_class = "private_draft"
        atoms = [_atom("action_result", role, object_class, evidence.status.value, refs=(call,))]
        permissions, missing = _permissions(
            record, evidence, role, manifest, read_classes.get(call, ()),
        )
        atoms.extend(permissions)
        result.not_admitted.extend(missing)
        atoms.extend(_state_changes(record, evidence, object_class))
        result.by_call[call] = atoms
        if not record.committed:
            continue
        if role == "read" and resource is not None:
            key = _resource_key(resource)
            upstream = produced.get(key)
            root = evidence.read_origin
            refs = (call,)
            field_name = "body" if decision.tool_name == "read_email" else "content"
            output_value = next((v.value_digest for v in evidence.output_evidence
                                 if v.field_path == (field_name,)), None)
            if upstream and (upstream.digest == returned.content_digest
                             and upstream.value_digest == output_value):
                root, refs = upstream.root, (*upstream.refs, call)
            if output_value is not None:
                reads[key] = _ContentOrigin(
                    returned.content_digest, output_value, role, object_class, root, refs,
                )

        created_ids.update(record.created_object_ids)
        if (decision.tool_name == "share_drive_file"
                and any(effect.is_delivery() for effect in record.effects)):
            shared_ids.add(decision.arguments.get("file_id"))

        # Real committed bodies come from state, never from model statements.
        digest = evidence.committed_content_digest
        output = evidence.committed_resource
        if digest is None or output is None:
            continue
        candidates = []
        parameter_role = "reference" if decision.tool_name == "share_drive_file" else "content"
        if decision.tool_name == "share_drive_file":
            origin = produced.get(_resource_key(output)) or reads.get(_resource_key(output))
            if origin and origin.digest == digest:
                candidates.append(origin)
        elif decision.tool_name in {"send_email", "create_drive_file"}:
            arg_name = "body" if decision.tool_name == "send_email" else "content"
            body = decision.arguments.get(arg_name)
            if not isinstance(body, str) or return_content_digest(body) != digest:
                result.not_admitted.append(f"inconsistent-content:{call}")
                continue
            arg_refs = decision.arguments.get(
                "related_refs" if decision.tool_name == "send_email" else "source_refs", ()
            ) or ()
            named = {(r.get("kind"), r.get("resource_id"))
                     for r in arg_refs if isinstance(r, dict)}
            for ref in evidence.committed_source_refs:
                if ref.kind.value != "drive_file" or (ref.kind.value, ref.resource_id) not in named:
                    continue
                for key, origin in reads.items():
                    if (key[:2] == (ref.kind.value, ref.resource_id)
                            and (ref.version_id is None or ref.version_id == key[2])
                            and origin.value_digest == sha256_digest(body)
                            and origin.digest == digest):
                        candidates.append(origin)
        roots = {origin.root for origin in candidates if origin.root != "unknown"}
        if candidates and len(roots) == len({origin.root for origin in candidates}):
            root = next(iter(roots)) if len(roots) == 1 else "mixed"
            refs = tuple(dict.fromkeys(
                ref for origin in candidates for ref in origin.refs
            )) + (call,)
            atoms.append(_atom("source_use", role, parameter_role, "tool_output", root, refs=refs))
            for origin in candidates:
                edge = _atom("source_edge", origin.role, origin.object_class, role, parameter_role,
                             refs=(*origin.refs, call))
                atoms.append(edge)
                result.source_edges.setdefault(call, []).append(edge)
            produced[_resource_key(output)] = _ContentOrigin(
                digest, candidates[0].value_digest, role, object_class, root, refs,
            )
        else:
            result.not_admitted.append(f"unproven-parameter-source:{call}:{parameter_role}")
    result.not_admitted.append(f"termination:{bundle.stop_reason.value}")
    return result
