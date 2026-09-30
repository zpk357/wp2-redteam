# Error-Capable Scenario Local Evidence

Date: 2026-09-30  
Probe: `scripts/probe_error_capable_scenario.py`  
Evidence: [`20260930-error-capable-admission.json`](20260930-error-capable-admission.json)

## Result

**Not admitted for a formal guided/random comparison.** This probe verifies registered contracts, deterministic materialization digests, synthetic Oracle examples, and six direct Office V2 tool paths. It does not run a live LLM Agent.

The report separates direct Office V2 tool evidence from synthetic Oracle examples. It does not claim that a live LLM Agent followed these paths.

## Verified locally

- Three task-family and ten attack-mode registrations.
- Nine registered path IDs represented in the generated plan matrix.
- Nine materializations with recomputable digests.
- Six local path probes use real Office V2 directory, search, read, create, write, share,
  and calendar update tools. Every probe discovered and read the hidden task file, completed
  its action sequence, and produced a state transition; path pairs have distinct tool traces.
- Oracle preserves `attempted` as unresolved rather than reporting it as safe.
- Local receipts now preserve selector input digests, guided/random feedback separation,
  four coverage increment cases, a fixed placebo schedule, and idempotent episode receipts.

## Still required before formal comparison

- Live Agent task discovery and completion evidence (the probes call tools directly and do not run an Agent).
- Distinct attack carriers are materialized in the actual supported tool contexts.
- Provider-backed selector invocation (the local receipt currently records the model identity and
  request digest but does not call a model).
- Behavior, risk, and joint coverage connected to a live Agent execution loop and delayed saturation.
- Placebo scheduling connected to campaign metrics.
- Episode evidence persistence, interruption/failure retention, and idempotent resume.

No server campaign was run. Formal comparison eligibility remains `false`.
