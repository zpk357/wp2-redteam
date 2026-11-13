"""ChainFuzzer-Lite prototype: multi-tool chain discovery for Agent tool sets.

The package is deliberately independent of ``sandbox``.  It reads a tool set
declared as ordinary Python functions, decides which tools are high-impact
sinks, builds the cross-tool dependency graph, and extracts candidate tool
chains that end at a sink.  Nothing here executes an Agent, calls a model, or
judges a business rule -- those layers come later and reuse the sandbox
infrastructure that already exists in this repository.

Scope change recorded in ``SPEC.md`` §13: the security effect of interest is a
generic, sandbox-observable one (a command runs, a request leaves for an
attacker-chosen destination, a file lands outside the workspace), not a
business authorisation breach.  The reason is stated there and not repeated
here.
"""

from agentfuzz.contracts import (
    Chain,
    DependencyEdge,
    DependencyKind,
    Field,
    Param,
    ResourceKind,
    SinkSite,
    SinkTool,
    SinkType,
    ToolContract,
    TypeTag,
)

__all__ = [
    "Chain",
    "DependencyEdge",
    "DependencyKind",
    "Field",
    "Param",
    "ResourceKind",
    "SinkSite",
    "SinkTool",
    "SinkType",
    "ToolContract",
    "TypeTag",
]
