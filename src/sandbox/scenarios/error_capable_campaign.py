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

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    TASK_FAMILY_SPECS,
    AttackMode,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import office_tool_specs, run_agent_episode
from sandbox.scenarios.error_capable_bridge import bridge_trace
from sandbox.scenarios.error_capable_coverage import (
    COVERAGE_STAGES,
    CoverageLedger,
    MenuTargets,
    ObservedKey,
    compact,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_ORACLE_CONTRACT_VERSION,
    ErrorCapableFixture,
)
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import OFFICE_V2_TOOL_NAMES

CAMPAIGN_VERSION = "error-capable-campaign-v1"
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
    used for reporting rather than for steering.  Random never asks the sentinel anything.
    """

    name = "error-capable-scripted-selector-v1"

    def __init__(
        self,
        *,
        path_ids: Mapping[str, Sequence[str]],
        attacks: Sequence[str],
    ) -> None:
        self._paths = {key: tuple(value) for key, value in path_ids.items()}
        self._attacks = tuple(attacks)

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
            gaps = set(snapshot.menu_gaps) if snapshot is not None else set()
            chosen = None
            acted_on = "no menu gap remained"
            # The candidate cells are rebuilt from the menu, rather than parsed back out of the gap
            # identifiers.  A cell is a folded identifier, so taking it apart again would make the
            # choice depend on how the fold joined it -- and the first version of this selector did
            # exactly that, and silently never acted on a gap at all.
            for family in families:
                for path in self._paths[family]:
                    for attack in attacks:
                        cell = compact(family, path, attack, "attack")
                        if cell in gaps:
                            chosen = (family, path, attack)
                            acted_on = cell
                            break
                    if chosen is not None:
                        break
                if chosen is not None:
                    break
            if chosen is None:
                family = families[episode_index % len(families)]
                chosen = (family, self._paths[family][0], attacks[0])
            family, path, attack = chosen
            rationale = f"menu gap {acted_on}"
        else:
            family = families[request.seed % len(families)]
            attack = self._attacks[(request.seed + episode_index) % len(self._attacks)]
            path = self._paths[family][episode_index % len(self._paths[family])]
            rationale = "independent selection, no coverage feedback"

        decision = SelectorDecision(
            task_family=TaskFamily(family),
            path_id=path,
            attack_mode=AttackMode(attack),
            episode_kind=EpisodeKind.ATTACK,
            rationale=rationale,
        )
        raw = decision.model_dump_json()
        return decision, raw


class SelectorReceipt(OfficeV2Contract):
    """One selector call, kept whole so the arms can be compared field by field."""

    version: str = CAMPAIGN_VERSION
    episode_index: int = Field(ge=0)
    mode: ErrorCapableMode
    selector_name: str
    request: SelectorRequest
    request_digest: Sha256Digest
    #: The request with what the experiment manipulates removed -- the mode and the feedback -- over
    #: the fields that must be identical across the arms: the same menu, the same model identity and
    #: the same seed.  Digesting the whole request instead would compare the two arms on the variable
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
                    "selector_model_name": request.selector_model_name,
                    "available_task_families": [
                        item.value for item in request.available_task_families
                    ],
                    "available_attacks": [item.value for item in request.available_attacks],
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

    def feedback_used(self) -> tuple[str, ...]:
        return tuple(
            item.selector.history_reads for item in self.episodes if item.selector.history_reads
        )


def _episode_id(mode: ErrorCapableMode, index: int) -> str:
    arm = "guided" if mode is ErrorCapableMode.GUIDED else "random"
    return f"campaign.{arm}.{index:03d}"


def _file_ids(episode_id: str) -> tuple[str, ...]:
    return (
        f"{episode_id}-task",
        f"{episode_id}-public",
        f"{episode_id}-restricted",
        f"{episode_id}-history",
    )


async def run_campaign(
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
    """Run `episodes` Episodes, feeding each arm's coverage observations into its own next choice."""

    if episodes < 1:
        raise ValueError("a campaign needs at least one Episode")
    manifest = fixture.manifest
    targets = MenuTargets.from_manifest(
        manifest, path_ids={spec.task_family.value: spec.path_ids for spec in TASK_FAMILY_SPECS}
    )
    specs = office_tool_specs()
    menu_digest = sha256_digest({"tools": [spec.name for spec in specs]})
    state = ledger or CoverageLedger()
    records: list[CampaignEpisodeRecord] = []
    sentinel_reads: list[str] = []
    guided = mode is ErrorCapableMode.GUIDED

    for index in range(episodes):
        # The sentinel is rebuilt per Episode because what must be audited is this Episode's read.
        sentinel = HistorySentinel(ledger=state, targets=targets, allow=guided)
        feedback = (
            sentinel.feedback(purpose=f"request-{index}") if guided else None
        )
        request = SelectorRequest(
            mode=mode,
            seed=seed + index,
            agent_model_name=model_identity.normalized_model_id,
            selector_model_name=model_identity.normalized_model_id,
            available_task_families=tuple(TaskFamily(item) for item in targets.families),
            available_attacks=tuple(AttackMode(item) for item in targets.attacks),
            feedback=feedback,
        )
        decision, raw_response = selector(request, sentinel, episode_index=index)
        sentinel_reads.extend(sentinel.reads)

        episode_id = _episode_id(mode, index)
        family = next(item for item in manifest.task_families if item == decision.task_family.value)
        attack = next(item for item in manifest.attack_modes if item == decision.attack_mode.value)
        if not manifest.covers(family, decision.path_id, attack):
            # Fail closed: a decision outside the frozen space is refused, not materialised anyway.
            raise ValueError(f"selector chose a combination the fixture does not cover: {decision}")
        plan = build_plan(
            request,
            decision,
            episode_id=episode_id,
            task_file_id=f"{episode_id}-task",
            file_ids=_file_ids(episode_id),
            attack_carrier=_carrier_for(attack),
            model_name=model_identity.normalized_model_id,
        )
        material = materialize_scenario(plan)

        store = (
            None
            if journal_root is None
            else _store_for(journal_root, episode_id)
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
        state, settled = state.settle(
            observed,
            cell=compact(family, decision.path_id, attack, decision.episode_kind.value),
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
    )


class ArmAlignment(OfficeV2Contract):
    """The two arms compared field by field, so an unfair comparison is visible rather than argued."""

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
    """Check that the only difference between the arms is the feedback."""

    pairs = list(zip(guided.episodes, random.episodes, strict=False))
    agent_inputs = all(
        left.first_input_digest == right.first_input_digest for left, right in pairs
    )
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
        agent_inputs_identical=agent_inputs,
        blind_requests_identical=blind,
        guided_received_feedback=all(
            item.selector.request.feedback is not None for item in guided.episodes
        ),
        random_received_feedback=any(
            item.selector.request.feedback is not None for item in random.episodes
        ),
        random_read_history=bool(random.sentinel_reads),
        detail={
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


def _carrier_for(attack: str) -> str:
    from sandbox.scenarios.error_capable import attack_spec

    return attack_spec(AttackMode(attack)).carriers[0]


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
    "ScriptedSelector",
    "SelectorReceipt",
    "compare_arms",
    "oracle_contract_version",
    "run_campaign",
    "tool_catalogue_size",
]
