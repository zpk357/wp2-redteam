"""Build the cross-tool dependency graph from tool contracts.

Two rules, both declared rather than inferred:

``DIRECT``
    An upstream return field and a downstream parameter carry the same type
    tag, e.g. ``search_docs`` returns ``results.url`` (``url``) and
    ``fetch_document`` takes ``url`` (``url``).

``INDIRECT``
    The upstream writes a resource kind the downstream reads, e.g.
    ``write_workspace_file`` writes ``workspace_file`` and ``run_saved_script``
    reads it.

The graph deliberately over-approximates, exactly as ``SPEC.md`` TC-02
describes: ``TEXT`` matching ``TEXT`` links ``summarize`` to ``publish_report``
and also links ``get_system_date`` to ``publish_report``, which no reasonable
workflow would take.  Restricting traversal to chains that end at a sink removes
most of that noise; the rest is what a semantic filter is for, and no semantic
filter exists yet.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentfuzz.contracts import DependencyEdge, DependencyKind, ResourceKind, ToolContract


@dataclass(frozen=True)
class DependencyGraph:
    """A directed multigraph over tool names, flattened to a tuple of edges."""

    tools: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]

    def incoming(self, tool: str) -> tuple[DependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.downstream == tool)

    def outgoing(self, tool: str) -> tuple[DependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.upstream == tool)

    def upstream_tools(self, tool: str) -> tuple[str, ...]:
        return tuple(edge.upstream for edge in self.incoming(tool))

    def edge_between(self, upstream: str, downstream: str) -> DependencyEdge | None:
        for edge in self.edges:
            if edge.upstream == upstream and edge.downstream == downstream:
                return edge
        return None

    def summary(self) -> dict[str, Any]:
        direct = sum(1 for edge in self.edges if edge.kind is DependencyKind.DIRECT)
        indirect = len(self.edges) - direct
        return {
            "tools": len(self.tools),
            "edges": len(self.edges),
            "direct_edges": direct,
            "indirect_edges": indirect,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "nodes": list(self.tools),
            "edges": [edge.to_dict() for edge in self.edges],
        }


def build_graph(contracts: Iterable[ToolContract]) -> DependencyGraph:
    """Connect every ordered pair of distinct tools that has a declared relation."""

    ordered = tuple(contracts)
    edges: list[DependencyEdge] = []
    for upstream in ordered:
        for downstream in ordered:
            if upstream.name == downstream.name:
                continue
            edges.extend(_edges_between(upstream, downstream))
    return DependencyGraph(tools=tuple(contract.name for contract in ordered), edges=tuple(edges))


def _edges_between(upstream: ToolContract, downstream: ToolContract) -> list[DependencyEdge]:
    shared = tuple(sorted(set(upstream.writes) & set(downstream.reads), key=_resource_order))
    direct: list[DependencyEdge] = []
    seen_tags: set[str] = set()
    for field in upstream.returns:
        for param in downstream.params:
            if field.tag is not param.tag or field.tag.value in seen_tags:
                continue
            seen_tags.add(field.tag.value)
            direct.append(
                DependencyEdge(
                    upstream=upstream.name,
                    downstream=downstream.name,
                    kind=DependencyKind.DIRECT,
                    rationale=f"type tag {field.tag.value!r} passes through",
                    upstream_field=field.name,
                    downstream_param=param.name,
                    shared_resources=shared,
                )
            )
    if direct:
        return direct
    if shared:
        return [
            DependencyEdge(
                upstream=upstream.name,
                downstream=downstream.name,
                kind=DependencyKind.INDIRECT,
                rationale="shared carrier " + ", ".join(kind.value for kind in shared),
                shared_resources=shared,
            )
        ]
    return []


def _resource_order(kind: ResourceKind) -> str:
    return kind.value
