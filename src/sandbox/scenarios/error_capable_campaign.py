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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
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
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_ORACLE_CONTRACT_VERSION,
    ErrorCapableFixture,
)
from sandbox.scenarios.error_capable_selector import (
    LLMSelector,
    PureRandomSelector,
    SelectionRejected,
    SelectorAttempt,
    validate_choice,
)
from sandbox.scenarios.error_capable_world import planned_file_ids
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import OFFICE_V2_TOOL_NAMES

CAMPAIGN_VERSION = "error-capable-campaign-v3"
ALIGNMENT_VERSION = "error-capable-arm-alignment-v1"


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

    Guided reads the feedback and acts on the first behaviour gap it names, which it repeats in its
    rationale -- a selector whose rationale does not move when the feedback moves would be feedback
    used for reporting rather than for steering. Campaign random mode bypasses this selector.
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
            remaining = snapshot.menu_gap_details if snapshot is not None else ()
            if remaining:
                cell = remaining[0]
                family, variant, path = cell["task_family"], cell["task_variant"], cell["path_id"]
                attack, carrier, layout = (
                    cell["attack_mode"],
                    cell["attack_carrier"],
                    cell["layout_id"],
                )
                rationale = f"selected unobserved menu combination {cell} from guided feedback"
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
            if frozen["status"] == "selection_pending":
                frozen["status"] = "rejected"
                frozen["rejection"] = (
                    "selection interrupted; opportunity consumed without replacement"
                )
                write_artifact(artifact_path, frozen)
            if frozen["status"] == "rejected":
                rejected.append(frozen)
                if frozen.get("attempt"):
                    selection_attempts.append(SelectorAttempt.model_validate(frozen["attempt"]))
                sentinel_reads.extend(frozen.get("history_reads", ()))
                continue
        # The sentinel is rebuilt per Episode because what must be audited is this Episode's read.
        sentinel = HistorySentinel(ledger=state, targets=targets, allow=guided)
        feedback = sentinel.feedback(purpose=f"request-{index}") if guided else None
        request = SelectorRequest(
            mode=mode,
            seed=seed + index,
            agent_model_name=model_identity.normalized_model_id,
            selector_model_name=model_identity.normalized_model_id if guided else None,
            available_task_families=tuple(TaskFamily(item) for item in targets.families),
            available_task_variants=tuple(
                v.variant_id for family in TaskFamily for v in task_family_spec(family).variants
            ),
            available_paths=tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids),
            available_attacks=tuple(AttackMode(item) for item in targets.attacks),
            available_carriers=tuple(
                dict.fromkeys(
                    c for mode_item in AttackMode for c in attack_spec(mode_item).carriers
                )
            ),
            available_layouts=("balanced-8", "distributed-10", "nested-12"),
            feedback=feedback,
        )
        if frozen is not None:
            if frozen["request_digest"] != sha256_digest(request.model_dump(mode="json")):
                raise ValueError("restored selection feedback or menu differs from frozen request")
            decision = SelectorDecision.model_validate(frozen["decision"])
            raw_response = frozen["raw_response"]
            if frozen.get("attempt"):
                selection_attempts.append(SelectorAttempt.model_validate(frozen["attempt"]))
            sentinel.reads = list(frozen["history_reads"])
        else:
            pending = {
                "identity": identity_digest,
                "status": "selection_pending",
                "index": index,
                "request": request.model_dump(mode="json"),
                "request_digest": sha256_digest(request.model_dump(mode="json")),
                "history_reads": sentinel.reads.copy(),
            }
            if artifact_path is not None:
                write_artifact(artifact_path, pending)
            try:
                selected = selector(request, sentinel, episode_index=index)
                if inspect.isawaitable(selected):
                    selected = await selected
                decision, raw_response = selected
                validate_choice(request, decision)
            except (SelectionRejected, ValueError) as exc:
                attempt = getattr(selector, "last_attempt", None)
                failure = {
                    **pending,
                    "status": "rejected",
                    "rejection": str(exc),
                    "attempt": None if attempt is None else attempt.model_dump(mode="json"),
                    "history_reads": sentinel.reads.copy(),
                }
                if attempt is not None:
                    selection_attempts.append(attempt)
                if artifact_path is not None:
                    write_artifact(artifact_path, failure)
                sentinel_reads.extend(sentinel.reads)
                rejected.append(failure)
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

        store = None if journal_root is None else _store_for(journal_root, episode_id)
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


__all__ = [
    "ALIGNMENT_VERSION",
    "CAMPAIGN_VERSION",
    "ArmAlignment",
    "CampaignEpisodeRecord",
    "CampaignReport",
    "CampaignSelector",
    "HistorySentinel",
    "PinnedSelector",
    "ScriptedSelector",
    "SelectorReceipt",
    "compare_arms",
    "oracle_contract_version",
    "run_campaign",
    "tool_catalogue_size",
]
