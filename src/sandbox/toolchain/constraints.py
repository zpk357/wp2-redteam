"""Recover the security constraints a run was judged against.

``SPEC.md`` §14.2 replaces per-case Oracle code with three declared tables and
one evaluator.  This module reads the first two out of the world state that was
frozen for the Episode:

* ``domain_graph.acl_entries`` -- who may read or act on which resource, at
  which version, with which rights.  This is the *可接收集合*.
* ``policy_rules`` -- which actions are refused for which resource sensitivity
  and which recipient kind, and whether the rule is audited or enforced.  This
  is the *可动作集合*.

The third table (which facts are protected, and who may receive them) lives in
``MaterializedScenario.facts`` and ``authorized_recipients``.  It is *not*
recoverable from a run root yet; :func:`load_world_constraints` therefore
reports it as missing rather than pretending otherwise.  TASK-01 in
``TASK.md`` measures exactly how often that gap bites.

Nothing here judges a run.  The loader is deliberately unable to say whether an
Episode was a violation -- a loader that could would be the per-case Oracle
again, wearing a different name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Key under ``world_state`` holding the authorisation table.
ACL_KEY = "acl_entries"
#: Key under ``world_state`` holding the enterprise policy table.
POLICY_KEY = "policy_rules"
#: The table that is not in any run artifact yet.
PROTECTED_FACTS_KEY = "materialized_facts"


@dataclass(frozen=True)
class AudienceGrant:
    """One row: a grantee holds ``rights`` on one resource."""

    grantee_id: str
    resource_kind: str
    resource_id: str
    version_id: str | None
    rights: tuple[str, ...]
    granted_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grantee_id": self.grantee_id,
            "resource_kind": self.resource_kind,
            "resource_id": self.resource_id,
            "version_id": self.version_id,
            "rights": list(self.rights),
            "granted_by": self.granted_by,
        }


@dataclass(frozen=True)
class PolicyRule:
    """One row: an action is refused (or audited) under these conditions."""

    rule_id: str
    effect: str
    enforcement_mode: str
    actions: tuple[str, ...]
    resource_kinds: tuple[str, ...]
    recipient_kinds: tuple[str, ...]
    sensitivities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "effect": self.effect,
            "enforcement_mode": self.enforcement_mode,
            "actions": list(self.actions),
            "resource_kinds": list(self.resource_kinds),
            "recipient_kinds": list(self.recipient_kinds),
            "sensitivities": list(self.sensitivities),
        }


@dataclass(frozen=True)
class ConstraintSet:
    """The tables recovered from one Episode, plus what was not recovered."""

    grants: tuple[AudienceGrant, ...] = ()
    policies: tuple[PolicyRule, ...] = ()
    #: Table keys that were absent or unusable, so a reader can tell "empty"
    #: from "not recorded".
    missing: tuple[str, ...] = ()
    #: Human-readable notes about rows that could not be parsed.
    malformed: tuple[str, ...] = ()

    @property
    def has_audience_table(self) -> bool:
        return bool(self.grants)

    @property
    def has_policy_table(self) -> bool:
        return bool(self.policies)

    @property
    def complete(self) -> bool:
        """Both recoverable tables are present and nothing was dropped."""

        return self.has_audience_table and self.has_policy_table and not self.malformed

    @property
    def distinct_resources(self) -> tuple[str, ...]:
        return tuple(sorted({grant.resource_id for grant in self.grants}))

    @property
    def distinct_grantees(self) -> tuple[str, ...]:
        return tuple(sorted({grant.grantee_id for grant in self.grants}))

    def summary(self) -> dict[str, Any]:
        return {
            "grants": len(self.grants),
            "policies": len(self.policies),
            "audience_table": self.has_audience_table,
            "policy_table": self.has_policy_table,
            "complete": self.complete,
            "distinct_resources": len(self.distinct_resources),
            "distinct_grantees": len(self.distinct_grantees),
            "missing": list(self.missing),
            "malformed": list(self.malformed),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "grant_rows": [grant.to_dict() for grant in self.grants],
            "policy_rows": [rule.to_dict() for rule in self.policies],
        }


def load_world_constraints(world_state: Mapping[str, Any] | None) -> ConstraintSet:
    """Parse the recoverable tables out of one frozen world state."""

    if not isinstance(world_state, Mapping):
        return ConstraintSet(missing=(ACL_KEY, POLICY_KEY, PROTECTED_FACTS_KEY))

    missing: list[str] = []
    malformed: list[str] = []

    domain = world_state.get("domain_graph")
    raw_acl = domain.get(ACL_KEY) if isinstance(domain, Mapping) else None
    grants, acl_notes = _parse_grants(raw_acl)
    if not isinstance(raw_acl, list) or not raw_acl:
        missing.append(ACL_KEY)
    malformed.extend(acl_notes)

    raw_policies = world_state.get(POLICY_KEY)
    policies, policy_notes = _parse_policies(raw_policies)
    if not isinstance(raw_policies, list) or not raw_policies:
        missing.append(POLICY_KEY)
    malformed.extend(policy_notes)

    if PROTECTED_FACTS_KEY not in world_state:
        missing.append(PROTECTED_FACTS_KEY)

    return ConstraintSet(
        grants=grants,
        policies=policies,
        missing=tuple(missing),
        malformed=tuple(malformed),
    )


def _parse_grants(raw: Any) -> tuple[tuple[AudienceGrant, ...], list[str]]:
    if not isinstance(raw, list):
        return (), []
    rows: list[AudienceGrant] = []
    notes: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            notes.append(f"{ACL_KEY}[{index}]: not a mapping")
            continue
        resource = item.get("resource")
        if not isinstance(resource, Mapping):
            notes.append(f"{ACL_KEY}[{index}]: no resource")
            continue
        grantee = item.get("grantee_id")
        if not isinstance(grantee, str) or not grantee:
            notes.append(f"{ACL_KEY}[{index}]: no grantee_id")
            continue
        rows.append(
            AudienceGrant(
                grantee_id=grantee,
                resource_kind=str(resource.get("kind", "")),
                resource_id=str(resource.get("resource_id", "")),
                version_id=_optional_str(resource.get("version_id")),
                rights=_str_tuple(item.get("rights")),
                granted_by=_optional_str(item.get("granted_by")),
            )
        )
    return tuple(rows), notes


def _parse_policies(raw: Any) -> tuple[tuple[PolicyRule, ...], list[str]]:
    if not isinstance(raw, list):
        return (), []
    rows: list[PolicyRule] = []
    notes: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            notes.append(f"{POLICY_KEY}[{index}]: not a mapping")
            continue
        rule_id = item.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            notes.append(f"{POLICY_KEY}[{index}]: no rule_id")
            continue
        scopes = item.get("action_scopes")
        actions: list[str] = []
        kinds: list[str] = []
        if isinstance(scopes, list):
            for scope in scopes:
                if not isinstance(scope, Mapping):
                    continue
                action = scope.get("action")
                if isinstance(action, str):
                    actions.append(action)
                kinds.extend(_str_tuple(scope.get("resource_kinds")))
        rows.append(
            PolicyRule(
                rule_id=rule_id,
                effect=str(item.get("effect", "")),
                enforcement_mode=str(item.get("enforcement_mode", "")),
                actions=tuple(sorted(set(actions))),
                resource_kinds=tuple(sorted(set(kinds))),
                recipient_kinds=_str_tuple(item.get("recipient_kinds")),
                sensitivities=_str_tuple(item.get("sensitivities")),
            )
        )
    return tuple(rows), notes


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted({item for item in value if isinstance(item, str)}))


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
