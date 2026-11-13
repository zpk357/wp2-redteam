"""Data contracts shared by analysis, execution and reporting.

Four objects carry the pipeline:

``ToolContract``
    What a tool consumes and produces, expressed as *type tags* and *resource
    kinds*.  This is the only input the dependency graph reads, so every edge
    can be traced back to a declared tag match or a declared read/write pair.

``SinkTool`` / ``SinkSite``
    A call into a high-impact API whose argument can be influenced by the
    tool's own entry parameters.  A constant argument never qualifies.

``DependencyEdge``
    One directed relation between two tools, with the rationale that produced
    it.  ``DIRECT`` means an upstream return field and a downstream parameter
    carry the same type tag; ``INDIRECT`` means the upstream persists a
    resource the downstream later reads.

``Chain``
    An ordered tool list ending at a sink, with every edge kept alongside it so
    a reader can audit why the chain exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TypeTag(StrEnum):
    """The value vocabulary tools exchange.

    Tags are intentionally coarse: they exist to decide whether an edge is
    *possible*, not whether it is *sensible*.  ``TEXT`` matching ``TEXT`` is the
    source of exactly the false positives ``SPEC.md`` TC-02 warns about.
    """

    URL = "url"
    PATH = "path"
    TEXT = "text"
    ROWS = "rows"
    SQL = "sql"
    COMMAND = "command"
    REPORT_ID = "report_id"


class ResourceKind(StrEnum):
    """Persisted carriers a tool can read or write."""

    DOCUMENT_STORE = "document_store"
    WORKSPACE_FILE = "workspace_file"
    METRICS_DB = "metrics_db"
    REPORT_STORE = "report_store"


class SinkType(StrEnum):
    """High-impact effect classes this prototype supports."""

    CMDI = "cmdi"
    CODEI = "codei"
    SSRF = "ssrf"
    SQLI = "sqli"


class DependencyKind(StrEnum):
    """Why two tools are connected."""

    #: An upstream return field and a downstream parameter share a type tag.
    DIRECT = "direct"
    #: The upstream persists a resource kind the downstream reads.
    INDIRECT = "indirect"


SINK_API_CATALOG: dict[str, SinkType] = {
    "subprocess.run": SinkType.CMDI,
    "subprocess.call": SinkType.CMDI,
    "subprocess.check_call": SinkType.CMDI,
    "subprocess.check_output": SinkType.CMDI,
    "subprocess.Popen": SinkType.CMDI,
    "subprocess.getoutput": SinkType.CMDI,
    "os.system": SinkType.CMDI,
    "os.popen": SinkType.CMDI,
    "os.execv": SinkType.CMDI,
    "os.spawnv": SinkType.CMDI,
    "eval": SinkType.CODEI,
    "exec": SinkType.CODEI,
    "builtins.eval": SinkType.CODEI,
    "builtins.exec": SinkType.CODEI,
    "urllib.request.urlopen": SinkType.SSRF,
    "urllib.request.Request": SinkType.SSRF,
    "requests.get": SinkType.SSRF,
    "requests.post": SinkType.SSRF,
    "requests.request": SinkType.SSRF,
    "httpx.get": SinkType.SSRF,
    "httpx.post": SinkType.SSRF,
    "cursor.execute": SinkType.SQLI,
    "connection.execute": SinkType.SQLI,
}
#: The catalog is keyed by the dotted call name as written in the tool source.
#: Aliased imports are not resolved; see ``agentfuzz.analysis.sink_scanner``.


@dataclass(frozen=True)
class Param:
    """One entry parameter of a tool."""

    name: str
    tag: TypeTag


@dataclass(frozen=True)
class Field:
    """One field a tool returns.

    Nested output is flattened into a dotted name (``results.url``) so a
    downstream parameter can be matched against the leaf it actually consumes.
    """

    name: str
    tag: TypeTag


@dataclass(frozen=True)
class ToolContract:
    """A tool as the analysis layer sees it."""

    name: str
    summary: str
    params: tuple[Param, ...]
    returns: tuple[Field, ...]
    reads: tuple[ResourceKind, ...]
    writes: tuple[ResourceKind, ...]

    def param(self, name: str) -> Param | None:
        for item in self.params:
            if item.name == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "params": [{"name": p.name, "tag": p.tag.value} for p in self.params],
            "returns": [{"name": f.name, "tag": f.tag.value} for f in self.returns],
            "reads": [r.value for r in self.reads],
            "writes": [w.value for w in self.writes],
        }


@dataclass(frozen=True)
class SinkSite:
    """A high-impact call whose argument an entry parameter can influence.

    ``tainted_arguments`` names the *entry parameters* that reached the call,
    not the intermediate locals on the way.  ``run_saved_script`` reports
    ``("path",)`` whether the argument is passed directly or after two
    assignments; that is the name an operator can act on.
    """

    tool: str
    sink_type: SinkType
    api: str
    line: int
    tainted_arguments: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "sink_type": self.sink_type.value,
            "api": self.api,
            "line": self.line,
            "tainted_arguments": list(self.tainted_arguments),
        }


@dataclass(frozen=True)
class SinkTool:
    """A tool holding at least one reachable sink site."""

    tool: str
    sites: tuple[SinkSite, ...]

    @property
    def sink_types(self) -> tuple[SinkType, ...]:
        seen: list[SinkType] = []
        for site in self.sites:
            if site.sink_type not in seen:
                seen.append(site.sink_type)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "sink_types": [t.value for t in self.sink_types],
            "sites": [s.to_dict() for s in self.sites],
        }


@dataclass(frozen=True)
class DependencyEdge:
    """A directed relation between two tools and the reason for it."""

    upstream: str
    downstream: str
    kind: DependencyKind
    #: For ``DIRECT``: the shared type tag.  For ``INDIRECT``: the resource kind.
    rationale: str
    #: The upstream field and downstream parameter the tag matched, when known.
    upstream_field: str | None = None
    downstream_param: str | None = None
    #: Resource kinds the upstream persists and the downstream reads.  A pair
    #: can be both a field dependency and a carrier dependency; the carrier is
    #: recorded here rather than silently dropped, because it is what makes a
    #: persisted artifact reach an execution sink.
    shared_resources: tuple[ResourceKind, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "upstream": self.upstream,
            "downstream": self.downstream,
            "kind": self.kind.value,
            "rationale": self.rationale,
            "upstream_field": self.upstream_field,
            "downstream_param": self.downstream_param,
            "shared_resources": [kind.value for kind in self.shared_resources],
        }


@dataclass(frozen=True)
class Chain:
    """An ordered tool list ending at a sink."""

    tools: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]
    sink: str
    sink_types: tuple[SinkType, ...]

    @property
    def length(self) -> int:
        return len(self.tools)

    @property
    def edge_kinds(self) -> tuple[DependencyKind, ...]:
        return tuple(edge.kind for edge in self.edges)

    def identifier(self) -> str:
        return "->".join(self.tools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier(),
            "length": self.length,
            "tools": list(self.tools),
            "sink": self.sink,
            "sink_types": [t.value for t in self.sink_types],
            "edges": [edge.to_dict() for edge in self.edges],
        }


def dumps(payload: Any) -> str:
    """Stable JSON, so two scans of the same tool set compare equal."""

    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
