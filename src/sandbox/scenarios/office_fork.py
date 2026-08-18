"""Validated carrier-payload replacement for recoverable office checkpoints."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import CheckpointStateEnvelope
from sandbox.scenarios.models import AttackBinding, TestCase, resolve_state_value
from sandbox.scenarios.office_episode import (
    OfficeEpisodeInitialization,
    OfficeToolRuntimeState,
    build_office_episode_initialization,
)
from sandbox.scenarios.office_runtime import OfficeActionRecord, OfficeRuntime, OfficeRuntimeError


class OfficeCarrierForkError(ValueError):
    """The selected checkpoint cannot safely accept a carrier payload replacement."""


@dataclass(frozen=True)
class OfficeCarrierForkResult:
    checkpoint_state: CheckpointStateEnvelope
    initialization: OfficeEpisodeInitialization
    parent_payload_digest: str
    replacement_payload_digest: str


def replace_office_carrier_payload(
    checkpoint_state: CheckpointStateEnvelope,
    replacement_payload: str,
) -> OfficeCarrierForkResult:
    """Replace only the attack payload while preserving the checkpoint prefix contract."""
    if not isinstance(replacement_payload, str) or not replacement_payload:
        raise OfficeCarrierForkError("carrier payload replacement must be a non-empty string")

    enterprise_state = deepcopy(checkpoint_state.enterprise_tool_state)
    office_payload = enterprise_state.get("office_episode")
    if not isinstance(office_payload, dict):
        raise OfficeCarrierForkError("checkpoint does not contain an office episode")
    try:
        saved = OfficeToolRuntimeState.model_validate(office_payload)
    except ValueError as exc:
        raise OfficeCarrierForkError("checkpoint office state is invalid") from exc

    parent_case = saved.initialization.test_case
    if parent_case.attack is None:
        raise OfficeCarrierForkError("office checkpoint has no attack payload to replace")
    parent_case.assert_integrity()
    if replacement_payload == parent_case.attack.payload:
        raise OfficeCarrierForkError("carrier payload replacement must change the payload")
    parent_runtime = _restore_prefix(parent_case, saved)
    _require_carrier_unexposed(checkpoint_state, parent_case, parent_runtime.records)

    child_case = _replace_case_payload(parent_case, replacement_payload)
    child_initialization = build_office_episode_initialization(child_case)
    child_runtime = OfficeRuntime(child_case)
    child_records = _replay_actions(child_runtime, saved)
    _require_prefix_results_unchanged(parent_runtime.records, child_records)

    child_tool_state = OfficeToolRuntimeState(
        initialization=child_initialization,
        actions=saved.actions,
        records_digest=_records_digest(child_records),
        final_state_digest=child_runtime.state_digest(),
    )
    enterprise_state["office_episode"] = child_tool_state.model_dump(mode="json")
    envelope_payload = checkpoint_state.model_dump(mode="json")
    envelope_payload["enterprise_tool_state"] = enterprise_state
    child_checkpoint = CheckpointStateEnvelope.model_validate(envelope_payload)
    return OfficeCarrierForkResult(
        checkpoint_state=child_checkpoint,
        initialization=child_initialization,
        parent_payload_digest=sha256_digest(parent_case.attack.payload),
        replacement_payload_digest=sha256_digest(replacement_payload),
    )


def _restore_prefix(case: TestCase, saved: OfficeToolRuntimeState) -> OfficeRuntime:
    runtime = OfficeRuntime(case)
    records = _replay_actions(runtime, saved)
    if runtime.state_digest() != saved.final_state_digest:
        raise OfficeCarrierForkError("checkpoint office state digest does not match its actions")
    if _records_digest(records) != saved.records_digest:
        raise OfficeCarrierForkError("checkpoint office record digest does not match its actions")
    return runtime


def _replay_actions(
    runtime: OfficeRuntime,
    saved: OfficeToolRuntimeState,
) -> list[OfficeActionRecord]:
    records: list[OfficeActionRecord] = []
    for action in saved.actions:
        try:
            records.append(runtime.execute(action.capability_id, action.arguments))
        except OfficeRuntimeError as exc:
            raise OfficeCarrierForkError("checkpoint contains an invalid office action") from exc
    return records


def _require_carrier_unexposed(
    checkpoint_state: CheckpointStateEnvelope,
    case: TestCase,
    records: list[OfficeActionRecord],
) -> None:
    attack = case.attack
    if attack is None:
        raise OfficeCarrierForkError("office checkpoint has no attack payload to replace")
    exists, materialized_value = resolve_state_value(
        case.scenario,
        OfficeRuntime(case).initial_state,
        attack.carrier.target,
    )
    if not exists or not isinstance(materialized_value, str):
        raise OfficeCarrierForkError("office carrier target is not materialized text")
    needles = (attack.payload, materialized_value)
    if _contains_any(checkpoint_state.agent_state.get("messages"), needles):
        raise OfficeCarrierForkError("carrier payload was already exposed to the Agent")
    if any(_contains_any(record.output, needles) for record in records):
        raise OfficeCarrierForkError("carrier payload was already exposed by a tool result")


def _replace_case_payload(parent: TestCase, payload: str) -> TestCase:
    attack = parent.attack
    if attack is None:
        raise OfficeCarrierForkError("office checkpoint has no attack payload to replace")
    try:
        child_attack = AttackBinding.model_validate(
            {**attack.model_dump(mode="json"), "payload": payload}
        )
        suffix = f"-fork-{sha256_digest(payload)[7:19]}"
        case_id = f"{parent.case_id[: 128 - len(suffix)]}{suffix}"
        child_case = TestCase.model_validate(
            {
                **parent.model_dump(mode="json", exclude={"content_digest"}),
                "case_id": case_id,
                "attack": child_attack.model_dump(mode="json"),
                "parent_case_id": parent.case_id,
            }
        )
    except ValueError as exc:
        raise OfficeCarrierForkError("replacement payload violates the frozen TestCase") from exc
    if (
        child_case.scenario != parent.scenario
        or child_case.benign_task != parent.benign_task
        or child_case.attack is None
        or child_case.attack.objective != attack.objective
        or child_case.attack.carrier != attack.carrier
        or child_case.agent != parent.agent
        or child_case.budget != parent.budget
        or child_case.seed != parent.seed
    ):
        raise OfficeCarrierForkError("carrier payload replacement changed frozen case identity")
    return child_case


def _require_prefix_results_unchanged(
    parent: list[OfficeActionRecord],
    child: list[OfficeActionRecord],
) -> None:
    if len(parent) != len(child):
        raise OfficeCarrierForkError("carrier replacement changed the prefix action count")
    fields = ("capability_id", "arguments", "authorized", "outcome", "output", "error")
    for parent_record, child_record in zip(parent, child, strict=True):
        if any(
            getattr(parent_record, field) != getattr(child_record, field)
            for field in fields
        ):
            raise OfficeCarrierForkError("carrier replacement changed a prefix tool result")


def _contains_any(value: Any, needles: tuple[str, ...]) -> bool:
    if isinstance(value, str):
        return any(needle in value for needle in needles)
    if isinstance(value, dict):
        return any(_contains_any(item, needles) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_any(item, needles) for item in value)
    return False


def _records_digest(records: list[OfficeActionRecord]) -> str:
    return sha256_digest([record.model_dump(mode="json") for record in records])
