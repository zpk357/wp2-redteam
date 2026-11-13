"""Extract candidate tool chains that end at a sink.

Traversal runs backwards from each sink: prepend an upstream tool that has a
declared edge into the current head, stop at ``max_length``, never repeat a
tool inside one chain.  Because the walk only follows declared edges, every
chain carries its own justification and can be audited edge by edge -- a chain
is never invented by the extractor itself.

Only chains with at least ``min_length`` tools are reported.  The single-tool
case is the one existing single-hop testing already covers, and this prototype
exists to find what that misses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentfuzz.analysis.dependency_graph import DependencyGraph
from agentfuzz.contracts import Chain, DependencyEdge, SinkTool, SinkType


@dataclass(frozen=True)
class ChainPool:
    """The extracted chains and the counts a report needs."""

    chains: tuple[Chain, ...]

    def summary(self) -> dict[str, Any]:
        by_length: dict[int, int] = {}
        by_sink: dict[str, int] = {}
        indirect_chains = 0
        for chain in self.chains:
            by_length[chain.length] = by_length.get(chain.length, 0) + 1
            for sink_type in chain.sink_types:
                by_sink[sink_type.value] = by_sink.get(sink_type.value, 0) + 1
            if any(edge.shared_resources for edge in chain.edges):
                indirect_chains += 1
        return {
            "chains": len(self.chains),
            "by_length": {str(k): by_length[k] for k in sorted(by_length)},
            "by_sink_type": dict(sorted(by_sink.items())),
            "chains_carrying_a_persisted_artifact": indirect_chains,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "items": [chain.to_dict() for chain in self.chains]}


def extract_chains(
    graph: DependencyGraph,
    sinks: Iterable[SinkTool],
    *,
    min_length: int = 2,
    max_length: int = 5,
    max_chains: int = 256,
) -> ChainPool:
    """Enumerate sink-terminated chains within the given length bound."""

    if min_length < 2:
        raise ValueError("min_length must be at least 2; single-tool cases are out of scope")
    if max_length < min_length:
        raise ValueError("max_length must be at least min_length")

    by_name = {sink.tool: sink for sink in sinks}
    found: dict[str, Chain] = {}

    for sink_name in sorted(by_name):
        _walk(
            graph=graph,
            sink=by_name[sink_name],
            path=[sink_name],
            edges=[],
            found=found,
            min_length=min_length,
            max_length=max_length,
        )
        if len(found) >= max_chains:
            break

    ordered = sorted(found.values(), key=lambda chain: (chain.length, chain.identifier()))
    return ChainPool(chains=tuple(ordered[:max_chains]))


def _walk(
    *,
    graph: DependencyGraph,
    sink: SinkTool,
    path: list[str],
    edges: list[DependencyEdge],
    found: dict[str, Chain],
    min_length: int,
    max_length: int,
) -> None:
    if len(path) >= min_length:
        chain = Chain(
            tools=tuple(path),
            edges=tuple(edges),
            sink=sink.tool,
            sink_types=sink.sink_types,
        )
        found.setdefault(chain.identifier(), chain)

    if len(path) >= max_length:
        return

    head = path[0]
    for edge in graph.incoming(head):
        if edge.upstream in path:
            continue
        _walk(
            graph=graph,
            sink=sink,
            path=[edge.upstream, *path],
            edges=[edge, *edges],
            found=found,
            min_length=min_length,
            max_length=max_length,
        )


def chains_for_sink_type(pool: ChainPool, sink_type: SinkType) -> tuple[Chain, ...]:
    """The subset of a pool that ends at a sink of the given type."""

    return tuple(chain for chain in pool.chains if sink_type in chain.sink_types)
