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


def binding_evidence_id(binding: ResolvedBinding, index: int) -> str:
    """Ledger evidence id for one resource of a frozen task binding."""

    return f"evidence.binding.{binding.query_id}.{index:03d}"


def binding_evidence_ids(binding: ResolvedBinding) -> tuple[str, ...]:
    """Every ledger evidence id the session can cite for a frozen binding.

    This is the single definition of the binding id space: the ledger seeds these
    records and the Oracle evidence bundle declares them as prior available
    evidence, so both sides must derive them from here.
    """

    return tuple(
        binding_evidence_id(binding, index) for index in range(len(binding.resource_refs))
    )


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
                evidence_id=binding_evidence_id(binding, index),
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
                if not any(
                    item.resource_ref is not None
                    and _citation_matches(item.resource_ref, value)
                    for item in evidence
                ):
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


def resource_reference_matches(observed: ResourceRef, citation: ResourceRef) -> bool:
    """Whether observed evidence satisfies the reference the Agent wrote.

    A drive file is routinely cited at file granularity while tool output evidence
    carries the resolved version, so a version-less citation is satisfied by any
    observed version of the same resource. A citation that names a version must
    still match that version exactly, and the kind and resource id must agree in
    both directions.
    """

    if observed.kind is not citation.kind or observed.resource_id != citation.resource_id:
        return False
    if citation.version_id is None:
        return True
    return observed.version_id == citation.version_id


def _citation_matches(observed: ResourceRef, value: object) -> bool:
    """Whether an argument value cites the resource an evidence record observed.

    Two shapes count: a resource-reference mapping, judged at the granularity the
    Agent wrote (see ``resource_reference_matches``), and a bare identifier string
    equal to the observed resource id or version id -- the shape concrete task
    references are rendered into ("id=...; version id=...").
    """

    resource = _resource_ref(value)
    if resource is not None:
        return resource_reference_matches(observed, resource)
    if isinstance(value, str):
        return value in {observed.resource_id, observed.version_id}
    return False


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
            candidates = tuple(
                item
                for item in evidence
                if item.resource_ref is not None
                and resource_reference_matches(item.resource_ref, resource)
            )
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


def infer_binding_argument_sources(
    arguments: dict[str, object],
    binding_evidence: Iterable[OutputEvidence],
    *,
    claimed_paths: Iterable[tuple[str, ...]] = (),
) -> tuple[ArgumentSource, ...]:
    """Link arguments to the frozen task bindings the Agent was shown.

    Bindings are seeded into the ledger before the Episode starts and rendered
    into the prompt's concrete task references, so citing one is prior,
    Agent-visible provenance even when no tool result exists yet. A path already
    claimed by a tool-result source keeps that more specific source.
    """

    claimed = set(claimed_paths)
    frozen = tuple(item for item in binding_evidence if item.resource_ref is not None)
    if not frozen:
        return ()
    sources: list[ArgumentSource] = []
    for path, value in _argument_values(arguments):
        if path in claimed:
            continue
        matches = tuple(
            item for item in frozen if _citation_matches(item.resource_ref, value)
        )
        if not matches:
            continue
        latest = max(
            matches,
            key=lambda item: (item.invocation_sequence, item.evidence_id),
        )
        sources.append(
            ArgumentSource(
                argument_path=path,
                source_evidence_ids=(latest.evidence_id,),
                mode=ArgumentSourceMode.RESOURCE_REFERENCE,
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


__all__ = [
    "EvidenceLedger",
    "ProvenanceError",
    "binding_evidence_id",
    "binding_evidence_ids",
    "infer_binding_argument_sources",
    "infer_exact_argument_sources",
    "resource_reference_matches",
]
