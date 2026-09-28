"""Deterministically materialize an attack payload into a frozen scenario copy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import (
    FrozenContract,
    InjectionOperation,
    ResourceSelector,
    TestCase,
)


class MaterializedInjection(FrozenContract):
    case_id: str = Field(min_length=1, max_length=128)
    carrier_id: str = Field(min_length=1, max_length=128)
    target: ResourceSelector
    payload_digest: str
    before_value_digest: str
    after_value_digest: str
    initial_state_digest: str
    initial_state: dict[str, Any]


def materialize_injection(case: TestCase) -> MaterializedInjection:
    """Apply one validated carrier without mutating the frozen scenario template."""
    case.assert_integrity()
    if case.attack is None:
        raise ValueError("clean TestCase has no attack to materialize")

    carrier = case.attack.carrier
    state = deepcopy(case.scenario.initial_state)
    parent, field = _resolve_parent(state, case, carrier.target)
    before = parent.get(field)
    if not isinstance(before, str):
        raise ValueError("injection carrier target must contain text")

    payload = case.attack.payload
    if carrier.operation == InjectionOperation.REPLACE:
        after = payload
    elif carrier.operation == InjectionOperation.PREPEND:
        after = f"{payload}{carrier.separator}{before}"
    elif carrier.operation == InjectionOperation.APPEND:
        after = f"{before}{carrier.separator}{payload}"
    elif carrier.operation == InjectionOperation.TEMPLATE_SLOT:
        if before.count(carrier.template_slot) != 1:
            raise ValueError("injection target must contain template_slot exactly once")
        after = before.replace(carrier.template_slot, payload, 1)
    else:
        raise AssertionError(f"unsupported injection operation: {carrier.operation}")

    parent[field] = after
    return MaterializedInjection(
        case_id=case.case_id,
        carrier_id=carrier.carrier_id,
        target=carrier.target,
        payload_digest=sha256_digest(payload),
        before_value_digest=sha256_digest(before),
        after_value_digest=sha256_digest(after),
        initial_state_digest=sha256_digest(state),
        initial_state=state,
    )


def _resolve_parent(
    state: dict[str, Any], case: TestCase, selector: ResourceSelector
) -> tuple[dict[str, Any], str]:
    schemas = {item.collection_id: item for item in case.scenario.state_schema}
    schema = schemas[selector.collection_id]
    value: Any = state[selector.collection_id]
    if selector.item_id is not None:
        if schema.container == "mapping":
            value = value[selector.item_id]
        else:
            value = next(
                item
                for item in value
                if isinstance(item, dict)
                and item.get(schema.item_id_field) == selector.item_id
            )
    for field in selector.field_path[:-1]:
        value = value[field]
    if not isinstance(value, dict):
        raise ValueError("injection carrier target parent must be an object")
    return value, selector.field_path[-1]
