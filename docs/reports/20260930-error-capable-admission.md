# Error-Capable Scenario Local Evidence

Date: 2026-09-30  
Probe: `scripts/probe_error_capable_scenario.py`  
Evidence: [`20260930-error-capable-admission.json`](20260930-error-capable-admission.json)

## Result

**Not admitted for a formal guided/random comparison.** This probe verifies registered contracts, deterministic materialization digests, and synthetic Oracle examples only. Registered paths and attacks are not proof that an Agent can reach them.

The prior report overstated the evidence: task hiding, all-stage separation, and attack/path admission were inferred or hard-coded rather than observed through execution. The probe now labels synthetic examples and lists missing evidence explicitly.

## Verified locally

- Three task-family and ten attack-mode registrations.
- Nine registered path IDs represented in the generated plan matrix.
- Nine materializations with recomputable digests.
- Unit test exercises the Office V2 directory listing, search, and read tools for one task file.
- Oracle preserves `attempted` as unresolved rather than reporting it as safe.

## Still required in the local implementation

- Three families each have at least two end-to-end legal paths using actual Office V2 tools and task-specific completion evidence.
- Distinct attack carriers are materialized in the actual supported tool contexts.
- Same-Provider selector invocation receipts and guided/random feedback isolation through the next selection.
- Behavior, risk, and joint coverage computed from execution evidence, including the four gain cases and delayed saturation.
- Placebo scheduling and separate campaign metrics.
- Episode evidence persistence, interruption/failure retention, and idempotent resume.

No server campaign was run. Formal comparison eligibility remains `false`.
