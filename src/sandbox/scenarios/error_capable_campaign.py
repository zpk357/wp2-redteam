"""Real cross-Episode coverage feedback (`RA-CLOSE-02`).

The loop this module runs:

    Episode N trace -> bridge -> observed coverage -> frozen feedback snapshot
        -> selector request for episode N+1 -> validated plan -> Agent

Three things are deliberately structural rather than asserted in prose:

* **the feedback is the complement of what was observed**, not the observed set (see
  `error_capable_coverage`), and the selector is handed a read-audited view of the ledger so
  "the random arm did not read history" is an observed fact, not a claim about the code;
* **the Agent never receives the feedback**.  The first input is rebuilt from the plan and material
  alone, and the alignment check compares the two arms' first-input digests;
* **coverage settles once per Episode id**.  A resumed Episode that reaches settlement a second time
  must not advance the ledger, because the ledger is what steers the next choice.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    PROVIDER_FAILURE_STOP_REASON,
    TASK_FAMILY_SPECS,
    AttackMode,
    CoverageFeedback,
    EpisodeKind,
    EpisodeScenarioPlan,
    ErrorCapableMode,
    MaterializedScenario,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    attack_spec,
    build_plan,
    materialize_scenario,
    task_family_spec,
)
from sandbox.scenarios.error_capable_agent import office_tool_specs, run_agent_episode
from sandbox.scenarios.error_capable_artifacts import read_artifact, write_artifact
from sandbox.scenarios.error_capable_bridge import bridge_trace
from sandbox.scenarios.error_capable_coverage import (
    CoverageLedger,
    MenuTargets,
    ObservedKey,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_priority import (
    PRIORITY_RULES_VERSION,
    CoverageIncrement,
    PriorityEvent,
    PriorityTable,
    UpdateClass,
    classify_opportunity,
    neighborhood_of,
    neighborhood_registry,
    score_for_event,
    score_rows,
)
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_ORACLE_CONTRACT_VERSION,
    ErrorCapableFixture,
)
from sandbox.scenarios.error_capable_selector import (
    LLMSelector,
    PureRandomSelector,
    SelectionRejected,
    SelectorAttempt,
    legal_combinations,
    refusable_cell,
    validate_choice,
)
from sandbox.scenarios.error_capable_world import planned_file_ids
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import OFFICE_V2_TOOL_NAMES

CAMPAIGN_VERSION = "error-capable-campaign-v5"
ALIGNMENT_VERSION = "error-capable-arm-alignment-v1"

#: Selection attempts per Opportunity before its Episode is forfeited.
#:
#: A refused selection used to consume the Opportunity outright -- `NP-08`'s frozen rule, and the
#: `Y_t = 0` of `NP-10`.  That rule assumes the selector can reliably name a fresh legal
#: combination.  A selector that cannot turns it into budget destruction rather than a treatment
#: cost: the v9 launch spent fourteen of the guided arm's sixteen Opportunities on refusals and
#: produced two Episodes, most of them lost to defects in the payload rather than to anything the arm
#: decided.
#:
#: A refusal is a rejected proposal, not a finished Episode, so it now costs an attempt instead of
#: the Episode.  It is still recorded, still fed forward into the next attempt's feedback, and still
#: counted as `refused` in the report.  The bound exists because at `temperature=0.0` an unanswered
#: question gets an unchanged answer: without a bound a wedged selector would call the provider for
#: ever, and the arm would never end.  When the attempts run out the Episode is forfeited and the arm
#: moves on.
#:
#: This revises the frozen selection rule at the operator's direction, and it changes what the
#: Opportunity denominator means, so `W / opportunities` and `W / |E|` have to be read beside the
#: attempt counts and a pair run under the old rule is not comparable with one run under this.
#: `CAMPAIGN_VERSION` moves with it, so a receipt written under the old rule cannot be restored into
#: a run using this one.
SELECTION_ATTEMPTS = 4


class HistorySentinel:
    """A read-audited view of the coverage ledger.

    The random arm is handed this instead of the ledger.  It answers nothing and records every
    attempt, so a random selector that reached for cross-Episode history would be caught by the
    record rather than by trusting that it did not.
    """

    def __init__(self, *, ledger: CoverageLedger, targets: MenuTargets, allow: bool) -> None:
        self._ledger = ledger
        self._targets = targets
        self._allow = allow
        self.reads: list[str] = []

    def feedback(self, *, purpose: str) -> CoverageFeedback | None:
        self.reads.append(purpose)
        if not self._allow:
            return None
        return self._ledger.feedback(self._targets)


class CampaignSelector(Protocol):
    """What a selector must provide.  Called once per Episode, in order."""

    name: str

    def __call__(
        self,
        request: SelectorRequest,
        history: HistorySentinel,
        *,
        episode_index: int,
    ) -> tuple[SelectorDecision, str]:
        """Return the validated decision and the raw response text it came from."""


class ScriptedSelector:
    """A deterministic selector, so the loop's guarantees can be tested without a provider.

    Guided reads the feedback the way the treatment is asked to: the combinations already taken
    decide what is left, and the recorded violation counts decide which of what is left is worth
    spending an opportunity on.  Its rationale moves with the feedback, because a selector whose
    rationale does not move when the feedback moves would be feedback used for reporting rather than
    for steering.  Campaign random mode bypasses this selector.
    """

    name = "error-capable-scripted-selector-v1"

    def __init__(
        self,
        *,
        path_ids: Mapping[str, Sequence[str]],
        attacks: Sequence[str],
        carriers: Sequence[str] = (),
    ) -> None:
        self._paths = {key: tuple(value) for key, value in path_ids.items()}
        self._attacks = tuple(attacks)
        self._carriers = tuple(carriers)

    @staticmethod
    def _construct_from_feedback(
        request: SelectorRequest, snapshot: CoverageFeedback | None, episode_index: int
    ) -> dict[str, str] | None:
        """Build a legal combination the ledger has not taken, preferring where it has broken.

        Groups are ordered by recorded violations and then by the frozen menu's own order, and the
        tie is broken by rotating over the surviving groups with the Episode index.  The rotation is
        what carries the property that matters: with an empty history -- every group level at zero
        violations -- taking the first group every time would send the whole run into whichever
        family the menu enumerates first, which is the failure the treatment's prompt was rewritten
        to avoid.  This is a contract double, not the treatment: the treatment is the model.
        """

        if snapshot is None:
            return None
        taken = {tuple(sorted(cell.items())) for cell in snapshot.chosen_menu_cells}
        remaining = [
            choice
            for choice in legal_combinations(request)
            if tuple(sorted(choice.items())) not in taken
        ]
        if not remaining:
            return None
        by_family = {
            str(row["task_family"]): int(row["violations"])  # type: ignore[call-overload]
            for row in snapshot.family_outcomes
        }
        by_mechanism = {
            str(row["attack_mode"]): int(row["violations"])  # type: ignore[call-overload]
            for row in snapshot.mechanism_outcomes
        }
        groups: dict[tuple[str, str], list[dict[str, str]]] = {}
        for choice in remaining:
            key = (choice["task_family"], choice["attack_mode"])
            groups.setdefault(key, []).append(choice)
        family_rank = {
            item.value: index for index, item in enumerate(request.available_task_families)
        }
        attack_rank = {item.value: index for index, item in enumerate(request.available_attacks)}
        order = sorted(
            groups,
            key=lambda key: (
                -(by_family.get(key[0], 0) + by_mechanism.get(key[1], 0)),
                family_rank.get(key[0], len(family_rank)),
                attack_rank.get(key[1], len(attack_rank)),
            ),
        )
        best = by_family.get(order[0][0], 0) + by_mechanism.get(order[0][1], 0)
        tied = [
            key
            for key in order
            if by_family.get(key[0], 0) + by_mechanism.get(key[1], 0) == best
        ]
        group = tied[episode_index % len(tied)]
        cells = groups[group]
        return cells[(episode_index // len(tied)) % len(cells)]

    def __call__(
        self,
        request: SelectorRequest,
        history: HistorySentinel,
        *,
        episode_index: int,
    ) -> tuple[SelectorDecision, str]:
        families = [item.value for item in request.available_task_families]
        attacks = [item.value for item in request.available_attacks]
        if request.mode is ErrorCapableMode.GUIDED:
            snapshot = history.feedback(purpose=f"guided-select-{episode_index}")
            cell = self._construct_from_feedback(request, snapshot, episode_index)
            if cell is not None:
                family, variant, path = cell["task_family"], cell["task_variant"], cell["path_id"]
                attack, carrier, layout = (
                    cell["attack_mode"],
                    cell["attack_carrier"],
                    cell["layout_id"],
                )
                rationale = (
                    f"constructed unobserved combination {cell} from guided feedback; that"
                    " direction carries the most recorded violations"
                )
            else:
                family = families[episode_index % len(families)]
                path = self._paths[family][0]
                attack = attacks[episode_index % len(attacks)]
                variants = tuple(
                    v.variant_id for v in task_family_spec(TaskFamily(family)).variants
                )
                variant = variants[episode_index % len(variants)]
                carrier = next(
                    c
                    for c in request.available_carriers
                    if c in attack_spec(AttackMode(attack)).carriers
                )
                layout = request.available_layouts[episode_index % len(request.available_layouts)]
                rationale = "menu exhausted; rotating open behaviour and risk observations"
        else:
            family = families[request.seed % len(families)]
            attack = self._attacks[(request.seed + episode_index) % len(self._attacks)]
            path = self._paths[family][episode_index % len(self._paths[family])]
            variants = tuple(
                item.variant_id for item in task_family_spec(TaskFamily(family)).variants
            )
            variant = variants[(request.seed + episode_index) % len(variants)]
            carrier = next(
                c
                for c in request.available_carriers
                if c in attack_spec(AttackMode(attack)).carriers
            )
            layout = request.available_layouts[
                (request.seed + episode_index) % len(request.available_layouts)
            ]
            rationale = "independent selection, no coverage feedback"

        decision = SelectorDecision(
            task_family=TaskFamily(family),
            task_variant=variant,
            path_id=path,
            attack_mode=AttackMode(attack),
            attack_carrier=carrier,
            layout_id=layout,
            episode_kind=EpisodeKind.ATTACK,
            rationale=rationale,
        )
        raw = decision.model_dump_json()
        return decision, raw


class PinnedSelector:
    """Emits one fixed condition for every opportunity, varying a single named field.

    The control for a mechanism comparison (`MW-AC-06`): everything except the varied field is held
    fixed, so a difference between two runs of it is attributable to that field and not to the
    family, variant, path, layout, carrier, budget or menu.  It reads no feedback and consults no
    model, which is why the guard admits it by name rather than treating it as guided selection.
    """

    name = "pinned-condition-v1"

    def __init__(self, fields: Mapping[str, str], *, vary: str) -> None:
        if vary not in fields:
            raise ValueError("the varied field must be part of the pinned condition")
        self.fields = dict(fields)
        self.vary = vary
        #: The name is part of the campaign's selection identity, and the identity decides whether a
        #: frozen selection is reused.  A constant name made two probes with different mechanisms
        #: share an identity, so the second silently executed the first one's frozen decision: the
        #: comparison ran one mechanism three times and reported it as three mechanisms.  The name
        #: therefore names the varied field and its value.
        self.name = f"{type(self).__name__.lower()}:{vary}={fields[vary]}"
        self.last_attempt = None

    def __call__(
        self, request: SelectorRequest, history: HistorySentinel, *, episode_index: int
    ) -> tuple[SelectorDecision, str]:
        del history, episode_index
        decision = validate_choice(
            request,
            SelectorDecision(
                **self.fields,
                rationale=(
                    f"pinned condition; only {self.vary} varies between probes and no feedback"
                    " is used"
                ),
            ),
        )
        return decision, decision.model_dump_json()


class SelectorReceipt(OfficeV2Contract):
    """One selector call, kept whole so the arms can be compared field by field."""

    version: str = CAMPAIGN_VERSION
    episode_index: int = Field(ge=0)
    mode: ErrorCapableMode
    selector_name: str
    request: SelectorRequest
    request_digest: Sha256Digest
    #: The request with selection policy and feedback removed, over
    #: the fields that must be identical across arms: the same menu, model identity and
    #: the same seed. Digesting the whole request would compare the arms on the variable
    #: itself and report a difference that is the experiment working as intended.
    blind_request_digest: Sha256Digest
    raw_response: str
    response_digest: Sha256Digest
    decision: SelectorDecision
    plan_digest: Sha256Digest
    model_identity_digest: Sha256Digest
    feedback_digest: Sha256Digest | None = None
    history_reads: tuple[str, ...] = ()
    receipt_digest: Sha256Digest

    @classmethod
    def seal(
        cls,
        *,
        episode_index: int,
        mode: ErrorCapableMode,
        selector_name: str,
        request: SelectorRequest,
        raw_response: str,
        decision: SelectorDecision,
        plan_digest: str,
        model_identity_digest: str,
        history_reads: Sequence[str],
    ) -> SelectorReceipt:
        feedback = request.feedback
        fields: dict[str, Any] = {
            "episode_index": episode_index,
            "mode": mode,
            "selector_name": selector_name,
            "request": request,
            "request_digest": sha256_digest(request.model_dump(mode="json")),
            "blind_request_digest": sha256_digest(
                {
                    "seed": request.seed,
                    "agent_model_name": request.agent_model_name,
                    "available_task_families": [
                        item.value for item in request.available_task_families
                    ],
                    "available_attacks": [item.value for item in request.available_attacks],
                    "available_task_variants": request.available_task_variants,
                    "available_paths": request.available_paths,
                    "available_carriers": request.available_carriers,
                    "available_layouts": request.available_layouts,
                }
            ),
            "raw_response": raw_response,
            "response_digest": sha256_digest({"raw_response": raw_response}),
            "decision": decision,
            "plan_digest": plan_digest,
            "model_identity_digest": model_identity_digest,
            "feedback_digest": (
                None if feedback is None else sha256_digest(feedback.model_dump(mode="json"))
            ),
            "history_reads": tuple(history_reads),
        }
        placeholder = cls.model_construct(**fields, receipt_digest="sha256:" + "0" * 64)
        return cls(**fields, receipt_digest=sha256_digest(placeholder.digest_payload()))

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_digest"}, exclude_none=False)


class CampaignEpisodeRecord(OfficeV2Contract):
    """Everything one Episode of the campaign produced, with the links back to its evidence."""

    version: str = CAMPAIGN_VERSION
    index: int = Field(ge=0)
    mode: ErrorCapableMode
    episode_id: Identifier
    selector: SelectorReceipt
    first_input_digest: Sha256Digest
    tool_menu_digest: Sha256Digest
    trace_digest: Sha256Digest
    bridge_digest: Sha256Digest
    stop_reason: str
    observed: ObservedKey | None = None
    #: True only the first time this Episode id settles, so the ledger advances exactly once.
    settled_coverage: bool = False
    unresolved: tuple[str, ...] = ()


class CampaignReport(OfficeV2Contract):
    """One arm's campaign: the plans it chose, the runs it produced and the coverage it moved."""

    version: str = CAMPAIGN_VERSION
    mode: ErrorCapableMode
    fixture_id: Identifier
    fixture_freeze_digest: Sha256Digest
    model_identity_digest: Sha256Digest
    max_tool_requests: int = Field(ge=0)
    tool_menu_digest: Sha256Digest
    tool_names: tuple[Identifier, ...]
    targets: MenuTargets
    episodes: tuple[CampaignEpisodeRecord, ...] = ()
    ledger: CoverageLedger = Field(default_factory=CoverageLedger)
    sentinel_reads: tuple[str, ...] = ()
    selection_attempts: tuple[SelectorAttempt, ...] = ()
    opportunities: int = Field(default=0, ge=0)
    rejected_opportunities: tuple[dict[str, Any], ...] = ()
    #: The guided arm's scores at the end of the run, in registry order.  `None` for the random arm,
    #: which keeps no score state at all, and for records written before this field existed.  The
    #: settlements on disk remain the source: this is the same table they replay into.
    priority: PriorityTable | None = None

    def feedback_used(self) -> tuple[str, ...]:
        return tuple(
            item.selector.history_reads for item in self.episodes if item.selector.history_reads
        )

    def selection_cost(self) -> dict[str, Any]:
        unknown = sum(
            attempt.provider_calls > 0 and attempt.token_usage is None
            for attempt in self.selection_attempts
        )
        usage = {
            key: sum((attempt.token_usage or {}).get(key, 0) for attempt in self.selection_attempts)
            for key in ("prompt_tokens", "completion_tokens")
        }
        return {
            "provider_calls": sum(a.provider_calls for a in self.selection_attempts),
            "elapsed_ms": sum(a.elapsed_ms for a in self.selection_attempts),
            "tokens": usage if unknown == 0 else None,
            "known_tokens": usage,
            "token_usage_missing_attempts": unknown,
            "tokens_status": "complete" if unknown == 0 else "incomplete",
        }


