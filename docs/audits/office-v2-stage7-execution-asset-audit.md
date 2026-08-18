# Office Workspace V2 Stage 7 Execution Asset Audit

Status: `Stage 7.0 frozen baseline`

This audit records the execution baseline before Office V2 is connected to the
container runtime. It does not change the production execution path and is not
evidence that Docker or Qwen can already run Office V2.

## 1. Frozen Inputs

| Stage | Evidence digest |
|---|---|
| 2 | `sha256:fce39b28f536bd7d538e08d12c0b8796894b8959ae62ee5c6cd9a076f517b291` |
| 3 | `sha256:7840411d116cffb17206708332edc7a57af9bf3f28b23f993d4a169e7d03b06c` |
| 4 | `sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077` |
| 5 | `sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04` |
| 6 | `sha256:f6cf9bc01fe70ee2372ebae6435f9c4286fa4d456e43dc13c60e6644fff7a740` |

The boundary test recomputes every digest instead of trusting the value stored
inside the evidence file. It also freezes the Office V2 contract versions,
TRACE schema `1.2`, state codec `2.0`, and the single public execution backend
value `trace_react_v2`.

## 2. Current Truth

The public protocol exposes one backend value, but the factory currently has
two internal adapter branches:

- `TRACE_G_FORMAL_AGENT=1` selects `LangGraphReactRuntime`.
- Other requests select `TraceReactAdapter`, optionally with the Ollama
  provider.

This is not two public backends. It is an unfinished migration inside the one
backend. More importantly, `LangGraphReactRuntime` still calls
`enable_office_episode()` and constructs its default `v1_session_surface`.
Office V2 can be injected into tests, but is not selected by the production
request path and cannot yet use the V1 recording route.

Therefore Stage 7 starts from this precise gap:

```text
today
host ExecutionRequest
  -> one trace_react_v2 protocol value
  -> AdapterFactory
  -> legacy TraceReactAdapter OR formal LangGraphReactRuntime
  -> V1 ToolRegistry / V1 office state by default

target
host freezes V2ExecutionEnvelope + V2 state
  -> one trace_react_v2 request/RPC
  -> one LangGraph episode loop
  -> one OfficeV2ToolRuntime + one EpisodeWorld
  -> model-visible tool results + trusted evidence sidecar
  -> TRACE/recording/checkpoint
  -> host rebuilds Stage 6 Oracle result
  -> container and temporary resources are removed
```

The target has one business-state owner: the Office V2 `EpisodeWorld` bound to
one `OfficeV2ToolRuntime` for the entire Episode. `ToolRegistry`, recording,
replay, and TRACE may transport or observe that state; they must not create a
second copy with independent behavior.

## 3. Asset Migration Table

| Asset | Classification | Stage 7 decision |
|---|---|---|
| `agent_image/app/runtime.py` | Direct reuse | Keep execution lifecycle, terminal-event checks, timeout/cancel and replay RPC orchestration. |
| `agent_image/app/server.py` | Direct reuse | Keep the existing JSON-RPC surface; do not create a V2 server. |
| `src/sandbox/scheduler/docker_scheduler.py` | Direct reuse | Keep one-case-per-container limits, isolation and cleanup. |
| `agent_image/app/agent/ollama_react_provider.py` | Direct reuse | Keep the locked in-container Ollama provider boundary. |
| `agent_image/app/tracing/` | Direct reuse | Keep TRACE 1.2 event collection; add V2 mappings through the existing collector. |
| `src/sandbox/protocol.py` | Connect V2 interface | Extend the existing request with a versioned V2 envelope in 7.1; do not add another RPC contract. |
| `agent_image/app/adapter/factory.py` | Connect and converge | Route a validated V2 request to the formal loop. Remove ambiguity only after deterministic and replay gates pass. |
| `agent_image/app/adapter/langgraph_react_runtime.py` | Connect V2 interface | Reuse the multi-turn loop, explicit submit rule and ToolMessage feedback. Replace the production V1 surface/state initialization with a V2 session factory. |
| `agent_image/app/tools/base.py` | Connect V2 interface | Bind the one V2 runtime instance. It must not translate V2 into V1 enterprise state. |
| `agent_image/app/replay/` | Connect V2 interface | Reuse recording, checkpoints, strict replay and fork mechanics; teach the existing codec the V2 state identity. |
| `agent_image/Dockerfile.qwen` and lock files | Connect V2 interface | Rebuild only after local protocol/runtime gates; Stage 7 gets a new image identity. |
| G5 preparation and server scripts | Historical packaging reference | Reuse packaging mechanics, not old gate names, manifests, digests or pass results. |
| `agent_image/app/tools/office_episode.py` | Historical test asset | V1 business model. It cannot own or adapt Office V2 state. |
| `agent_image/app/tools/workspace_scenario.py` | Historical test asset | Retain only for old focused regression until the V2 path replaces its active use. |
| `agent_image/app/adapter/trace_react_adapter.py` | Transitional active asset | Existing non-formal path. Do not add V2 logic to it; converge the formal V2 path before retiring its active scenario role. |
| Old 13-tool office controls and fixed matrices | Historical test asset | They may calibrate old contracts but cannot count as V2 execution evidence. |
| Inspect, Inspect Evals and AgentDojo runtime code | Retired | No production dependency or runtime route may return. |

## 4. Dependency Boundary

Before Stage 7 implementation, direct Office V2 imports outside the Office V2
package are limited to three explicit framework-neutral surfaces:

- `agent_image/app/agent/react_contract.py` reuses clarification arguments.
- `src/sandbox/agent_prompts.py` exposes the frozen V2 prompt renderer.
- `src/sandbox/tool_contracts.py` exposes the frozen V2 model tool specs.

The container runtime consumes the latter two surfaces, but the production
request still initializes V1 state; the complete V2 session remains
test-injected.

The boundary test fails if an unplanned direct Office V2 importer appears. Each
later Stage 7 step must deliberately update that allowlist together with a
focused behavior test. Office V2 core remains forbidden from importing the
container app, scheduler, coverage, mutation, fuzzer, judge, or V1 office
layers.

## 5. Decisions and Stop Signals

- Do not build a second state model, RPC server, replay engine, or Oracle.
- Do not convert the frozen TaskGoalGraph into a hidden action plan.
- Do not let model-visible results contain PolicyDecision, state digests, or
  Oracle verdicts.
- Do not claim Docker or Qwen support from this audit.
- Stop if Stage 2-6 evidence changes, a second public backend value appears, or
  V2 state must be translated through `OfficeEpisode` to run.

## 6. Next Step

Stage 7.1 has defined `V2ExecutionEnvelope` inside the existing execution
request. Stage 7.2 now connects that validated input to one container-side
`EpisodeWorld` and `OfficeV2ToolRuntime`; it must not translate through the V1
`OfficeEpisode` state.
