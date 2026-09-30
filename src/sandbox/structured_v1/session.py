"""One episode, orchestrated from the envelope and nothing else (R2/R9/R13; P6).

The session is the only place that runs an episode, and it runs it in one way: verify the
envelope against the container's own assets, re-derive the initial state, then loop over
model decisions and tool calls under the frozen budget, capturing effects and exposures as
they commit, proving closure, and writing a bundle. A refusal at any step propagates: there
is no branch that falls back to the old execution path.

Everything it needs from the outside world arrives through narrow ports (``ModelPort``,
``ToolPort``, ``ClockPort``), so the identical code path can be rehearsed offline with fakes
and, in the container, driven by the real model client and tool runtime. Nothing in this
module imports a container, a model client or the old route.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.assets import FrozenAssetStore
from sandbox.structured_v1.budget import BudgetTracker, CallKind
from sandbox.structured_v1.bundle import EpisodeBundle, StopReason, build_bundle
from sandbox.structured_v1.closure import (
    CLOSURE_WINDOW_SECONDS,
    ClosureObservations,
    SubmittedAction,
    closure_states,
    prove_closure,
    require_submissions_resolved,
)
from sandbox.structured_v1.effects import capture_effect
from sandbox.structured_v1.envelope import StructuredEnvelope, require_runnable, verify_envelope
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import (
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EffectKey,
    RelatedResourceRef,
    RequestState,
)
from sandbox.structured_v1.execution_evidence import ToolExecutionEvidence
from sandbox.structured_v1.exposure import (
    CommittedDeliveryContent,
    ExposureFact,
    ToolReturn,
    carried_content_digest,
    fact_from_tool_return,
    return_content_digest,
)
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.models import Identifier, Sha256Digest, StructuredContract
from sandbox.structured_v1.phases import PhaseReceipt
from sandbox.structured_v1.redaction import registered_file_digest
from sandbox.structured_v1.tool_catalogue import ToolingFacts
from sandbox.structured_v1.transitions import (
    StructuredTransitionRecord,
    initialized_transition_record,
)
from sandbox.structured_v1.world import materialize_world
from sandbox.tool_contracts import ToolSpec

DEFAULT_CHANNELS = (DeliveryChannel.MESSAGE, DeliveryChannel.SHARED_STORAGE)


class ToolCallReport(StructuredContract):
    """What one tool call did, as the runtime observed it (path A capture input).

    The world-side digests are here because the runtime is the only thing that knows them:
    the session turns this into a transition record and never touches the world itself.
    """

    tool_call_id: Identifier
    tool_name: Identifier
    action_request_id: Identifier
    ordinal: int = Field(default=0, ge=0)
    channel: DeliveryChannel
    committed: bool
    blocked: bool = False
    post_submit: bool = False
    world_transition_digest: Sha256Digest
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    created_objects: tuple[str, ...] = ()
    content_digest: Sha256Digest
    proof_digest: Sha256Digest
    returned_content: str = ""
    registered_units: tuple[str, ...] = ()
    registered_files: tuple[str, ...] = ()
    related_refs: tuple[RelatedResourceRef, ...] = ()
    audience: tuple[DeliveryRelation, ...] = ()
    source_object_id: str | None = None
    source_field: str | None = None
    slot_id: Identifier | None = None
    model_visible_result: dict[str, Any] = Field(default_factory=dict)
    delivery_content: CommittedDeliveryContent | None = None
    execution_evidence: ToolExecutionEvidence | None = None

    @property
    def wrote_to_the_world(self) -> bool:
        """A call that reached the world, whether it committed or was blocked."""

        return self.committed or self.blocked

    @property
    def produced_an_effect(self) -> bool:
        """Whether this call is an effect at all.

        A blocked call is one (a behaviour fact), a delivery is one, and a committed write
        into a real channel is one. A private read that created nothing is not: it is
        documented by its transition record and, when it carried material, by its exposure
        fact - not by an effect that claims a delivery nobody could read.
        """

        if self.post_submit or not self.committed:
            return self.blocked
        if self.channel is DeliveryChannel.ACTOR_PRIVATE:
            return bool(self.created_objects)
        return True

    @property
    def request_state(self) -> RequestState:
        if self.committed or self.blocked:
            return RequestState.COMPLETED
        return RequestState.UNRESOLVED


class ModelPort(Protocol):
    """The model, including its trusted-task and tool-result conversation."""

    def bind(self, *, task_text: str, tools: tuple[ToolSpec, ...]) -> None: ...

    def continue_task(
        self, *, task_text: str, tools: tuple[ToolSpec, ...] | None = None
    ) -> None: ...

    def decide(self, *, step: int) -> ModelDecision: ...

    def observe(self, decision: ModelDecision, report: ToolCallReport) -> None: ...


class ToolPort(Protocol):
    """The tool runtime, as the session uses it: execute one chosen action."""

    def execute(self, decision: ModelDecision) -> ToolCallReport: ...


class ClockPort(Protocol):
    """Monotonic seconds; the closure window is measured with it (`SOC-ENV-85`).

    ``None`` means the clock is unavailable, which makes closure unprovable rather than
    silently assumed.
    """

    source: str

    def now(self) -> int | None: ...

    def wait_until(self, target: int) -> None: ...


class HostProbe(Protocol):
    """The host's view of the container, used for the closure proof."""

    source: str

    def saw_container_activity(self) -> bool | None: ...


