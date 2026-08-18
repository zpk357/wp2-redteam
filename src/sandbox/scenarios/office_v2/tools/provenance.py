"""Episode-local field evidence ledger and argument-source verification."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import ResolvedBinding, ResourceRef
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    EvidenceOrigin,
    OfficeToolInvocation,
    OfficeToolResult,
    OutputEvidence,
    ToolFailureCode,
    argument_value,
)


class ProvenanceError(ValueError):
    def __init__(self, code: ToolFailureCode):
        self.code = code
        super().__init__(code.value)


class EvidenceLedger:
    """Mutable session ledger; evidence records themselves remain immutable."""

    def __init__(self) -> None:
        self._items: dict[str, OutputEvidence] = {}

    @property
    def items(self) -> tuple[OutputEvidence, ...]:
        return tuple(sorted(self._items.values(), key=OutputEvidence.sort_key))

    def add(self, evidence: Iterable[OutputEvidence]) -> None:
        for item in evidence:
            existing = self._items.get(item.evidence_id)
            if existing is not None and existing != item:
                raise ValueError("evidence id cannot be reused with different content")
            self._items[item.evidence_id] = item

    def seed_binding(self, binding: ResolvedBinding) -> tuple[OutputEvidence, ...]:
        seeded = tuple(
            OutputEvidence(
                evidence_id=f"evidence.binding.{binding.query_id}.{index:03d}",
                invocation_id=f"binding.{binding.query_id}",
                invocation_sequence=-1,
                field_path=(binding.binding_name, str(index)),
                resource_ref=resource,
                value_digest=sha256_digest(resource.model_dump(mode="json")),
                origin=EvidenceOrigin.FROZEN_BINDING,
            )
            for index, resource in enumerate(binding.resource_refs)
        )
        self.add(seeded)
        return seeded

    def verify_sources(self, invocation: OfficeToolInvocation) -> None:
        for source in invocation.argument_sources:
            evidence = self._resolve_prior(source, invocation)
            try:
                value = argument_value(invocation.arguments, source.argument_path)
            except (KeyError, IndexError, ValueError) as exc:
                raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISMATCH) from exc
            if source.mode is ArgumentSourceMode.EXACT_VALUE:
                digest = sha256_digest(value)
                if not any(item.value_digest == digest for item in evidence):
                    raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISMATCH)
            elif source.mode is ArgumentSourceMode.RESOURCE_REFERENCE:
                try:
                    resource = ResourceRef.model_validate(value)
                except Exception as exc:
                    raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISMATCH) from exc
                if not any(item.resource_ref == resource for item in evidence):
                    raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISMATCH)

    def _resolve_prior(
        self, source: ArgumentSource, invocation: OfficeToolInvocation
    ) -> tuple[OutputEvidence, ...]:
        resolved: list[OutputEvidence] = []
        for evidence_id in source.source_evidence_ids:
            item = self._items.get(evidence_id)
            if item is None:
                raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISSING)
            if item.invocation_sequence >= invocation.sequence:
                raise ProvenanceError(ToolFailureCode.ARGUMENT_SOURCE_MISMATCH)
            resolved.append(item)
        return tuple(resolved)


def infer_exact_argument_sources(
    arguments: dict[str, object],
    observed_results: Iterable[OfficeToolResult],
) -> tuple[ArgumentSource, ...]:
    """Link arguments to exact fields the Agent previously observed."""

    evidence = tuple(
        item
        for result in observed_results
        for item in result.output_evidence
    )
    sources: list[ArgumentSource] = []
    for path, value in _argument_values(arguments):
        resource = _resource_ref(value)
        if resource is not None:
            candidates = tuple(item for item in evidence if item.resource_ref == resource)
            mode = ArgumentSourceMode.RESOURCE_REFERENCE
        else:
            digest = sha256_digest(value)
            candidates = tuple(item for item in evidence if item.value_digest == digest)
            mode = ArgumentSourceMode.EXACT_VALUE
        if not candidates:
            continue
        latest = max(
            candidates,
            key=lambda item: (item.invocation_sequence, item.evidence_id),
        )
        sources.append(
            ArgumentSource(
                argument_path=path,
                source_evidence_ids=(latest.evidence_id,),
                mode=mode,
            )
        )
    return tuple(sources)


def _argument_values(
    value: object,
    path: tuple[str, ...] = (),
) -> Iterable[tuple[tuple[str, ...], object]]:
    resource = _resource_ref(value)
    if path and resource is not None:
        yield path, value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _argument_values(item, (*path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _argument_values(item, (*path, str(index)))
        return
    if path:
        yield path, value


def _resource_ref(value: object) -> ResourceRef | None:
    if not isinstance(value, dict):
        return None
    try:
        return ResourceRef.model_validate(value)
    except Exception:
        return None


__all__ = ["EvidenceLedger", "ProvenanceError", "infer_exact_argument_sources"]
