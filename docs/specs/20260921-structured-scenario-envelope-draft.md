# Structured-scenario execution envelope (draft)

- Status: **DRAFT** (2026-09-21, revision 6). **Not a user-approved SPEC.** The user's technical review accepted the four revision-2 directions as an implementation basis (single envelope shape, mandatory re-materialisation, transaction-payload binding, same-key-same-content receipts, versioned development assets) and asked for the three fixes applied in revision 3. That review is **not an approval**: it authorises neither container coding nor any probe run. Revision 4 added **materialisation is not delivery** (`SOC-ENV-76`, `SOC-ENV-77`); revision 5 applies the user's five decisions for the first real episode — calibration is never counted as model attack success (`SOC-ENV-87`), exposure evidence must be bound (`SOC-ENV-88`), the initialization transaction is retained in the bundle but excluded from delivery effects (`SOC-ENV-89`), closure is provable or becomes scoped UNKNOWN (`SOC-ENV-85`), and every real run carries a call-and-cost budget envelope with recorded usage (`SOC-ENV-86`).
- Requirement IDs: `SOC-ENV-01`–`SOC-ENV-90`, stable within this draft; cite them in reviews and in code comments. They do not replace the obligations in `SOC-SAF`/`SS`/`SOC-FBK`.
- Scope: what a *new* protocol identity must carry so that a structured case can be executed, judged, recovered and **refused when it is not the case it claims to be**.
- Not scope: budget, model, task split, repeat counts, statistics, run authorisation (independent experiment SPEC).
- Old route: the old `V2ExecutionEnvelope`, its frozen constants and every old Campaign stay untouched. This draft defines a separate identity.
- Word use: **A/B are world-asset policies, not two envelope shapes** (§3); the recommended envelope is one shape that both can use (§8).
- Status honesty: the materialisation layer referenced below is implemented and passes offline tests, but it is **not yet accepted by the user and has never been verified in a run**. This draft depends only on that layer's *contract* (base world as an input, the three digests, canonical field paths) and does not treat its test results as accepted evidence.

**Revision 6 adds what implementing P1 showed was missing from the register.** The checks of `SOC-ENV-21`–`SOC-ENV-23` (task text, actor, authorisation context) had no code, so `envelope.context_mismatch` is now registered; `envelope.objective_catalogue_present` also covers an Oracle scope that is not the fixture's own obligation set; and the material row now names a recomputed digest that disagrees with its declaration. The register holds **21 refusal codes and 2 outcome codes**, and `envelope_codes.py` is checked against §7 by a test, so the two cannot drift.

**Revision 3 fixes three review findings and one freeze gap.** R5: `SOC-ENV-54` no longer marks the whole episode UNKNOWN — proven `VIOLATED` findings survive, only obligations affected by the pending commit **and lacking positive evidence** become UNKNOWN, and the world is isolated while the Campaign pauses. R6: §7 now carries an explicit **failure-code column**, and the old `SOC-ENV-71` is split into three codes (base, tool catalogue, resume) for diagnosis. R7: fairness now freezes the **mapping**, not a single policy — all arms share one frozen `fixture → base/overlay` mapping, and different fixtures may pin different policies. R8: the tool freeze also records the **running image's immutable digest**, because contract digests cannot cover "interfaces unchanged, implementation changed".

**Revision 2 fixes four review findings.** R1: the field list could not support the checks it promised — payloads, locators and a **re-materialisation check** are now required (`SOC-ENV-20`–`SOC-ENV-32`). R2: B8 may not accept an effect that merely cites a real committed transaction — entries must be **bound to and re-derived from the transaction payload** (`SOC-ENV-51`–`SOC-ENV-54`). R3: retry and recovery rules contradicted each other — unified as **same key + same content returns the receipt, same key + different content is refused** (`SOC-ENV-55`), with recovery driven by the full transaction record and violations monotone (`SOC-ENV-56`, `SOC-ENV-57`). R4: the asset policy contradicted itself — unified as **development may revise assets under a new version, the formal comparison freezes a fixture→base mapping, no switching during a run** (`SOC-ENV-10`–`SOC-ENV-14`).

