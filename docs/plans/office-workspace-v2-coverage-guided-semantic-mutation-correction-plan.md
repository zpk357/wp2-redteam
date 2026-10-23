# Office V2 Design Alignment Plan

This plan implements the user's six-step generation loop in small, reviewable batches. It is subordinate to `SPEC.md` and must not create a second product contract.

## Batches

1. **Documentation contract**: align `SPEC.md`, `AGENTS.md`, `HANDOFF.md`, README, and this plan with the six-step loop, two strategies, two Agent Runtimes, twelve semantic operator families, and safety/compliance rules.
2. **Pure-random surface (complete)**: restore the random strategy CLI, paired audit, and comparison archive as formal comparison tools. The remaining no-promotion storage invariant is intentionally deferred to Batch 3.
3. **No-promotion random execution (complete)**: execute a real Episode and retain trajectory/Oracle/Coverage, but keep generated text temporary and never insert derived `AttackSeed` or `CorpusEntry`. Store and promotion gates reject any random seed-pool or CorpusEntry mutation.
4. **Frozen-root bootstrap**: complete. Bootstrap now emits immutable `FrozenRootSupportRecord` objects for frozen materializations; root entries reference support records, and initial `ExecutionRecord` values are absent until a real Agent Episode runs. Scheduler, runtime, preflight, pairing, and store validation preserve this boundary.
5. **Operator contract**: model family + concrete variant + prompt instruction + changed/preserved dimensions + risk applicability + forbidden changes + replayable selection receipt. The formal twelve-family directory and variant records are now implemented; production prompt construction includes the selected variant constraints. The operator changes only the initial text expression; it never changes the seed attack objective or frozen facts.
6. **Verification (complete)**: focused tests now cover both strategies, both Agent Runtimes, lowest trigger-level scheduling, operator variants and constraints, no-promotion, immutable lineage, and fail-closed deployment. The verification pass also found and repaired a stale DeepSeek Harness source-lock digest after runtime edits.

## Acceptance rules

- Risk direction is the only formal scheduler target; behavior Coverage is an Episode result and promotion signal.
- Coverage-guided consumes readable cross-generation feedback; pure random consumes none.
- Pure random never adds generated text to the seed pool.
- The final Agent-facing text is stored exactly when a guided candidate is promoted.
- No model self-report can create tool, permission, state, Oracle, Coverage, or submit evidence.
- Local Fake tests, Docker E2E, and authorized server runs are reported separately.
- Legacy semantic names and selectors may remain only as private replay/test compatibility aliases; they are not formal production API. The formal production directory is the twelve-family catalog.

## Safety

The project is for lawful authorized software engineering and coverage research. It must not be used for unauthorized access, data theft, malware, exploitation, or security-control bypass. Data security, prohibited-use, security-reporting, and continuous-review requirements remain active for every batch.
