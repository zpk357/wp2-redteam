# H1 Feasibility Decision

Decision: `feasible` for H2 interface design.

The selected path is the official TypeScript SDK JSON-RPC client/server with a
custom minimal Cordis composition. A disposable Runtime process owns one
Episode. The driver observes durable session events and waits from inbox
admission to whole-Agent idle.

The H1 vertical probe proved:

- a custom OpenAI-compatible loopback route can preserve tool-call and usage
  data through `dsh-llm-pi-ai`;
- a stdio MCP server registers exactly one model-visible tool;
- the Agent performs model request, tool call, tool-result feedback and a
  subsequent model request;
- a second user message continues the same Session;
- normal close reaches JSON-RPC shutdown and process reap;
- cancellation closes the disposable Runtime and rejects the in-flight run.

The official headless package loads, but a full one-shot product run was not
performed. Its official contract requires the complete `dsh` launcher and a
broader Code Mode composition, so it is rejected as the formal platform driver.
This unrun comparison is recorded in `h1-evidence.json` and is not described as
passing.

Important constraints for H2:

- the pre-release SDK wire has no prompt cancel or session close method;
- `messageId` is an enqueue receipt, not a prompt-scoped result;
- producer Session events are acquisition facts, not a replacement TRACE;
- no Runtime fallback is allowed;
- the model endpoint, package lock, composition, image and ToolSpec catalog must
  be part of `AgentRuntimeIdentity`.

H1 used only deterministic loopback fixtures. It did not run Docker, Ollama,
Qwen, Office V2, Coverage, Campaign, Replay, Mutation or Judge.
