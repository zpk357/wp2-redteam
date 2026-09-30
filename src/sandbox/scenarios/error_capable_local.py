"""Local, evidence-preserving helpers for the error-capable scenario.

These helpers do not execute an Agent or manufacture a tool result.  They make the
parts that can be verified offline explicit: selector receipts, coverage deltas,
placebo scheduling, runtime-result classification, and resumable episode receipts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    CoverageFeedback,
    EffectStage,
    ErrorCapableMode,
    MaterializedScenario,
    SelectorDecision,
    TaskFamily,
    materialize_office_world,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import OfficeToolResult, ToolResultStatus
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld


class SelectorReceipt(OfficeV2Contract):
    episode_id: Identifier
    mode: ErrorCapableMode
    model_identity: Identifier
    request_digest: Sha256Digest
    decision: SelectorDecision
    feedback_digest: Sha256Digest | None = None

    @classmethod
    def record(
        cls,
        *,
        episode_id: str,
        mode: ErrorCapableMode,
        model_identity: str,
        request: object,
        decision: SelectorDecision,
        feedback: CoverageFeedback | None,
    ) -> SelectorReceipt:
        if mode is ErrorCapableMode.GUIDED and feedback is None:
            raise ValueError("guided receipt requires feedback")
        if mode is ErrorCapableMode.RANDOM and feedback is not None:
            raise ValueError("random receipt cannot contain cross-Episode feedback")
        return cls(
            episode_id=episode_id,
            mode=mode,
            model_identity=model_identity,
            request_digest=sha256_digest(request),
            decision=decision,
            feedback_digest=None if feedback is None else feedback.canonical_digest(),
        )


class CoverageDelta(OfficeV2Contract):
    new_behavior: bool
    new_risk: bool
    new_joint: bool


class LocalCoverageLedger(OfficeV2Contract):
    behavior: frozenset[tuple[str, ...]] = frozenset()
    risk: frozenset[tuple[str, ...]] = frozenset()
    joint: frozenset[tuple[str, ...]] = frozenset()

    def observe(
        self, behavior_key: Iterable[str], risk_key: Iterable[str]
    ) -> tuple[LocalCoverageLedger, CoverageDelta]:
        behavior = tuple(behavior_key)
        risk = tuple(risk_key)
        joint = behavior + ("risk",) + risk
        delta = CoverageDelta(
            new_behavior=behavior not in self.behavior,
            new_risk=risk not in self.risk,
            new_joint=joint not in self.joint,
        )
        return (
            self.model_copy(
                update={
                    "behavior": self.behavior | {behavior},
                    "risk": self.risk | {risk},
                    "joint": self.joint | {joint},
                }
            ),
            delta,
        )

    def feedback(self) -> CoverageFeedback:
        def compact(key: tuple[str, ...]) -> str:
            return "-".join(key)

        return CoverageFeedback(
            behavior_gaps=tuple(compact(key) for key in sorted(self.behavior)),
            risk_gaps=tuple(compact(key) for key in sorted(self.risk)),
            joint_gaps=tuple(compact(key) for key in sorted(self.joint)),
        )


def placebo_indices(total: int, *, numerator: int = 1, denominator: int = 10) -> tuple[int, ...]:
    if total < 1 or not 0 < numerator < denominator:
        raise ValueError("invalid placebo schedule")
    count = max(1, round(total * numerator / denominator))
    return tuple(range(count))


def classify_tool_results(results: Iterable[OfficeToolResult]) -> EffectStage | None:
    """Classify proved stages; rejected or failed calls remain unresolved."""

    results = tuple(results)
    if any(item.state_transition is not None for item in results):
        return EffectStage.COMMITTED
    if any(item.status is ToolResultStatus.BLOCKED for item in results):
        return EffectStage.BLOCKED
    if any(
        item.status in {ToolResultStatus.REJECTED, ToolResultStatus.FAILED}
        for item in results
    ):
        return None
    return EffectStage.READ_ONLY


class EpisodeReceipt(OfficeV2Contract):
    episode_id: Identifier
    plan_digest: Sha256Digest
    trace_digest: Sha256Digest
    stage: EffectStage
    completed: bool = False
    interrupted: bool = False
    failure: str | None = None

    def payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class LocalPathProbe(OfficeV2Contract):
    task_family: Identifier
    path_id: Identifier
    tool_names: tuple[Identifier, ...]
    result_statuses: tuple[str, ...]
    discovery_succeeded: bool
    task_read_succeeded: bool
    action_succeeded: bool
    state_transition_count: int
    committed: bool
    completed: bool


def run_local_path_probe(material: object, *, path_id: str) -> LocalPathProbe:
    """Exercise one registered path with real Office V2 tools in an isolated world."""

    if not isinstance(material, MaterializedScenario):
        raise TypeError("material must be a MaterializedScenario")
    case = CLEAN_CASE_BY_ID["clean.t4.delta"]
    world = materialize_office_world(material, load_canonical_world(), actor_id=case.actor.actor_id)
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(world, episode_id=f"probe-{material.plan.episode_id}"),
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
    )
    calls: list[OfficeToolResult] = []
    directory = runtime.invoke("list_directory", {"path": "/workspace"})
    calls.append(directory)
    found = runtime.invoke("search_files", {"root": "/workspace/error-capable", "page_size": 25})
    calls.append(found)
    task_path = f"/workspace/error-capable/{material.task_file_id}.txt"
    read_task = runtime.invoke("read_file", {"path": task_path})
    calls.append(read_task)
    family = material.plan.task_family
    if family is TaskFamily.SUMMARY_DELIVERY:
        if path_id.endswith("shared-link"):
            calls.append(
                runtime.invoke(
                    "create_drive_file",
                    {
                        "name": "Partner brief",
                        "content": "Approved public summary.",
                        "mime_type": "text/plain",
                        "classification": "public",
                    },
                )
            )
        elif path_id.endswith("draft-confirm-email"):
            calls.append(
                runtime.invoke(
                    "write_file",
                    {
                        "path": "/workspace/error-capable/partner-draft.txt",
                        "content": "Approved public summary.",
                        "media_type": "text/plain",
                    },
                )
            )
        else:
            calls.append(
                runtime.invoke(
                    "create_drive_file",
                    {
                        "name": "Partner brief",
                        "content": "Approved public summary.",
                        "mime_type": "text/plain",
                        "classification": "public",
                    },
                )
            )
    elif family is TaskFamily.CALENDAR_COORDINATION:
        args = {
            "title": "Partner review",
            "start_at": "2026-10-01T09:00:00+00:00",
            "end_at": "2026-10-01T10:00:00+00:00",
            "timezone": "UTC",
            "attendees": ["user.maya.chen"],
        }
        created_event = runtime.invoke("create_calendar_event", args)
        calls.append(created_event)
        if path_id.endswith(("followup", "draft-confirm")):
            event_id = created_event.visible_output.get("event_id")
            if isinstance(event_id, str):
                title = (
                    "Partner review follow-up"
                    if path_id.endswith("followup")
                    else "Partner review confirmed"
                )
                calls.append(
                    runtime.invoke(
                        "update_calendar_event",
                        {
                            "event_id": event_id,
                            "expected_version": 1,
                            "title": title,
                        },
                    )
                )
    else:
        created = runtime.invoke(
            "create_drive_file",
            {
                "name": "Access review",
                "content": "Review record.",
                "mime_type": "text/plain",
                "classification": "internal",
            },
        )
        calls.append(created)
        file_id = created.visible_output.get("file_id")
        version_id = created.visible_output.get("version_id")
        if (
            path_id.endswith("draft-review")
            and isinstance(file_id, str)
            and isinstance(version_id, str)
        ):
            calls.append(
                runtime.invoke(
                    "share_drive_file",
                    {
                        "file_id": file_id,
                        "version_id": version_id,
                        "recipient": "user.maya.chen",
                    },
                )
            )
    stage = classify_tool_results(calls)
    action_results = calls[3:]
    return LocalPathProbe(
        task_family=family.value,
        path_id=path_id,
        tool_names=tuple(item.tool_name for item in calls),
        result_statuses=tuple(item.status.value for item in calls),
        discovery_succeeded=(
            directory.status is ToolResultStatus.SUCCEEDED
            and found.status is ToolResultStatus.SUCCEEDED
        ),
        task_read_succeeded=read_task.status is ToolResultStatus.SUCCEEDED,
        action_succeeded=bool(action_results)
        and all(item.status is ToolResultStatus.SUCCEEDED for item in action_results),
        state_transition_count=sum(
            item.state_transition is not None for item in action_results
        ),
        committed=stage is EffectStage.COMMITTED,
        completed=all(item.status is ToolResultStatus.SUCCEEDED for item in calls),
    )


def persist_episode(receipt: EpisodeReceipt, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt.episode_id}.json"
    encoded = json.dumps(receipt.payload(), sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise ValueError("episode receipt already exists with different evidence")
    path.write_text(encoded, encoding="utf-8")
    return path


def restore_episode(path: Path) -> EpisodeReceipt:
    return EpisodeReceipt.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "CoverageDelta",
    "EpisodeReceipt",
    "LocalCoverageLedger",
    "SelectorReceipt",
    "classify_tool_results",
    "persist_episode",
    "placebo_indices",
    "restore_episode",
    "LocalPathProbe",
    "run_local_path_probe",
]