#: How many times an Episode is run again after such a failure.
#:
#: A retry only reaches failures that are actually transient.  The provider runs at
# : `temperature=0.0`, which is greedy decoding: the seed does not steer it, and a malformed
# generation
#: is a property of the prompt rather than of the sampling.  A repetition of this exact failure was
#: observed -- the same prompt produced the same broken tool call twice, twelve seconds apart, with
#: the same parser error -- so changing the seed on a retry was considered and rejected: it would
#: have changed the recorded seed of a retried Episode without changing what the retry does.  What
#: the attempt count is for is the other kind of failure: a timeout, a dropped connection, a service
#: that is briefly unavailable.
#:
#: Each attempt gets **its own journal directory**, because one shared path means the second attempt
#: finds the first one's settled failure and resumes it instead of running, and then the third and
# : fourth do the same -- four attempts that are one call. The directory is named for the attempt,
# so
#: the discarded attempts stay auditable and none of them can resume another.
PROVIDER_FAILURE_RETRIES = 3


def _episode_id(mode: ErrorCapableMode, index: int) -> str:
    arm = "guided" if mode is ErrorCapableMode.GUIDED else "random"
    return f"campaign.{arm}.{index:03d}"


async def _run_campaign(
    *,
    fixture: ErrorCapableFixture,
    mode: ErrorCapableMode,
    episodes: int,
    adapter: Any,
    selector: CampaignSelector,
    model_identity: ModelIdentity,
    seed: int,
    max_tool_requests: int = 24,
    journal_root: Any | None = None,
    ledger: CoverageLedger | None = None,
) -> CampaignReport:
    """Run Episodes; only guided uses coverage observations to steer its next choice."""

    if episodes < 1:
        raise ValueError("a campaign needs at least one Episode")
    from sandbox.scenarios.error_capable_registry import ERROR_CAPABLE_FIXTURE_ID

    if fixture.fixture_id != ERROR_CAPABLE_FIXTURE_ID:
        raise ValueError("new material cannot execute under a historical fixture identity")
    if mode is ErrorCapableMode.RANDOM:
        selector = PureRandomSelector()
    if isinstance(selector, LLMSelector):
        if (
            selector.adapter is not adapter
            or selector.model_identity.identity_digest != model_identity.identity_digest
        ):
            raise ValueError(
                "selector and Agent must share the same adapter and full model identity"
            )
    elif not isinstance(selector, (ScriptedSelector, PureRandomSelector, PinnedSelector)):
        raise ValueError(
            "guided requires an LLMSelector, an explicit contract ScriptedSelector, or a"
            " PinnedSelector control"
        )
    manifest = fixture.manifest
    targets = MenuTargets.from_manifest(
        manifest, path_ids={spec.task_family.value: spec.path_ids for spec in TASK_FAMILY_SPECS}
    )
    specs = office_tool_specs()
    menu_digest = sha256_digest({"tools": [spec.name for spec in specs]})
    state = ledger or CoverageLedger()
    records: list[CampaignEpisodeRecord] = []
    sentinel_reads: list[str] = []
    selection_attempts: list[SelectorAttempt] = []
    rejected: list[dict[str, Any]] = []
    guided = mode is ErrorCapableMode.GUIDED
    #: Combinations this arm proposed and the run refused, carried forward into every later feedback
    # : snapshot. A refusal that is not handed back leaves the next question identical to the one
    # that
    #: produced it; at `temperature=0.0` the same question gets the same answer, so the first
    #: duplicate would spend every remaining opportunity instead of one.
    refused_cells: list[dict[str, str]] = []
    registry = neighborhood_registry(
        {spec.task_family.value: spec.path_ids for spec in TASK_FAMILY_SPECS}
    )
    settlements_dir = None if journal_root is None else Path(journal_root) / "settlements"
    #: The guided arm's scores.  Random keeps none: `NP-04` gives it no score state that could
    #: influence a choice, and keeping an unused table would only be one more thing to drift.
    table = PriorityTable.initial(registry) if guided else None

    for index in range(episodes):
        episode_id = _episode_id(mode, index)
        artifact_path = (
            None if journal_root is None else Path(journal_root) / f"{episode_id}.selection.json"
        )
        identity_digest = sha256_digest(
            {
                "version": CAMPAIGN_VERSION,
                "fixture": fixture.freeze_digest,
                "model": model_identity.identity_digest,
                "adapter": adapter.version,
                "selector": selector.name,
                "seed": seed + index,
                "mode": mode.value,
                "max_tool_requests": max_tool_requests,
                "targets": targets.model_dump(mode="json"),
            }
        )
        frozen = None
        if artifact_path is not None and artifact_path.exists():
            frozen = read_artifact(artifact_path, identity=identity_digest)
            # Attempts before the accepted one are on disk under their own names.  Their refusals go
            # back into the set before the request is rebuilt, or a resumed run would ask a question
            # the frozen run never asked and the digest check would refuse to continue.
            refused_attempts: list[dict[str, Any]] = []
            for earlier in sorted(
                artifact_path.parent.glob(f"{episode_id}.selection.attempt-*.json")
            ):
                record = read_artifact(earlier, identity=identity_digest)
                if record.get("status") == "rejected":
                    refused_attempts.append(record)
                more = refusable_cell(record.get("refused_coordinate"))
                if more is not None and more not in refused_cells:
                    refused_cells.append(more)
            if frozen["status"] == "selection_pending":
                frozen["status"] = "rejected"
                frozen["rejection"] = (
                    "selection interrupted; opportunity consumed without replacement"
                )
                write_artifact(artifact_path, frozen)
            if frozen["status"] == "rejected":
                # Every refused attempt is replayed, not only the one that forfeited the Opportunity.
                # A refusal is an attempt now, the run stores one record per attempt, and a resume
                # that collapsed them into a single entry would report a different Campaign from the
                # one it resumed -- fewer refusals, fewer attempts, and a denominator it cannot be
                # compared with.  The canonical receipt holds the last of them, so the attempt files
                # are the list; it falls back to the canonical one only when there are none, which is
                # the shape a receipt interrupted before its first attempt has.
                replayed = refused_attempts or [frozen]
                for record in replayed:
                    rejected.append(record)
                    if record.get("attempt"):
                        selection_attempts.append(
                            SelectorAttempt.model_validate(record["attempt"])
                        )
                sentinel_reads.extend(replayed[-1].get("history_reads", ()))
                # A resumed refusal is still a refusal the later offers have to know about, or the
                # resumed run would hand the selector the same question the frozen run did.
                # Through the gate: a resumed refusal that is not a combination must not reach a
                # field declared to hold combinations, on this pass either.
                refused = refusable_cell(frozen.get("refused_coordinate"))
                if refused is not None:
                    refused_cells.append(refused)
                if table is not None:
                    # A refusal is an opportunity like any other, so a resumed run has to replay its
                    # settlement too -- the scores are the ordered events, and skipping one would
                    # put
                    # every later score one step out.
                    table = _settle_without_episode(
                        table=table,
                        settlements_dir=settlements_dir,
                        episode_id=episode_id,
                        index=index,
                        mode=mode,
                        identity=identity_digest,
                        reason=str(frozen.get("rejection") or "refused selection"),
                    )
                continue
        # The sentinel is rebuilt per Episode because what must be audited is this Episode's read.
        sentinel = HistorySentinel(ledger=state, targets=targets, allow=guided)
        # Built once and re-cut per attempt: the only thing that moves between attempts is
        # `rejected_menu_cells`, so re-reading the ledger for each one would record reads the arm
        # never made.
        feedback_base = sentinel.feedback(purpose=f"request-{index}") if guided else None
        feedback_digest: str | None = None
        if feedback_base is not None:
            updates: dict[str, Any] = {}
            if table is not None:
                # The scores are joined onto the ledger's remaining counts here rather than inside
                # the ledger, because a score is a history of results and not a coverage count, and
                # merging them into one number is the mistake `NP-02` names.
                updates["neighborhood_scores"] = score_rows(
                    table, feedback_base.unobserved_by_neighborhood
                )
            if updates:
                feedback_base = feedback_base.model_copy(update=updates)
        decision: SelectorDecision | None = None
        raw_response = ""
        failure: dict[str, Any] | None = None
        request: SelectorRequest | None = None
        # A refusal costs an attempt, not the Episode (`SELECTION_ATTEMPTS`).  Every attempt asks the
        # question again with the refusal just recorded added to it, because at `temperature=0.0` a
        # question that does not change gets an answer that does not change either.
        for attempt_no in range(SELECTION_ATTEMPTS):
            feedback = feedback_base
            if feedback is not None and refused_cells:
                feedback = feedback.model_copy(
                    update={"rejected_menu_cells": tuple(refused_cells)}
                )
            feedback_digest = (
                None if feedback is None else sha256_digest(feedback.model_dump(mode="json"))
            )
            request = SelectorRequest(
                mode=mode,
                seed=seed + index,
                agent_model_name=model_identity.normalized_model_id,
                selector_model_name=model_identity.normalized_model_id if guided else None,
                available_task_families=tuple(TaskFamily(item) for item in targets.families),
                available_task_variants=tuple(
                    v.variant_id for family in TaskFamily for v in task_family_spec(family).variants
                ),
                available_paths=tuple(
                    path for spec in TASK_FAMILY_SPECS for path in spec.path_ids
                ),
                available_attacks=tuple(AttackMode(item) for item in targets.attacks),
                available_carriers=tuple(
                    dict.fromkeys(
                        c for mode_item in AttackMode for c in attack_spec(mode_item).carriers
                    )
                ),
                available_layouts=("balanced-8", "distributed-10", "nested-12"),
                # The unobserved-combination contract is the guided treatment's, not a property of
                # every run that happens to be in guided mode.  The pinned control deliberately
                # repeats one fixed condition to compare mechanisms, so it is exempt rather than the
                # rule being weakened for the arm the comparison is about.
                require_unobserved=guided and not isinstance(selector, PinnedSelector),
                feedback=feedback,
            )
            if frozen is not None:
                # The frozen path is one pass.  The request is rebuilt before the comparison because
                # the refusals that preceded the accepted attempt are re-collected from the attempt
                # records above, so the rebuilt request is the one that was accepted.
                if frozen["request_digest"] != sha256_digest(request.model_dump(mode="json")):
                    raise ValueError(
                        "restored selection feedback or menu differs from frozen request"
                    )
                decision = SelectorDecision.model_validate(frozen["decision"])
                raw_response = frozen["raw_response"]
                if frozen.get("attempt"):
                    selection_attempts.append(SelectorAttempt.model_validate(frozen["attempt"]))
                sentinel.reads = list(frozen["history_reads"])
                break
            pending = {
                "identity": identity_digest,
                "status": "selection_pending",
                "index": index,
                # Named apart from the `attempt` key a refusal record carries: that one holds the
                # `SelectorAttempt`, this one says which attempt of the Opportunity it was.
                "selection_attempt": attempt_no,
                "request": request.model_dump(mode="json"),
                "request_digest": sha256_digest(request.model_dump(mode="json")),
                "history_reads": sentinel.reads.copy(),
            }
            attempt_path = (
                None
                if artifact_path is None
                else artifact_path.with_name(f"{episode_id}.selection.attempt-{attempt_no}.json")
            )
            if attempt_path is not None:
                write_artifact(attempt_path, pending)
            # The canonical name is the crash marker before the first attempt and the verdict after
            # the last one; the per-attempt names keep every refused attempt auditable.
            if attempt_no == 0 and artifact_path is not None:
                write_artifact(artifact_path, pending)
            try:
                selected = selector(request, sentinel, episode_index=index)
                if inspect.isawaitable(selected):
                    selected = await selected
                decision, raw_response = selected
                validate_choice(request, decision)
                failure = None
                break
            except (SelectionRejected, ValueError) as exc:
                attempt = getattr(selector, "last_attempt", None)
                coordinate = getattr(exc, "coordinate", None)
                # The gate, not `isinstance(coordinate, dict)`: a reply that left an axis empty is
                # refused and recorded, but it is not a combination and must not be appended to a
                # set of them.  Doing so killed a Campaign with a `ValidationError` on the next
                # request instead of costing one Opportunity.
                refused = refusable_cell(coordinate)
                if refused is not None and refused not in refused_cells:
                    refused_cells.append(refused)
                failure = {
                    **pending,
                    "status": "rejected",
                    "rejection": str(exc),
                    "attempt": None if attempt is None else attempt.model_dump(mode="json"),
                    "history_reads": sentinel.reads.copy(),
                    # Kept so a resumed run still knows what this arm was refused, instead of asking
                    # the same question again and being refused in the same way.
                    "refused_coordinate": (
                        dict(coordinate) if isinstance(coordinate, dict) else None
                    ),
                }
                if attempt is not None:
                    selection_attempts.append(attempt)
                if attempt_path is not None:
                    write_artifact(attempt_path, failure)
                # Every refused attempt is reported.  A refusal is now an attempt rather than an
                # Opportunity, so this list is a list of refused attempts and each record carries
                # the `selection_attempt` it belonged to.
                rejected.append(failure)
                decision = None
        if decision is None:
            # Every attempt was refused, so the Episode is forfeited.  The refusal still settles: it
            # has no direction to score and must not be scored as if it had one, so it takes a `null`
            # neighborhood and no movement.
            if artifact_path is not None and failure is not None:
                write_artifact(artifact_path, failure)
            sentinel_reads.extend(sentinel.reads)
            if table is not None:
                reason = str((failure or {}).get("rejection") or "refused selection")
                table = _settle_without_episode(
                    table=table,
                    settlements_dir=settlements_dir,
                    episode_id=episode_id,
                    index=index,
                    mode=mode,
                    identity=identity_digest,
                    reason=reason,
                    feedback_digest=feedback_digest,
                    detail={"rejection": reason},
                )
            continue
        validate_choice(request, decision)
        attempt = getattr(selector, "last_attempt", None)
        if attempt is not None and frozen is None:
            selection_attempts.append(attempt)
        sentinel_reads.extend(sentinel.reads)

        family = next(item for item in manifest.task_families if item == decision.task_family.value)
        attack = next(item for item in manifest.attack_modes if item == decision.attack_mode.value)
        if not manifest.covers(family, decision.path_id, attack):
            # Fail closed: a decision outside the frozen space is refused, not materialised anyway.
            raise ValueError(f"selector chose a combination the fixture does not cover: {decision}")
        file_ids = planned_file_ids(episode_id, seed=seed + index, layout_id=decision.layout_id)
        plan = build_plan(
            request,
            decision,
            episode_id=episode_id,
            file_ids=file_ids,
            attack_carrier=decision.attack_carrier,
            model_name=model_identity.normalized_model_id,
            layout_id=decision.layout_id,
            task_file_id=file_ids[0],
        )
        material = materialize_scenario(plan)
        receipt = SelectorReceipt.seal(
            episode_index=index,
            mode=mode,
            selector_name=selector.name,
            request=request,
            raw_response=raw_response,
            decision=decision,
            plan_digest=sha256_digest(plan.model_dump(mode="json")),
            model_identity_digest=model_identity.identity_digest,
            history_reads=sentinel.reads,
        )
        if frozen is not None:
            restored_plan = EpisodeScenarioPlan.model_validate(frozen["plan"])
            restored_material = MaterializedScenario.model_validate(frozen["material"])
            if (
                restored_plan != plan
                or restored_material != material
                or frozen["receipt"] != receipt.model_dump(mode="json")
            ):
                raise ValueError("frozen material or receipt drifted; refuse to execute")
            plan, material = restored_plan, restored_material
        elif artifact_path is not None:
            write_artifact(
                artifact_path,
                {
                    "identity": identity_digest,
                    "status": "selected",
                    "index": index,
                    "request_digest": sha256_digest(request.model_dump(mode="json")),
                    "request": request.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                    "raw_response": raw_response,
                    "history_reads": sentinel.reads.copy(),
                    "attempt": None if attempt is None else attempt.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "material": material.model_dump(mode="json"),
                    "receipt": receipt.model_dump(mode="json"),
                },
            )

        attempts = PROVIDER_FAILURE_RETRIES + 1
        for attempt in range(attempts):
            if journal_root is None:
                store = None
            elif attempt == 0:
                store = _store_for(journal_root, episode_id)
            else:
                # A discarded attempt must not be resumed, so it gets its own directory, named for
                # the attempt.  The Episode id has to stay the same: the journal refuses to persist
                # a checkpoint whose episode_id does not match the store's, so a suffixed id would
                # fail on the first write instead of recording anything.
                store = _store_for(
                    Path(journal_root) / "retries" / f"attempt-{attempt}", episode_id
                )
            trace = await run_agent_episode(
                fixture=fixture,
                plan=plan,
                material=material,
                adapter=adapter,
                model_identity=model_identity,
                seed=plan.seed,
                max_tool_requests=max_tool_requests,
                journal=store,
                resume=store is not None and store.exists(),
            )
            if trace.stop_reason != PROVIDER_FAILURE_STOP_REASON:
                break
        # Falling out of the loop means every attempt failed, and that is an Episode outcome rather
        # than a reason to abandon the Campaign. The specification says so in as many words: a parse
        # failure is not an invalidity, it counts in the denominator and is reported separately. The
        # Opportunity is consumed by an Episode that produced no turn -- identifiable in the
        # artifact
        # by this stop reason and by an `unassessed` coverage key, the class for a run that never
        # looked -- and the discarded attempts stay in `retries/` beside it.
        #
        # Aborting here instead, which is what this used to do, throws away every valid Episode the
        # repetition had already produced. It did exactly that to the first repetition of the formal
        # experiment, at Episode twelve of sixteen, over two malformed generations.  The denominator
        # still counts this Opportunity, so nothing is quietly shrunk by carrying on.
        bridged = bridge_trace(trace, material=material, plan=plan)

        # The key is built from the trace and the material, not from the plan: which files the run
        # opened and which calls it proved are facts about the run, and a plan-based key would count
        # two different runs as one behaviour whenever they were planned alike.
        observed = ObservedKey.from_evidence(
            episode_id=episode_id,
            family=family,
            attack=attack,
            kind=decision.episode_kind.value,
            trace=trace,
            bridge=bridged,
            material=material,
        )
        if journal_root is not None:
            write_artifact(
                Path(journal_root) / f"{episode_id}.evidence.json",
                {
                    "identity": identity_digest,
                    "trace": trace.model_dump(mode="json"),
                    "bridge": bridged.model_dump(mode="json"),
                    "coverage": observed.model_dump(mode="json"),
                },
            )
        if table is not None:
            # The settlement is written after the Episode's own evidence, so an interruption between
            # the two leaves an Episode that ran with no settlement yet -- which the resume settles
            # exactly once, from the evidence that is already on disk.
            settlement_path = (
                None if settlements_dir is None else settlements_dir / f"{episode_id}.json"
            )
            stored = _load_settlement(
                settlement_path, index=index, identity=identity_digest, table=table
            )
            if stored is not None:
                # An Episode that already settled is replayed, not re-settled: this is the branch
                # that makes a resumed Campaign agree with the one it resumed.
                table, _changed = table.with_event(stored)
            else:
                before = table.digest()
                classification, reason = classify_opportunity(
                    episode_present=True, bridge=bridged, stop_reason=trace.stop_reason
                )
                event = score_for_event(
                    table,
                    PriorityEvent(
                        opportunity_id=episode_id,
                        episode_index=index,
                        mode=mode.value,
                        episode_id=episode_id,
                        neighborhood_id=neighborhood_of(
                            decision.task_family.value, decision.path_id
                        ),
                        cell=_cell_of(decision),
                        # What this Episode added to the coverage record, asked of the ledger before
                        # it is settled: after `settle` the profile it brought is in the ledger and
                        # every class would read `no_increment`.
                        increment=CoverageIncrement(state.classify_gain(observed)),
                        update_class=classification,
                        reason=reason,
                        evidence={
                            "trace_digest": trace.trace_digest,
                            "coverage_digest": observed.evidence_digest,
                        },
                    ),
                )
                table, _changed = table.with_event(event)
                _write_settlement(
                    settlement_path,
                    opportunity_id=episode_id,
                    index=index,
                    mode=mode,
                    identity=identity_digest,
                    status="settled",
                    cell=_cell_of(decision),
                    event=event,
                    table_before=before,
                    table_after=table.digest(),
                    feedback_digest=feedback_digest,
                    detail={
                        "stop_reason": trace.stop_reason,
                        "classification_reason": event.reason,
                    },
                )
        state, settled = state.settle(
            observed,
            cell=next(
                cell
                for cell, choice in zip(targets.cells, targets.choices, strict=True)
                if choice
                == {
                    "task_family": decision.task_family.value,
                    "task_variant": decision.task_variant,
                    "path_id": decision.path_id,
                    "attack_mode": decision.attack_mode.value,
                    "attack_carrier": decision.attack_carrier,
                    "layout_id": decision.layout_id,
                }
            ),
        )
        receipt = SelectorReceipt.seal(
            episode_index=index,
            mode=mode,
            selector_name=selector.name,
            request=request,
            raw_response=raw_response,
            decision=decision,
            plan_digest=sha256_digest(plan.model_dump(mode="json")),
            model_identity_digest=model_identity.identity_digest,
            history_reads=sentinel.reads,
        )
        records.append(
            CampaignEpisodeRecord(
                index=index,
                mode=mode,
                episode_id=episode_id,
                selector=receipt,
                first_input_digest=trace.first_input.payload_digest,
                tool_menu_digest=trace.first_input.tool_menu_digest,
                trace_digest=trace.trace_digest,
                bridge_digest=sha256_digest(bridged.model_dump(mode="json")),
                stop_reason=trace.stop_reason,
                observed=state.get(episode_id),
                settled_coverage=settled,
                unresolved=trace.unresolved,
            )
        )

    if settlements_dir is not None and table is not None:
        # A derived view, written for a reader who wants the end state without replaying the events;
        # the settlements remain the source, and the digest is what says whether the view is
        # current.
        snapshots = settlements_dir.parent / "priority-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        (snapshots / "final.json").write_text(
            json.dumps(
                {"table": table.model_dump(mode="json"), "table_digest": table.digest()},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    return CampaignReport(
        mode=mode,
        fixture_id=fixture.fixture_id,
        fixture_freeze_digest=fixture.freeze_digest,
        model_identity_digest=model_identity.identity_digest,
        max_tool_requests=max_tool_requests,
        tool_menu_digest=menu_digest,
        tool_names=tuple(spec.name for spec in specs),
        targets=targets,
        episodes=tuple(records),
        ledger=state,
        sentinel_reads=tuple(sentinel_reads),
        selection_attempts=tuple(selection_attempts),
        opportunities=episodes,
        rejected_opportunities=tuple(rejected),
        priority=table,
    )


async def run_campaign(**kwargs: Any) -> CampaignReport:
    """Guard selection artifacts and Episode journals with one campaign writer."""
    root = kwargs.get("journal_root")
    if root is None:
        return await _run_campaign(**kwargs)
    store = _store_for(root, f"campaign.{kwargs['mode'].value}")
    store.claim()
    try:
        report = await _run_campaign(**kwargs)
        write_artifact(
            Path(root) / f"campaign.{report.mode.value}.report.json",
            {
                "identity": report.fixture_freeze_digest,
                "report": report.model_dump(mode="json"),
            },
        )
        return report
    finally:
        store.release()


class ArmAlignment(OfficeV2Contract):
    """Compare the two arms field by field so mismatched conditions are visible."""

    version: str = ALIGNMENT_VERSION
    episodes: int = Field(ge=0)
    shared_model_identity: bool
    shared_tool_menu: bool
    shared_budget: bool
    shared_targets: bool
    agent_inputs_identical: bool
    blind_requests_identical: bool
    guided_received_feedback: bool
    random_received_feedback: bool
    random_read_history: bool
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def aligned(self) -> bool:
        return all(
            (
                self.shared_model_identity,
                self.shared_tool_menu,
                self.shared_budget,
                self.shared_targets,
                self.agent_inputs_identical,
                self.blind_requests_identical,
                self.guided_received_feedback,
                not self.random_received_feedback,
                not self.random_read_history,
            )
        )


def compare_arms(guided: CampaignReport, random: CampaignReport) -> ArmAlignment:
    """Check shared Agent conditions and menus under guided versus pure random selection."""

    random_by_index = {record.index: record for record in random.episodes}
    pairs = [
        (record, random_by_index[record.index])
        for record in guided.episodes
        if record.index in random_by_index
    ]
    complete = (
        bool(pairs)
        and guided.opportunities == random.opportunities
        and len(pairs) == guided.opportunities
    )
    agent_inputs = all(left.first_input_digest == right.first_input_digest for left, right in pairs)
    blind = all(
        left.selector.blind_request_digest == right.selector.blind_request_digest
        for left, right in pairs
    )
    return ArmAlignment(
        episodes=len(pairs),
        shared_model_identity=guided.model_identity_digest == random.model_identity_digest,
        shared_tool_menu=(
            guided.tool_menu_digest == random.tool_menu_digest
            and guided.tool_names == random.tool_names
        ),
        shared_budget=guided.max_tool_requests == random.max_tool_requests,
        shared_targets=guided.targets == random.targets,
        agent_inputs_identical=complete and agent_inputs,
        blind_requests_identical=complete and blind,
        guided_received_feedback=bool(guided.episodes)
        and all(item.selector.request.feedback is not None for item in guided.episodes),
        random_received_feedback=any(
            item.selector.request.feedback is not None for item in random.episodes
        ),
        random_read_history=bool(random.sentinel_reads),
        detail={
            "selection_policy": "guided_llm_vs_uniform_random",
            "random_provider_selection_calls": sum(
                a.provider_calls for a in random.selection_attempts
            ),
            "guided_provider_selection_calls": sum(
                a.provider_calls for a in guided.selection_attempts
            ),
            "guided_selection_elapsed_ms": sum(
                a.elapsed_ms for a in guided.selection_attempts
            ),
            "guided_history_reads": list(guided.sentinel_reads),
            "random_history_reads": list(random.sentinel_reads),
            "agent_input_digests": [item.first_input_digest for item in guided.episodes],
            "guided_choices": [
                [item.selector.decision.task_family.value, item.selector.decision.attack_mode.value]
                for item in guided.episodes
            ],
            "random_choices": [
                [item.selector.decision.task_family.value, item.selector.decision.attack_mode.value]
                for item in random.episodes
            ],
            "guided_rationales": [item.selector.decision.rationale for item in guided.episodes],
        },
    )


def oracle_contract_version() -> str:
    return ERROR_CAPABLE_ORACLE_CONTRACT_VERSION


def tool_catalogue_size() -> int:
    return len(OFFICE_V2_TOOL_NAMES)


def _store_for(root: Any, episode_id: str) -> Any:
    from sandbox.scenarios.error_capable_journal import JournalStore

    return JournalStore(root, episode_id)


def _cell_of(decision: SelectorDecision) -> dict[str, str]:
    """The six coordinates, in the order the menu writes them."""

    return {
        "task_family": decision.task_family.value,
        "task_variant": decision.task_variant,
        "path_id": decision.path_id,
        "attack_mode": decision.attack_mode.value,
        "attack_carrier": decision.attack_carrier,
        "layout_id": decision.layout_id,
    }


def _load_settlement(
    path: Path | None, *, index: int, identity: str, table: PriorityTable
) -> PriorityEvent | None:
    """The settlement already on disk for this opportunity, or `None` if there is not one yet.

    The checks are the ones a resumed Campaign must pass before it may carry a score forward, and
    each
    one is a way the scores could quietly become a different experiment: settled under other rules,
    a
    different identity (so a different seed, fixture or model), or not chaining onto the table as it
    stands at this point in the order.  Any of those stops the run rather than continuing from a
    guess, because a score that cannot be reproduced is not evidence of anything.
    """

    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != PRIORITY_RULES_VERSION:
        raise ValueError(
            f"{path.name} was settled under {payload.get('version')!r}, not"
            f" {PRIORITY_RULES_VERSION!r}; scores cannot be carried across a rule change"
        )
    if int(payload.get("episode_index", -1)) != index:
        raise ValueError(f"{path.name} is filed against a different opportunity index")
    if payload.get("identity") != identity:
        raise ValueError(
            f"{path.name} belongs to a different identity; refusing to carry its score into"
            " this run"
        )
    if payload.get("table_digest_before") != table.digest():
        raise ValueError(
            f"{path.name} does not chain onto the table as it stands; the event order or the"
            " table is wrong"
        )
    return PriorityEvent.model_validate(payload["event"])


def _settle_without_episode(
    *,
    table: PriorityTable,
    settlements_dir: Path | None,
    episode_id: str,
    index: int,
    mode: ErrorCapableMode,
    identity: str,
    reason: str,
    feedback_digest: str | None = None,
    status: str = "no_episode",
    detail: dict[str, Any] | None = None,
) -> PriorityTable:
    """Score an opportunity that produced no Episode: neutral, no neighborhood, no movement.

    A settlement already on disk is replayed and left alone; only a missing one is derived and
    written.  Rewriting a stored settlement would let a later run restate what an earlier one
    recorded, and the whole point of the record is that it did not change.
    """

    path = None if settlements_dir is None else settlements_dir / f"{episode_id}.json"
    stored = _load_settlement(path, index=index, identity=identity, table=table)
    if stored is not None:
        settled, _changed = table.with_event(stored)
        return settled
    before = table.digest()
    event = score_for_event(
        table,
        PriorityEvent(
            opportunity_id=episode_id,
            episode_index=index,
            mode=mode.value,
            update_class=UpdateClass.NEUTRAL,
            reason=reason,
        ),
    )
    settled, _changed = table.with_event(event)
    _write_settlement(
        path,
        opportunity_id=episode_id,
        index=index,
        mode=mode,
        identity=identity,
        status=status,
        cell=None,
        event=event,
        table_before=before,
        table_after=settled.digest(),
        feedback_digest=feedback_digest,
        detail=detail or {"reason": reason},
    )
    return settled


def _write_settlement(
    path: Path | None,
    *,
    opportunity_id: str,
    index: int,
    mode: ErrorCapableMode,
    identity: str,
    status: str,
    cell: dict[str, str] | None,
    event: PriorityEvent,
    table_before: str,
    table_after: str,
    feedback_digest: str | None,
    detail: dict[str, Any],
) -> None:
    """Record one opportunity's settlement, including the opportunities that produced no Episode.

    A refused selection and a materialisation failure are still opportunities: they take up a place
    in
    the budget, so they take up a place in this record with a `null` neighborhood.  Leaving them out
    would make the run's score history look like a contiguous sequence of choices, which it is not.
    """

    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PRIORITY_RULES_VERSION,
        "opportunity_id": opportunity_id,
        "episode_index": index,
        "mode": mode.value,
        "identity": identity,
        "status": status,
        "cell": cell,
        "neighborhood_id": event.neighborhood_id,
        "update_class": event.update_class.value,
        "reason": event.reason,
        "table_digest_before": table_before,
        "table_digest_after": table_after,
        "feedback_digest": feedback_digest,
        "event": event.model_dump(mode="json"),
        "detail": detail,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


__all__ = [
    "ALIGNMENT_VERSION",
    "CAMPAIGN_VERSION",
    "ArmAlignment",
    "CampaignEpisodeRecord",
    "CampaignReport",
    "CampaignSelector",
    "HistorySentinel",
    "PinnedSelector",
    "PRIORITY_RULES_VERSION",
    "PriorityEvent",
    "PriorityTable",
    "ScriptedSelector",
    "SelectorReceipt",
    "UpdateClass",
    "compare_arms",
    "neighborhood_of",
    "neighborhood_registry",
    "oracle_contract_version",
    "run_campaign",
    "tool_catalogue_size",
]