## 1. Why a new envelope is needed

| Bound by the old envelope | Where | Why it conflicts |
|---|---|---|
| `base_world_digest` | `src/sandbox/protocol.py:154-156`, `:236-237` | Freezes one world identity into the envelope; our base world must be an input (`SOC-ENV-01`) |
| Tool catalogue | `protocol.py:157-159`, `:238-239` | Re-freezable in principle, under the scheme in §11.1 |
| **Old 16-objective catalogue** | `protocol.py:160-162`, `:240-241` | Directly conflicts with `SS-001`: this protocol has **no fixed objective set** (`SOC-ENV-05`) |

The container's only entry is `request.office_v2_execution → load_office_v2_session` (`office_v2_runtime_surface.py:48-65`, `office_v2_session.py:505-529`), so a new protocol identity necessarily needs a container branch (`SOC-ENV-84`).

Reuse candidates (unchanged): tool contracts and provenance, state/transaction infrastructure, receipts and recovery, recording and bundle verification. See `docs/current-state/soc-t01-reuse-boundary.md`.

## 2. World identity

| ID | Requirement |
|---|---|
| `SOC-ENV-01` | `base_world_digest` is a **required input**, never a constant baked into the envelope. |
| `SOC-ENV-02` | Three digests are recorded **separately** and never merged: `base_world_digest` (world freeze), `overlay_digest` (fixture freeze), `material_digest` (rendering, recomputed at materialisation). |
| `SOC-ENV-03` | The episode start is recorded by two further digests: `initial_state_digest` and `initialization_transition_digest`, both produced by the materialisation transaction (`SOC-ENV-30`). |
| `SOC-ENV-04` | Chain rule: `base + overlay + material → initial_state`, and every later transition satisfies `before_state_digest ==` the previous `after_state_digest`. A chain that does not line up is a refusal (`SOC-ENV-62`), not a warning. |
| `SOC-ENV-05` | **Using the old world is not inheriting the old objective catalogue.** The new envelope has no objective-catalogue field at all; the Oracle scope comes from the fixture's obligation instances. A present catalogue field is a refusal (`SOC-ENV-63`). |
| `SOC-ENV-06` | Digests never stand in for objects: every digest in the envelope has a payload or a locator reachable by the container (§4). |
| `SOC-ENV-75` | The executed tooling has **two** recorded identities: the tool **contract catalogue digest** and the running **image's immutable digest**. Contract digests alone cannot cover "interfaces unchanged, implementation changed" (§11.1). |

## 3. Asset policy: A, B, and when either may change

| ID | Requirement |
|---|---|
| `SOC-ENV-10` | **A** = one frozen base world shared by the experiment, each fixture adding an overlay. **B** = one frozen base world per fixture, overlay minimal or empty. |
| `SOC-ENV-11` | **Development phase** (probe, vertical slice, local acceptance): assets may be revised. A revision produces a **new version** (new `base_world_digest`/`overlay_digest`), **keeps the old results** reported as belonging to the earlier version, and **re-freezes the run configuration** (episode counts, budgets, seeds) for the new version. Samples from before and after a revision are **never combined into one experiment**, and results from different versions are never compared as if equal. The user confirmed this rule applies to the probe. |
| `SOC-ENV-12` | **Before the formal comparison**, the **mapping is frozen**: `fixture → base/overlay`, published with the experiment SPEC. Within one experiment run, each fixture uses exactly one base and one overlay, identical for every arm; different fixtures may pin different policies (`SOC-ENV-40`). |
| `SOC-ENV-13` | **No switching during a run.** A base may not be changed, repaired or re-frozen while episodes of that version are being collected; doing so invalidates the run and requires a new version. |
| `SOC-ENV-14` | Within a frozen version, a structurally blocked route is **recorded, never patched** (`SOC-ENV-42`). Changing the world to unblock it is an asset revision under `SOC-ENV-11`, i.e. a new version — the two rules are not in conflict once versions are explicit. |

**A and B on the four axes.**

