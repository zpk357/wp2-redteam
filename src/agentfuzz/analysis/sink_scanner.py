"""Decide which tools are sinks, from the tool source.

Two conditions must both hold, and the second is what keeps the result honest:

1. the body calls one of :data:`agentfuzz.contracts.SINK_API_CATALOG`, and
2. an argument of that call can be influenced by the tool's own entry
   parameters.

``get_system_date`` calls ``subprocess.run`` with a constant list and is
therefore not a sink, even though the API is the same one ``run_shell`` uses.
That counterexample is a unit test, not a comment.

Reachability is a flow-insensitive intraprocedural approximation: assignments
are iterated to a fixed point and any expression containing a tainted name
counts as tainted.  It over-approximates.  Aliased imports, attribute stores,
and cross-function flows are not modelled, and a tool that funnels its input
through a helper function will be reported as *not* a sink.  Both limits are
recorded in ``SPEC.md`` §13 rather than papered over.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from agentfuzz.contracts import SINK_API_CATALOG, SinkSite, SinkTool, SinkType

#: How many assignment passes to run before giving up on convergence.
_FIXPOINT_ROUNDS = 8


def scan_sinks(source_path: str | Path, tools: Iterable[str]) -> tuple[SinkTool, ...]:
    """Return one :class:`SinkTool` per tool that holds a reachable sink."""

    path = Path(source_path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = set(tools)

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    }

    found: list[SinkTool] = []
    for name in sorted(functions):
        sites = _sites_in(functions[name], name)
        if sites:
            found.append(SinkTool(tool=name, sites=tuple(sites)))
    return tuple(found)


def _sites_in(function: ast.FunctionDef | ast.AsyncFunctionDef, tool: str) -> list[SinkSite]:
    roots = _fixpoint(function, {arg.arg: frozenset({arg.arg}) for arg in function.args.args})

    sites: list[SinkSite] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        api = _dotted(node.func)
        sink_type = _classify(api)
        if sink_type is None:
            continue
        influenced = sorted(_entry_parameters(node, roots))
        if not influenced:
            continue
        sites.append(
            SinkSite(
                tool=tool,
                sink_type=sink_type,
                api=api,
                line=node.lineno,
                tainted_arguments=tuple(influenced),
            )
        )
    return sorted(sites, key=lambda site: (site.line, site.api))


def _fixpoint(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    roots: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    """Propagate taint through local assignments until nothing new appears.

    Each name maps to the set of *entry parameters* that reached it, so a sink
    reached through three intermediate variables still reports the parameter a
    reader can act on.
    """

    current = dict(roots)
    for _ in range(_FIXPOINT_ROUNDS):
        grown = dict(current)
        for node in ast.walk(function):
            source, targets = _assignment(node)
            if source is None or targets is None:
                continue
            reached = _entry_parameters(source, current)
            if not reached:
                continue
            for name in targets:
                grown[name] = grown.get(name, frozenset()) | reached
        if grown == current:
            break
        current = grown
    return current


def _assignment(
    node: ast.AST,
) -> tuple[ast.expr | None, set[str] | None]:
    if isinstance(node, ast.Assign):
        return node.value, _target_names(node.targets)
    if isinstance(node, ast.AnnAssign):
        return node.value, _target_names([node.target])
    if isinstance(node, ast.For):
        return node.iter, _target_names([node.target])
    return None, None


def _target_names(targets: Iterable[ast.expr]) -> set[str]:
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.update(_target_names(target.elts))
    return names


def _entry_parameters(expr: ast.expr, roots: dict[str, frozenset[str]]) -> set[str]:
    reached: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Name):
            reached.update(roots.get(node.id, ()))
    return reached


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else node.attr
    return ""


def _classify(api: str) -> SinkType | None:
    """Map a dotted call name onto a sink type, if the catalog knows it."""

    if not api:
        return None
    direct = SINK_API_CATALOG.get(api)
    if direct is not None:
        return direct
    tail = ".".join(api.split(".")[-2:])
    return SINK_API_CATALOG.get(tail)
