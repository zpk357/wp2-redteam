# DeepSeek Harness Runtime Probe

This directory is the isolated H1 feasibility area for the optional
`deepseek_harness` Agent Runtime. It does not modify or replace the current
LangGraph runtime.

The upstream source is locked by `upstream-lock.json`. `package-lock.json` is
the executable dependency lock; do not replace exact versions with npm tags.

H1 chooses the official SDK JSON-RPC process boundary with a custom minimal
Cordis composition. The runtime process owns one Episode. Normal disposal uses
JSON-RPC `shutdown`; cancellation closes the disposable runtime process because
the pre-release SDK wire has no per-prompt cancel method.

The formal composition may load only:

- the JSON-RPC server and Agent core;
- a local Ollama-compatible model adapter;
- JSONL session persistence and required checkpoint support;
- one stdio MCP bridge generated from the existing Office ToolSpec catalog.

Web, Bash, PowerShell, local filesystem tools, terminal UI, subagents, remote
MCP transports and automatic external-provider fallback are excluded. Merely
installing a package does not enable a capability; the checked composition is
the capability boundary.

H1 probes are contract evidence only. H3 now adds a formal locked composition,
Python adapter, one real Office V2 read-only tool, trusted bridge sidecar, and
success/cancellation container probes. Its evidence is `h3-evidence.json`.

H3 does not establish all 17 tools, multi-turn authorization, Oracle parity,
Replay, Coverage, Campaign, Mutation, Judge, or real-model acceptance. Those
remain behind H4 and later stage gates.