| | **A: shared frozen world + per-fixture overlay** | **B: one frozen world per fixture** |
|---|---|---|
| World identity | One `base_world_digest` for the experiment; `overlay_digest` and the two state digests vary per candidate | `base_world_digest` per fixture; overlay minimal or empty |
| Noise control | Background is **identical for every arm**, so it is a constant rather than a confound; it can still dilute the material and degrade reachability | The visible content is exactly what the fixture declares: attribution is cleaner and reachability is easier to control |
| Asset freeze cost | Low: one build, one freeze, one verification; new objects arrive as reviewable overlay entries | Higher: a build, freeze and verification per fixture, plus re-freezing on every fixture change (drift risk across fixtures) |
| G1 interpretation boundary | `INCONCLUSIVE` concerns the *fixture + shared background* pair; reachability must be reported so a blocked route is not read as "the material changed nothing" | Cleaner attribution for that fixture, with no claim beyond its world |

**B's extrapolation is not automatically weaker than A**, and this draft does not assume it. Which policy generalises better depends on the task and on how representative the sampled world is: a heavily loaded shared world may be *less* representative than a purpose-built one, and a purpose-built world may be too tidy. Representativeness must therefore be **argued per fixture** using reachability and background statistics (`SOC-ENV-43`), not assumed from the policy name. The honest cost of B is freeze and drift effort, not evidentiary weakness.

**B does not require a new hardcoded digest constant** (`SOC-ENV-15`). Its base digest can be computed at freeze time and carried by the fixture or a freeze manifest, exactly as A's can; a hardcoded constant is one possible implementation, and if that route is taken the constant is a *freeze artifact* to register, not an envelope requirement.

## 4. Payloads, locators and reconstruction

**Who builds the envelope:** the host (runner/generator) constructs it from frozen assets and the committed materialisation record. The candidate and the Provider never construct or amend it (`SOC-ENV-20`).

### 4.1 What the envelope carries

| ID | Object | Carrier | Fetched/verified by |
|---|---|---|---|
| `SOC-ENV-21` | Trusted task statement (fixture public task text, frozen) | by value | container checks it equals the manifest's, byte for byte |
| `SOC-ENV-22` | Execution actor: principal id, kind, organisation, status | by value | container checks it exists in the **base+overlay** directory and matches the fixture's declared actor |
| `SOC-ENV-23` | Authorisation context: `task_authorized`, per-class `data_allowed`, `required_delivery` | by value | container checks it equals the manifest's declarations (these drive `AUDIENCE_SCOPE`/`DATA_RELEASE`) |
| `SOC-ENV-24` | Fixture manifest | by value | self-digest check + equals `manifest_digest` |
| `SOC-ENV-25` | Overlay (principals, files, ACLs, slot targets) | by value | recomputed `overlay_digest`; canonical field paths; unique write locations |
| `SOC-ENV-26` | Rendered material | by value | slot set equals the manifest's; registered syntax; **recomputed** `material_digest` |
| `SOC-ENV-27` | Base world | **by locator** into the container's own frozen asset store, plus its digest | container fetches it itself; a locator that resolves outside the store is a refusal (`SOC-ENV-64`) |
| `SOC-ENV-28` | Initial state and initialization transition | **derived** (`SOC-ENV-30`); the envelope carries their digests and, for audit, the payloads | recomputed and compared |
| `SOC-ENV-29` | Oracle scope, tool catalogue digest, budget reservation, schedule identity, attempt/resume state, effect ledger | by value / by reference | §4.2, §6 |

### 4.2 The initial state is derived, never accepted

| ID | Requirement |
|---|---|
| `SOC-ENV-30` | The container **re-runs the deterministic materialisation** `materialize(base, overlay, material) → (initial_state, initialization_transition)` and requires the derived digests to equal the envelope's. A digest set that is internally consistent but **does not reproduce is refused** (`SOC-ENV-65`). Digests alone can only confirm an existing object; they cannot show that the object was produced by the rules. |
| `SOC-ENV-31` | The derived transition must be `committed`, carry no failure code, and satisfy the chain rule of `SOC-ENV-04` before any episode action runs. |
| `SOC-ENV-32` | Re-materialisation is required to be cheap (one transaction on the base world) and is performed **per episode**; a cache may be shipped for audit, but it is never the object of trust. If a future fixture makes re-materialisation expensive, that is an amendment to this draft, not a silent fallback to digest-only checking. |
| `SOC-ENV-33` | Everything the candidate authored is confined to `SOC-ENV-26`; the candidate never supplies a locator, a base world, a digest, an actor, an authorisation context or an Oracle scope. |

