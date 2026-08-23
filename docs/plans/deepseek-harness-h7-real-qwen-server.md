# DeepSeek Harness H7: real-model server validation

Status: local real-model entry and online build path are implemented and focused-verified;
waiting for the common source checkpoint and explicit server-cost approval.

Upstream plan: `docs/plans/deepseek-harness-parallel-agent-plan.md`.

## 1. Goal

Validate the DeepSeek Harness Agent with the same locked Qwen model, frozen Office V2
world and Stage 6 Campaign pipeline already used by the default LangGraph Agent.

H7 proves one multi-step tool path, one representative authorization or policy branch,
a two-generation Campaign, strict replay, GPU evidence and cleanup. It does not rank the
two Agents, expand the Office V2 matrix, redesign Campaign/Mutation, or integrate Judge.

## 2. Deployment acquisition policy

H7 is registry-first and server-built:

1. Export a source-only archive from one full Git commit, transfer it directly to the
   server and verify its SHA-256 before extraction. GitHub and a release tag are not
   deployment prerequisites.
2. Fetch `qwen3.5:27b-q4_K_M` directly from the official Ollama registry.
3. Verify the complete Ollama manifest, config and layer digests against the release
   identity before any Agent execution.
4. Install the locked Harness dependency graph from `package-lock.json` and verify the
   upstream commit and every runtime-source digest.
5. Build the LangGraph and Harness Agent images locally on the server from the same clean
   source checkout and locked model content.
6. Generate a build receipt containing source commit/snapshot digest, image IDs, Runtime
   identities and complete model verification; derive the two Runtime-specific Campaign
   locks only after this receipt closes.

H7 must not upload a model archive, create a mandatory offline bundle, publish model
layers to GHCR, build from a dirty worktree, or silently change model identity.

## 3. Runtime topology

```text
Controller
  |- serial GPU lease -> Mutator role
  `- serial GPU lease -> selected Agent role
       |- loopback-only Ollama + locked Qwen
       |- selected Agent Runtime
       |- Office V2 tools + one OfficeV2ContainerSession
       `- TRACE/recording artifact exporter
```

The Agent container remains disposable, non-root, read-only, without a Docker socket,
host business-data mounts or public model endpoint. The Mutator remains a separate role
with its own image, prompt, provider, budget and identity.

## 4. Expected implementation area

The local H7.0 allowlist is:

```text
agent_variants/deepseek_harness/Dockerfile.qwen
agent_variants/deepseek_harness/locks/**
scripts/export_server_source_snapshot.ps1
scripts/server_prepare_online_office_v2.sh
scripts/verify_online_ollama_store.py
scripts/write_online_build_receipt.py
scripts/server_preflight_office_v2_step6.sh
scripts/server_run_office_v2_step6.sh
scripts/monitor_office_v2_stage6_gpu.py
src/sandbox/fuzzer/v2_stage6_identity.py
src/sandbox/fuzzer/v2_stage6_evidence.py
tests/unit/test_deepseek_harness_server_contract.py
```

Shared Stage 6 validation modules should be parameterized by Runtime identity. Do not
copy the Stage 6 script suite or modify historical repair-package scripts.

## 5. Construction steps

### H7.0 Local static contract

Freeze the exact source commit/snapshot, model manifest/config/layer digests, Ollama image digest,
Harness upstream lock, expected image roles and server build inputs. Statically review
shell syntax and fail-closed paths. Do not contact the server or download the model.
The draft release manifest must remain `deployment_ready=false` until the official
Ollama and Node repository digests plus all four final image IDs are populated. Run
`scripts/validate_release_candidate.py --require-deployment-ready` only after server image
identities are returned; a local Docker image ID must never be recorded as a repository digest.

### H7.1 Online server bootstrap

Verify and extract the fixed source snapshot. Pull the pinned Ollama image, fetch the
official model, verify every digest, then build both Agent images once. A partial download
or identity mismatch must stop before image build.

### H7.2 Preflight

Run one normal multi-step task. Passing requires at least one successful Office tool
call, the real tool result in a later model request, a subsequent Agent decision and a
valid submit. A direct answer with no tool use cannot pass.

### H7.3 Representative branch

Run one frozen authorization or policy branch that is semantically distinct from the
preflight. Reuse a Campaign Episode if it naturally supplies the same evidence.

### H7.4 Two-generation Campaign

Use one Campaign for exactly two generations. Each generation uses the separate Mutator
role and then the Harness Agent role under a serial GPU lease. Generation two must bind
generation one's feedback digest. Success requires two complete Episode/Oracle/Coverage/
settlement closures; novelty or risk progress is reported as an observation, not forced.

### H7.5 Replay and recovery

Download one recording and run local strict replay without a model call. Only exercise
server resume if a real interruption occurs; do not create an artificial extra run merely
to increase the test count.

### H7.6 Evidence and cleanup

Bind preflight and Campaign artifacts to one deployment identity while preserving their
different execution scopes. Campaign progress, database, recordings, Oracle and Coverage
must share one Campaign identity. Sample GPU residency, offload configuration and peak
memory while inference is active. Confirm no current Episode container, volume, network,
Ollama, Node, Mutator or GPU process remains after cleanup.

## 6. Minimum execution budget

- One real-model preflight.
- One representative authorization/policy Episode, merged with Campaign when possible.
- One two-generation Campaign.
- One local strict replay.

Do not run a 17-tool matrix, 24/48-case matrix, 10-50 generation run, cross-Agent ranking
or Judge suite. Do not repeat the existing LangGraph server gate unless a shared runtime
digest changed.

## 7. Acceptance

- Qwen chooses tool names, arguments and submit timing without a host action plan.
- A successful tool result is consumed by a later model decision.
- Campaign completed generations, progress and completion status agree; paused or
  incomplete execution cannot be archived as success.
- Source, model, image, Runtime, tool, Case and Campaign identities form one verifiable
  deployment closure, and mixed artifacts are rejected.
- Agent and Mutator identities, sessions, prompts and costs remain separate.
- Active-inference GPU and offload evidence exists, and cleanup leaves zero current-run
  residue.
- Strict replay matches the source behavior and final Office state.

## 8. Stop conditions

Stop on model/tool protocol incompatibility, undeclared CPU offload, source or digest
mismatch, mixed Campaign evidence, incomplete cleanup, or a server script that reports
success for an incomplete state. Do not change models, use an external endpoint, lower an
acceptance gate, or fall back to the other Agent Runtime.

## 9. External cost gate

Before renting, connecting to or changing a server, report the exact tag, official model
download size, expected build storage, commands, execution budget and stop conditions.
Remote work starts only after explicit user approval.
