"""Recover ``ToolContract`` objects from a live tool module.

The extractor refuses to run when the declared metadata and the real code
disagree.  That check is the whole point of the module: a chain edge is only
worth reporting if the parameter it names actually exists in the signature, and
a return field is only worth reporting if the function is declared to expose it.

What is *not* checked, because static code cannot: whether the returned body
really carries the tag.  ``fetch_document`` is declared to return ``TEXT``
because its signature says ``str``; that a document body is text is a task-level
assumption, recorded here as a known limitation rather than verified.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import ModuleType

from agentfuzz.contracts import Field, Param, ResourceKind, ToolContract, TypeTag


class ToolContractError(ValueError):
    """Declared metadata does not match the code it describes."""


@dataclass(frozen=True)
class ToolsetExtraction:
    """The contracts plus the module they came from."""

    module_name: str
    source_path: str
    contracts: tuple[ToolContract, ...]

    def by_name(self) -> dict[str, ToolContract]:
        return {contract.name: contract for contract in self.contracts}


def extract_toolset(module: ModuleType) -> ToolsetExtraction:
    """Build contracts for every tool in ``module.TOOL_METADATA``."""

    metadata = getattr(module, "TOOL_METADATA", None)
    if not isinstance(metadata, dict) or not metadata:
        raise ToolContractError(f"{module.__name__} declares no TOOL_METADATA")

    contracts: list[ToolContract] = []
    for name, meta in metadata.items():
        function = getattr(module, name, None)
        if function is None or not callable(function):
            raise ToolContractError(f"metadata names {name!r} but the module has no such callable")
        contracts.append(_contract_for(name, function, meta))

    return ToolsetExtraction(
        module_name=module.__name__,
        source_path=str(getattr(module, "__file__", "")),
        contracts=tuple(contracts),
    )


def _contract_for(name: str, function: object, meta: dict[str, object]) -> ToolContract:
    declared_params = _mapping(meta, "params", name)
    declared_returns = _mapping(meta, "returns", name)

    signature = inspect.signature(function)  # type: ignore[arg-type]
    actual_params = tuple(signature.parameters)
    if set(actual_params) != set(declared_params):
        missing = sorted(set(actual_params) - set(declared_params))
        extra = sorted(set(declared_params) - set(actual_params))
        raise ToolContractError(
            f"{name}: declared parameters do not match the signature "
            f"(undeclared={missing}, not in signature={extra})"
        )

    params = tuple(
        Param(name=param, tag=_tag(declared_params[param], name, param))
        for param in actual_params
    )
    returns = tuple(
        Field(name=field, tag=_tag(declared_returns[field], name, field))
        for field in sorted(declared_returns)
    )

    return ToolContract(
        name=name,
        summary=str(meta.get("summary", "")),
        params=params,
        returns=returns,
        reads=_resources(meta, "reads", name),
        writes=_resources(meta, "writes", name),
    )


def _mapping(meta: dict[str, object], key: str, tool: str) -> dict[str, object]:
    value = meta.get(key, {})
    if not isinstance(value, dict):
        raise ToolContractError(f"{tool}: {key!r} must be a mapping")
    return dict(value)


def _tag(value: object, tool: str, item: str) -> TypeTag:
    if not isinstance(value, TypeTag):
        raise ToolContractError(f"{tool}.{item}: expected a TypeTag, got {value!r}")
    return value


def _resources(meta: dict[str, object], key: str, tool: str) -> tuple[ResourceKind, ...]:
    value = meta.get(key, ())
    if not isinstance(value, (tuple, list)):
        raise ToolContractError(f"{tool}: {key!r} must be a sequence")
    result: list[ResourceKind] = []
    for item in value:
        if not isinstance(item, ResourceKind):
            raise ToolContractError(f"{tool}.{key}: expected a ResourceKind, got {item!r}")
        result.append(item)
    return tuple(result)