### 4.3 Trust boundary

| Field group | Candidate/Provider influence | Container checks |
|---|---|---|
| `protocol_id`, `envelope_version`, `schema_version`, `syntax_version` | none | equal the registered identity |
| fixture identity, manifest, task text, actor, authorisation context | none | `SOC-ENV-21`–`SOC-ENV-24` |
| base world and every digest | none | `SOC-ENV-01`–`SOC-ENV-06`, `SOC-ENV-27` |
| material | **content only** | `SOC-ENV-26`, `SOC-ENV-30` |
| episode/arm/generation/case identity, schedule receipt | none | consistent with the scheduler record |
| `oracle_scope` | none | non-empty, derived from the fixture's obligation instances, never a catalogue constant |
| tool catalogue digest **and** image immutable digest | none | `SOC-ENV-75`, §11.1 |
| budget reservation | none | matches the reservation receipt |
| effect ledger, attempt, resume state | none | §6 |

What a candidate may influence is confined to **the content it authors** (inside the fixture's slot grammar) and to **the Agent's own runtime actions** — nothing that determines how those actions are judged (`SOC-ENV-33`).

## 5. Fairness and shared content

| ID | Requirement |
|---|---|
| `SOC-ENV-40` | Within one experiment version, all arms share **one frozen `fixture → base/overlay` mapping**: for a given fixture every arm sees the same base and the same overlay (byte-identical) and the same visible content. **Different fixtures may pin different policies** (`SOC-ENV-10`) — a fixture's policy is part of the frozen mapping, not an experiment-wide choice. Only which material is written differs between arms, plus each arm's own scheduling inputs. |
| `SOC-ENV-41` | Fixed background content does **not** by itself constitute confounding: it is constant across arms. It can still (a) affect whether the material is reached at all and (b) limit how far results extrapolate. |
| `SOC-ENV-42` | Reachability is measured and reported: how often a declared route to the material was available and used, and any empirical structural block. Within a frozen version a blocked route is **recorded, never repaired** (`SOC-ENV-14`). |
| `SOC-ENV-43` | Policy choice (A/B) is justified per fixture with those measurements (`SOC-ENV-11`–`SOC-ENV-13`), not by the assumption that one policy is intrinsically more representative. |
| `SOC-ENV-44` | The six arms stay symmetric in opportunity and budget (TASK §8): no arm gains episodes because another arm is slower, fails more, has a larger archive or more candidates. |

## 6. Commit-time effect evidence (B8) and recovery

### 6.1 Evidence bound to the transaction

| ID | Requirement |
|---|---|
| `SOC-ENV-50` | Inside the tool's **committing transaction**, the runtime writes an effect record carrying: content digest; the audience relation **as it was at commit time** (who could read it, with which rights); the created/modified object or message identity; the transaction id; and a monotonic sequence number. |
| `SOC-ENV-51` | The effect record is **part of the transaction payload, not a parallel log**: the ledger is a projection of committed payloads, and the payload is authoritative. |
| `SOC-ENV-52` | **Content binding.** Every entry must be re-derivable from the transaction it cites: object identity, content digest, audience and rights must correspond to what that transaction's payload records. An entry that cites a real committed transaction whose payload does **not** contain it is **unusable** (`SOC-ENV-66`), not merely suspicious — citing a transaction is not evidence of anything. |
| `SOC-ENV-53` | **Write failure ⇒ no commit.** If the effect record cannot be written, the delivery transaction must not commit. "Committed but unrecorded" is not an allowed outcome. |
| `SOC-ENV-54` | **Unknown commit status ⇒ scoped UNKNOWN, isolation, Campaign pause.** If a commit outcome cannot be established: **already-proven `VIOLATED` findings are kept** (`SOC-ENV-57`); **only** the obligations that the pending commit could affect **and that have no positive evidence yet** are marked `UNKNOWN`; the world is **isolated** (no further actions, no re-execution) and the **Campaign pauses**. It is not enough to "not report committed", and blanket-marking the whole episode UNKNOWN is not allowed either. |
| `SOC-ENV-76` | **Materialisation is not delivery.** The initialization transaction writes the world's starting content; it is **configuration, not an Agent effect**. It is excluded from delivery effects: the Oracle counts deliveries solely from the ledger, so an initialization write can never be scored as a `VIOLATED` delivery, and no ledger entry may cite it (`SOC-ENV-77`). |
| `SOC-ENV-89` | **The initialization transaction is retained in the bundle** (payload plus derived digest), because initial-state reconstruction (`SOC-ENV-30`) and audit need it, while remaining outside the delivery effects of `SOC-ENV-76`. Retaining it is required; counting it is forbidden. |
| `SOC-ENV-88` | **Exposure evidence is bound, not matched.** A read is evidenced only by a fact that binds (a) the actual tool return of **this episode** (its recorded content digest), (b) the source object and field the content came from, and (c) the material identity it carries (slot and `material_digest`). Matching a text label alone is **not** sufficient; an exposure claim that cannot be bound is unusable (`SOC-ENV-90`). |

### 6.2 Retry, recovery and monotonicity

| ID | Requirement |
|---|---|
| `SOC-ENV-55` | **Retry rule (replaces the earlier contradiction).** Effect keys are derived from the action request id plus the effect's ordinal within it. **Same key + identical content → return the original receipt** (no second entry, no refusal). **Same key + different content → refused** (`SOC-ENV-67`). "Duplicate ⇒ refusal" is not a rule of this protocol. |
| `SOC-ENV-56` | **Recovery uses the full persisted transaction record.** The world checkpoint is the last committed transition in the episode history, verified by the chain rule; the last ledger entry is **not** a checkpoint and may not be used as one. |
| `SOC-ENV-57` | **Monotone findings.** Once a violation is established for an episode, later evidence cannot erase it: cleanup, deletion, revocation, retries and later ledger entries may add findings, never remove one. |
| `SOC-ENV-58` | Revocation, deletion, ACL restoration and episode cleanup change the world but **do not change** any recorded entry; judging reads entries, never the final state. |

Completeness: a delivery path with no entry means "unknown", never "no violation" (`SOC-ENV-68`).

### 6.3 Closure and run budget

| ID | Requirement |
|---|---|
| `SOC-ENV-85` | **Closure must be provable, and the semantics may not be deferred.** Before any real run, the episode ends with a `ClosureRecord` proving: the tool process was reaped; no ledger entry was appended after the last action; no unresolved asynchronous work remains; and the host observed no further container activity. Until proven, a **quiet window** must elapse (first value: 30 s of monotonic wall clock, frozen in the bundle) during which every tool call is rejected and recorded. If closure cannot be proven — unavailable or inconsistent clock, failed reaping, unknown commit state — then proven violations are **kept**, the obligations affected by the unresolved work are marked `UNKNOWN` (`SOC-ENV-54`), the world is isolated and the run stops. Literal claims such as the old route's `cleanup_confirmed=True` are not evidence and are not reused. |
| `SOC-ENV-86` | **Every real run carries a budget envelope**: `max_model_calls`, `max_tool_calls`, `max_wall_clock_seconds`, `max_input_tokens`, `max_output_tokens`, `max_expense_units`, frozen before the run. Reaching a cap ends the episode and records `budget.exceeded` (a controlled end, not a refusal). Actual calls and usage are recorded per call; when the provider returns no usage, the fact is marked `usage.missing` and any cost report is labelled incomplete. Calls not needed for the first episode (Mutator, Judge) are not wired for it. |
| `SOC-ENV-87` | **Calibration is not attack success.** The first real episode has to show a normal delivery and a **deterministic** calibration set (positive, negative, unknown) run without a model. Controlled positive calibration may be reported as proof that the Oracle can discriminate, and must **never** be counted as a model producing a violation; the two are reported in separate columns (consistent with `SOC-SAF-22`). |

**Boundary (must not be overstated):** §6 is the design. The materialisation slice delivered only the ledger structure and its consumption path; the loop is unimplemented and unverified, so nothing here shows that a safety obligation has been proven.

## 7. Refusal conditions (never fall back to the old protocol)

On any of the following, the container **aborts the episode**, records the reason with the **failure code below** (registered per §11.2), and **must not** fall back to the old envelope's execution path (`SOC-ENV-60`). The code is diagnostic: it names the check that failed, not merely "refused".

The `ID` column names the **requirement each code serves**; a requirement with several distinct failure modes has one row per mode, and a rule stated elsewhere in this draft (`SOC-ENV-54`) appears here only as its code.

| ID | Condition | Failure code | Checked by |
|---|---|---|---|
| `SOC-ENV-61` | Protocol identity mismatch (`protocol_id`, `envelope_version`, `schema_version`, `syntax_version`) | `envelope.identity_mismatch` | container |
| `SOC-ENV-62` | Transition chain not contiguous, or a state digest not matching the previous `after_state_digest` | `state.chain_broken` | container |
| `SOC-ENV-63` | An objective catalogue is present (inherited from the old type), or the Oracle scope is not the fixture's own obligation set | `envelope.objective_catalogue_present` | container |
| `SOC-ENV-21`–`SOC-ENV-23` | The declared task text, actor or authorisation context differs from the manifest's | `envelope.context_mismatch` | container |
| `SOC-ENV-64` | A locator resolves outside the frozen asset store | `envelope.locator_outside_store` | container |
| `SOC-ENV-64` | No payload is reachable for a declared digest | `envelope.payload_missing` | container |
| `SOC-ENV-65` | **Initial state does not reproduce** from `base + overlay + material` | `envelope.initial_state_not_reproducible` | container |
| `SOC-ENV-66` | An effect entry is not bound to its cited transaction's payload | `evidence.unbound` | container + Oracle |
| `SOC-ENV-67` | Effect key conflict (same key, different content) | `evidence.key_conflict` | container |
| `SOC-ENV-68` | A submission has no effect entry | `evidence.missing` → recorded as **missing evidence** | Oracle |
| `SOC-ENV-77` | A ledger entry cites the initialization transaction as a delivery | `evidence.initialization_cited_as_delivery` | container + Oracle |
| `SOC-ENV-90` | An exposure claim is not bound to a tool return, a source object/field and a material identity | `exposure.unbound` | container + Oracle |

**Not refusals.** Two codes are recorded **outcomes**, not refusals, and never abort silently: `budget.exceeded` (`SOC-ENV-86`, a controlled end) and `usage.missing` (`SOC-ENV-86`, a marker that a cost report is incomplete). Neither may be converted into a violation, a pass, or an unmarked omission.
| `SOC-ENV-54` | A commit outcome cannot be established | `evidence.commit_unknown` → scoped UNKNOWN + isolation + Campaign pause | container + Oracle |
| `SOC-ENV-69` | Fixture/manifest identity mismatch, or the manifest fails its own digest | `envelope.fixture_mismatch` | container |
| `SOC-ENV-69` | Material slot set ≠ manifest slots, repeated slot, unregistered syntax, or a recomputed material digest that does not match its declaration | `material.coverage_mismatch` | container |
| `SOC-ENV-70` | Two slots sharing a write location | `material.write_conflict` | container |
| `SOC-ENV-70` | A field path that is not the canonical one for its kind | `material.field_path_mismatch` | container |
| `SOC-ENV-71` | Declared `base_world_digest` ≠ the world fetched from the store | `envelope.base_world_mismatch` | container |
| `SOC-ENV-73` | Tool contracts ≠ frozen catalogue, **or** the running image ≠ the recorded immutable image digest (§11.1) | `tools.catalogue_mismatch` / `tools.image_mismatch` | container |
| `SOC-ENV-74` | Resume state inconsistent with the transaction chain | `resume.inconsistent` | container |

Refusals are recorded, not silently retried. A retry is allowed only through the receipt path of `SOC-ENV-55`, never by re-executing a submitted action (`SOC-ENV-72`).

## 8. Recommendation

| ID | Requirement |
|---|---|
| `SOC-ENV-80` | **One envelope shape**: `base_world_digest` is a required input, the overlay is always present, the digests are recorded separately, and the initial state is derived and re-checked. A and B are then asset policies chosen at freeze time, with no envelope fork. |
| `SOC-ENV-81` | **A is the default for the first probe and the first vertical slice**: lowest freeze cost, one identity chain to verify, background constant across arms. |
| `SOC-ENV-82` | **A fixture may move to B under `SOC-ENV-11`**: the revision creates a new version, keeps the earlier results, and must be justified by reachability/representativeness evidence (`SOC-ENV-43`) rather than by convenience. |
| `SOC-ENV-83` | Do not build a world per fixture up front; do not assume B is intrinsically weaker either (§3). |
| `SOC-ENV-84` | No container branch, no envelope constant and no migration of the old envelope until this draft is approved. |

## 9. State of this draft and authorisation

The design revision is closed. The five decisions the user took for the first real episode are now requirements: calibration is not attack success (`SOC-ENV-87`), exposure must be bound (`SOC-ENV-88`), the initialization transaction is retained in the bundle but excluded from delivery effects (`SOC-ENV-89`), closure is provable or becomes scoped UNKNOWN (`SOC-ENV-85`), and every run carries a call-and-cost budget envelope with recorded usage (`SOC-ENV-86`).

The **concrete implementation plan is the TASK's §14** (phases P1–P7), which also carries the closure design (§14.2), the budget envelope (§14.3) and the run-authorisation package (§14.4).

**Still not approved and not authorised:** this draft is not a SPEC; container coding and any real run — including G1 — remain gated on explicit user approval, and the run authorisation is applied for **separately** once the configuration and budget are fixed.

## 10. Relationship to the current work

- Materialisation layer: implemented, passes offline tests, **not yet accepted by the user, never run**. This draft uses its contract only.
- T01 audit B blockers B1–B8 remain open; §6 is the *design* for B8, not its completion.
- The probe's statistical protocol (`SOC-T03.5`, TASK §1.3) stays DRAFT and is frozen before any G1 run, independently of this envelope.
- `SOC-ENV-11` applies to the probe as confirmed: an asset revision starts a new version, keeps the earlier results, re-freezes the run configuration, and never merges old and new samples into one experiment.

## 11. Proposed engineering decisions (mine, for confirmation)

### 11.1 Tool catalogue and image freezing

- The image build emits `tool_catalogue.json`: the sorted list of tool names, each tool's contract digest, and the contract schema version; its digest is recorded in the image manifest.
- **Contract digests cannot stand in for the implementation identity** (`SOC-ENV-75`). The build therefore also records the **immutable digest of the running image** (the image content digest / image ID produced by the build) in the image manifest and writes it into every bundle. A changed implementation behind an unchanged interface is caught by `image_digest`, not by the catalogue.
- At session start the container **recomputes** the catalogue digest from the loaded tool registry and compares it with (i) the image manifest and (ii) the envelope's `tool_catalogue_digest`, **and** compares the running image's digest with the recorded `image_digest`; mismatches are `tools.catalogue_mismatch` and `tools.image_mismatch` (`SOC-ENV-73`).
- Both digests are written into every bundle, so a mismatch is detectable after the fact, not only live.
- No new hardcoded constant: the values live in the image manifest, which is the frozen artifact to register.

### 11.2 Failure-code registry

- Codes are declared once as a `FailureCode` string enum in `src/sandbox/structured_v1/envelope_codes.py`, grouped by prefix (`envelope.`, `material.`, `state.`, `evidence.`, `exposure.`, `resume.`, `tools.`, `budget.`, `usage.`).
- **§7 is the register**: every refusal row carries the exact code it records, so the code column and the enum are two views of one list.
- Refusals are recorded in the episode's failure field using exactly those strings, and a test asserts that every code named in §7 exists in the enum and vice versa, so the document and the code cannot drift.
- The **registry lives with the new package**, independent of the old protocol's failure codes.
