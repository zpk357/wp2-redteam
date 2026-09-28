# DeepSeek Harness Runtime

This directory contains the optional `deepseek_harness` Agent Runtime. It is
selected by `--agent-runtime deepseek_harness` and shares the exploratory
Campaign request, timeout, step, and tool-call limits with LangGraph.

`package-lock.json` pins executable dependencies for reproducible installs. It is
not a Campaign, model, image, or release validation mechanism.

The runtime process owns one Episode. Normal disposal uses JSON-RPC `shutdown`;
cancellation closes the disposable runtime process because the SDK wire has no
per-prompt cancel method.

The composition may load only:

- the JSON-RPC server and Agent core;
- a local Ollama-compatible model adapter;
- JSONL session persistence and required checkpoint support;
- one stdio MCP bridge generated from the existing Office ToolSpec catalog.

Web, Bash, PowerShell, local filesystem tools, terminal UI, subagents, remote
MCP transports and automatic external-provider fallback are excluded. There is
no separate release gate or historical evidence package for this runtime.