def run_episode(
    envelope: StructuredEnvelope,
    *,
    assets: FrozenAssetStore,
    tooling: ToolingFacts,
    model: ModelPort,
    tools: ToolPort,
    clock: ClockPort,
    host: HostProbe,
    channels: tuple[DeliveryChannel, ...] = DEFAULT_CHANNELS,
    model_tools: tuple[ToolSpec, ...] = (),
    cancelled: Callable[[], bool] | None = None,
) -> EpisodeBundle:
    """Run exactly one episode and return its bundle (`R2`, `R9`, `R13`)."""

    verified = verify_envelope(envelope, assets=assets, tooling=tooling)
    require_runnable(verified)

    base_world = assets.resolve(envelope.base_locator)
    materialized = materialize_world(
        envelope.manifest,
        envelope.material,
        base_world=base_world,
        overlay=envelope.overlay,
        episode_id=envelope.schedule.episode_id,
        expected_base_world_digest=envelope.base_world_digest,
    )
    initialization = initialized_transition_record(materialized)

    tracker = BudgetTracker(budget=envelope.budget)
    decisions: list[ModelDecision] = []
    records: list[StructuredTransitionRecord] = []
    effects: list[DeliveryEffect] = []
    exposures: list[ExposureFact] = []
    returns: list[ToolReturn] = []
    submissions: list[SubmittedAction] = []
    rejected: list[str] = []
    slot_contents = {
        slot.slot_id: "\n".join(slot.contents) for slot in envelope.material.slots
    }
    slot_target_objects = {target.object_id for target in envelope.overlay.slot_targets}
    frozen_resource_contents = {
        **{
            item.file_id: item.content
            for item in envelope.overlay.drive_files
            if item.file_id not in slot_target_objects
        },
        **{
            item.path: item.content
            for item in envelope.overlay.workspace_files
            if item.path not in slot_target_objects
        },
    }
    fixed_registered_ids = {
        item.file_id for item in envelope.manifest.registered_files
        if item.file_id in envelope.overlay.fixed_registered_reads
        and item.file_id in frozen_resource_contents
    }
    frozen_resource_digests = {
        resource_id: (
            carried_content_digest(frozen_resource_contents[resource_id]),
            sha256_digest(frozen_resource_contents[resource_id]),
            registered_file_digest(frozen_resource_contents[resource_id]),
        )
        for resource_id in fixed_registered_ids
    }

    stop_reason = StopReason.MODEL_STOPPED
    step = 0
    ledger_sequence = 0
    last_action_at = clock.now() or 0
    episode_started = last_action_at
    protocol = envelope.manifest.session_protocol
    phase_budgets = protocol.budgets(envelope.budget) if protocol else ()
    phase_index = 0
    phase_start = 0
    phase_started = episode_started
    phase_tracker = BudgetTracker(budget=phase_budgets[0]) if protocol else tracker
    phases: list[PhaseReceipt] = []

    # `ZIE-11`: the action phase may be given its own tool set.  Naming a tool this run does not
    # offer is a configuration error rather than a silently empty list.
    action_tools: tuple[ToolSpec, ...] | None = None
    if protocol is not None and protocol.action_tools:
        offered = {spec.name for spec in model_tools}
        unknown = sorted(set(protocol.action_tools) - offered)
        if unknown:
            raise ValueError(f"the action phase names tools this run does not offer: {unknown}")
        wanted = set(protocol.action_tools)
        action_tools = tuple(spec for spec in model_tools if spec.name in wanted)

    def require_active() -> None:
        if cancelled is not None and cancelled():
            raise RuntimeError("structured episode was cancelled; no further calls permitted")

    def resource_limit(current: BudgetTracker, started: int) -> bool:
        usage, budget = current.usage, current.budget
        elapsed = max((clock.now() or started) - started, 0)
        return (max(elapsed, usage.wall_clock_seconds) >= budget.max_wall_clock_seconds
                or usage.input_tokens >= budget.max_input_tokens
                or usage.output_tokens >= budget.max_output_tokens
                or usage.expense_units >= budget.max_expense_units)

    def close_phase(reason: str) -> None:
        if protocol:
            phases.append(PhaseReceipt(
                phase_id="investigation" if phase_index == 0 else "action",
                task_text=envelope.task_text if phase_index == 0 else protocol.action_task_text,
                start_decision=phase_start, end_decision=step,
                budget=phase_tracker.budget, usage=phase_tracker.usage,
                elapsed_seconds=max((clock.now() or phase_started) - phase_started, 0),
                end_reason=reason,
            ))

    def start_action(reason: str) -> None:
        nonlocal phase_index, phase_start, phase_started, phase_tracker
        require_active()
        close_phase(reason)
        model.continue_task(task_text=protocol.action_task_text, tools=action_tools)
        phase_index = 1
        phase_start = step
        phase_started = clock.now() or phase_started
        phase_tracker = BudgetTracker(budget=phase_budgets[1])

    phase_reason = "model-stopped"
    model.bind(task_text=envelope.task_text, tools=model_tools)
    while True:
        require_active()
        if protocol and (resource_limit(tracker, episode_started)
                         or resource_limit(phase_tracker, phase_started)):
            stop_reason = StopReason.BUDGET_EXCEEDED
            phase_reason = "budget-exceeded"
            break
        if not tracker.can_call(CallKind.MODEL):
            stop_reason = StopReason.BUDGET_EXCEEDED
            phase_reason = "budget-exceeded"
            break
        if protocol and not phase_tracker.can_call(CallKind.MODEL):
            if phase_index == 0 and tracker.can_call(CallKind.TOOL):
                start_action("phase-call-limit")
                continue
            stop_reason = StopReason.BUDGET_EXCEEDED
            phase_reason = "phase-call-limit"
            break
        decision = model.decide(step=step)
        if protocol:
            decision = decision.model_copy(update={
                "phase_id": "investigation" if phase_index == 0 else "action",
            })
        decisions.append(decision)
        tracker = tracker.record(
            CallKind.MODEL,
            input_tokens=decision.input_tokens,
            output_tokens=decision.output_tokens,
            expense_units=decision.expense_units,
            wall_clock_seconds=decision.wall_clock_seconds,
            usage_reported=decision.usage_reported,
        )
        step += 1
        require_active()
        if protocol:
            phase_tracker = phase_tracker.record(
                CallKind.MODEL, input_tokens=decision.input_tokens,
                output_tokens=decision.output_tokens, expense_units=decision.expense_units,
                wall_clock_seconds=decision.wall_clock_seconds,
                usage_reported=decision.usage_reported,
            )
            if (resource_limit(tracker, episode_started)
                    or resource_limit(phase_tracker, phase_started)):
                stop_reason = StopReason.BUDGET_EXCEEDED
                phase_reason = "budget-exceeded"
                break
        if decision.stopped:
            if (protocol and phase_index == 0 and tracker.can_call(CallKind.MODEL)
                    and tracker.can_call(CallKind.TOOL)):
                start_action("model-stopped")
                continue
            break
        if (not tracker.can_call(CallKind.TOOL)
                or (protocol and not phase_tracker.can_call(CallKind.TOOL))):
            stop_reason = StopReason.BUDGET_EXCEEDED
            phase_reason = "budget-exceeded"
            break

        report = tools.execute(decision)
        model.observe(decision, report)
        tracker = tracker.record(CallKind.TOOL)
        if protocol:
            phase_tracker = phase_tracker.record(CallKind.TOOL)
        returns.append(
            ToolReturn(
                tool_call_id=report.tool_call_id,
                tool_name=report.tool_name,
                content_digest=return_content_digest(report.returned_content),
                delivery_content=report.delivery_content,
                execution_evidence=report.execution_evidence,
            )
        )
        if report.post_submit:
            rejected.append(report.tool_name)
            continue
        last_action_at = clock.now() or last_action_at

        ledger_sequence += 1
        effect: DeliveryEffect | None = None
        if report.produced_an_effect:
            effect = capture_effect(
                key=EffectKey(
                    action_request_id=report.action_request_id, ordinal=report.ordinal
                ),
                sequence=ledger_sequence,
                channel=report.channel,
                committed=report.committed,
                blocked=report.blocked,
                content_digest=report.content_digest,
                proof_digest=report.proof_digest,
                registered_units=report.registered_units,
                registered_files=report.registered_files,
                created_objects=report.created_objects,
                related_refs=report.related_refs,
                audience=report.audience,
            )
            effects.append(effect)

        record = StructuredTransitionRecord(
            sequence=len(records) + 1,
            transaction_id=report.tool_call_id,
            action_request_id=report.action_request_id,
            committed=report.committed,
            world_transition_digest=report.world_transition_digest,
            before_state_digest=report.before_state_digest,
            after_state_digest=report.after_state_digest,
            created_object_ids=tuple(sorted(report.created_objects)),
            effects=(effect,) if effect else (),
        )
        records.append(record)
        submissions.append(
            SubmittedAction(
                action_request_id=report.action_request_id,
                channel=report.channel,
                request_state=report.request_state,
            )
        )

        if report.committed and report.source_object_id:
            slot_content = None
            resource_id = None
            if report.slot_id is not None:
                slot_content = slot_contents.get(report.slot_id)
                if slot_content is None:
                    raise EnvelopeRefusal(
                        FailureCode.EXPOSURE_UNBOUND,
                        f"the tool reported a read of unknown slot {report.slot_id}",
                    )
            elif (
                report.execution_evidence is not None
                and report.execution_evidence.read_origin == "frozen_resource"
                and report.source_object_id in fixed_registered_ids
            ):
                resource_id = report.source_object_id
                slot_content = frozen_resource_contents[resource_id]
            if not slot_content:
                continue
            exposures.append(
                fact_from_tool_return(
                    fact_id=f"fact.{len(exposures) + 1:06d}",
                    sequence=len(exposures) + 1,
                    tool_name=report.tool_name,
                    tool_call_id=report.tool_call_id,
                    returned_content=report.returned_content,
                    source_object_id=report.source_object_id,
                    source_field=report.source_field or "content",
                    slot_id=report.slot_id,
                    resource_id=resource_id,
                    slot_content=slot_content,
                    material_digest=envelope.material.material_digest,
                    principal=envelope.actor.principal_id,
                )
            )

    close_phase(phase_reason)
    recorded_action_request_ids = frozenset(
        record.action_request_id
        for record in records
        if record.action_request_id is not None
    )
    require_submissions_resolved(tuple(submissions), recorded_action_request_ids)

    clock.wait_until(last_action_at + CLOSURE_WINDOW_SECONDS)
    closed_at = clock.now()
    quiet = 0 if closed_at is None else max(closed_at - last_action_at, 0)
    host_activity = host.saw_container_activity()
    closure_record = prove_closure(
        ClosureObservations(
            quiet_elapsed_seconds=quiet,
            monotonic_available=closed_at is not None,
            reaped=True,
            ledger_sequence_at_last_action=ledger_sequence,
            ledger_sequence_at_close=ledger_sequence,
            rejected_calls=tuple(rejected),
            host_saw_container_activity=bool(host_activity),
            host_observation_available=host_activity is not None,
            clock_source=clock.source,
            reaper_source="synchronous-tool-runtime",
            host_source=host.source,
        )
    )
    closure = closure_states(closure_record, channels)
    outcome_codes = tuple(
        code for code in (tracker.code(),) if isinstance(code, FailureCode)
    )
    if (protocol and stop_reason is StopReason.BUDGET_EXCEEDED
            and FailureCode.BUDGET_EXCEEDED not in outcome_codes):
        outcome_codes = (*outcome_codes, FailureCode.BUDGET_EXCEEDED)
    return build_bundle(
        episode_id=envelope.schedule.episode_id,
        fixture_id=envelope.manifest.fixture_id,
        envelope_digest=verified.envelope_digest,
        base_world_digest=envelope.base_world_digest,
        overlay_digest=envelope.overlay.canonical_digest(),
        material=envelope.material,
        model_decisions=tuple(decisions),
        phases=tuple(phases),
        initial_state_digest=materialized.initial_state_digest,
        initialization_transition_digest=materialized.initialization_transition_digest,
        final_state_digest=(
            records[-1].after_state_digest
            if records
            else materialized.initial_state_digest
        ),
        initialization_transition=initialization,
        records=tuple(records),
        exposures=tuple(exposures),
        frozen_resource_digests=tuple(sorted(
            (resource_id, *frozen_resource_digests[resource_id])
            for resource_id in {fact.material.resource_id for fact in exposures}
            if resource_id is not None
        )),
        tool_returns=tuple(returns),
        closure_record=closure_record,
        closure=closure,
        complete=closure_record.proven,
        missing=closure_record.unproven_reasons,
        tooling=verified_tooling(envelope, tooling),
        usage=tracker.usage,
        stop_reason=stop_reason,
        rejected_calls=tuple(rejected),
        outcome_codes=outcome_codes,
    )


def verified_tooling(envelope: StructuredEnvelope, facts: ToolingFacts):
    """The tooling verification, recomputed here so the bundle carries the proof itself."""

    from sandbox.structured_v1.tool_catalogue import verify_tooling

    return verify_tooling(envelope.tooling, facts)
