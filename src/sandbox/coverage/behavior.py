"""Deterministic behavior feature extraction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sandbox.coverage.events import (
    event_data,
    event_sequence,
    event_source,
    event_type,
    iter_tool_windows,
    terminal_kind,
)
from sandbox.coverage.feature_normalizer import value_shape
from sandbox.coverage.models import (
    BehaviorFeature,
    BehaviorFeatureKind,
    BehaviorProfile,
    OfficeBaselineActionEvidence,
    OfficeExecutionEvidence,
    OfficeToolExecutionEvidence,
)
from sandbox.replay.digests import sha256_digest

FeatureAdder = Callable[[BehaviorFeatureKind, str, list[int]], None]


class BehaviorFeatureExtractor:
    def __init__(self, *, max_features: int = 500) -> None:
        self.max_features = max_features

    def extract(
        self,
        *,
        trajectory_id: str,
        execution_id: str,
        events: list[dict[str, Any]],
        office_evidence: OfficeExecutionEvidence | None = None,
    ) -> BehaviorProfile:
        if office_evidence is not None:
            office_evidence.assert_integrity()
        collected: dict[tuple[BehaviorFeatureKind, str], list[int]] = defaultdict(list)
        frequencies: dict[tuple[BehaviorFeatureKind, str], int] = defaultdict(int)

        def add(kind: BehaviorFeatureKind, value: str, sequences: list[int]) -> None:
            if not value:
                return
            key = (kind, value)
            collected[key].extend(sequence for sequence in sequences if sequence >= 0)
            frequencies[key] += 1

        if office_evidence is None:
            tool_calls = [event for event in events if event_type(event) == "tool_call"]
            tool_names = [str(event_data(event).get("name", "")) for event in tool_calls]
            tool_sequences = [event_sequence(event) for event in tool_calls]
        else:
            tool_names = [
                action.tool_name
                for action in (*office_evidence.baseline_actions, *office_evidence.actions)
            ]
            tool_sequences = [-1] * len(office_evidence.baseline_actions) + [
                action.call_sequence for action in office_evidence.actions
            ]

        for name, sequence in zip(tool_names, tool_sequences, strict=True):
            add(BehaviorFeatureKind.TOOL_UNIGRAM, name, [sequence])

        for size, kind in (
            (2, BehaviorFeatureKind.TOOL_BIGRAM),
            (3, BehaviorFeatureKind.TOOL_TRIGRAM),
        ):
            for index in range(len(tool_names) - size + 1):
                add(
                    kind,
                    "→".join(tool_names[index : index + size]),
                    tool_sequences[index : index + size],
                )

        nodes: list[tuple[str, int]] = []
        for event in events:
            if event_type(event) != "node_start":
                continue
            node = event_source(event)
            if not node or (nodes and nodes[-1][0] == node):
                continue
            nodes.append((node, event_sequence(event)))
        for (left, left_sequence), (right, right_sequence) in zip(
            nodes, nodes[1:], strict=False
        ):
            add(BehaviorFeatureKind.NODE_EDGE, f"{left}→{right}", [left_sequence, right_sequence])

        if office_evidence is None:
            self._add_event_window_features(events, add)
            termination = terminal_kind(events)
        else:
            self._add_office_features(office_evidence, add)
            termination = office_evidence.termination

        terminal_sequences = [
            event_sequence(event) for event in events if event_type(event).startswith("execution_")
        ]
        add(
            BehaviorFeatureKind.TERMINATION,
            termination,
            terminal_sequences[-1:] if terminal_sequences else [],
        )

        features = [
            BehaviorFeature(
                kind=kind,
                value=value,
                source_sequences=sorted(set(sequences)),
                frequency=frequencies[(kind, value)],
            )
            for (kind, value), sequences in sorted(
                collected.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        ]
        if len(features) > self.max_features:
            features = features[: self.max_features]
        profile_payload = [
            {"kind": feature.kind.value, "value": feature.value}
            for feature in features
        ]
        return BehaviorProfile(
            trajectory_id=trajectory_id,
            execution_id=execution_id,
            features=features,
            profile_hash=sha256_digest(profile_payload),
            feature_count=len(features),
        )

    @staticmethod
    def _add_event_window_features(
        events: list[dict[str, Any]],
        add: FeatureAdder,
    ) -> None:
        security_states = ["normal"]
        windows = iter_tool_windows(events)
        for window in windows:
            result = window.result or {}
            allowed = result.get("allowed")
            outcome = str(result.get("outcome", ""))
            risk_category = result.get("risk_category")
            if allowed is False or window.security_categories:
                state = "blocked"
            elif allowed is True and outcome in {"success", "succeeded"} and risk_category:
                state = "risky_success"
            else:
                state = "normal"
            security_states.append(state)

            if allowed is False:
                result_kind = "blocked"
            elif outcome in {"success", "succeeded"}:
                result_kind = "succeeded"
            elif outcome == "not_found":
                result_kind = "not_found"
            else:
                result_kind = "error"
            add(
                BehaviorFeatureKind.TOOL_RESULT,
                f"{window.tool_name}:{result_kind}",
                window.evidence_sequences,
            )

            shapes = [
                f"{name}={value_shape(name, value)}"
                for name, value in sorted(window.arguments.items())
            ]
            shape_value = f"{window.tool_name}:" + (",".join(shapes) if shapes else "<NO_ARGS>")
            add(
                BehaviorFeatureKind.PARAM_SHAPE,
                shape_value,
                [window.call_sequence],
            )

        if security_states[-1] != "normal":
            security_states.append("normal")
        for left, right in zip(security_states, security_states[1:], strict=False):
            if left != right:
                add(BehaviorFeatureKind.SECURITY_TRANSITION, f"{left}→{right}", [])

    @classmethod
    def _add_office_features(
        cls,
        evidence: OfficeExecutionEvidence,
        add: FeatureAdder,
    ) -> None:
        previous_authorization = "start"
        previous_sequence: int | None = None
        all_actions = (*evidence.baseline_actions, *evidence.actions)
        for action in all_actions:
            if isinstance(action, OfficeToolExecutionEvidence):
                sequences = [action.call_sequence, action.result_sequence]
                call_sequence = action.call_sequence
                authorization = (
                    "rejected"
                    if not action.allowed
                    else "authorized"
                    if action.authorized is True
                    else "unauthorized"
                )
            else:
                sequences = []
                call_sequence = None
                authorization = "authorized" if action.authorized else "unauthorized"
            result_kind = cls._office_result_kind(action)
            add(
                BehaviorFeatureKind.TOOL_RESULT,
                f"{action.tool_name}:{result_kind}",
                sequences,
            )

            invalid_arguments = (
                isinstance(action, OfficeToolExecutionEvidence)
                and not action.arguments_valid
            )
            shapes = (
                []
                if invalid_arguments
                else [
                    f"{name}={value_shape(name, value)}"
                    for name, value in sorted(action.arguments.items())
                ]
            )
            shape_value = (
                "<INVALID_ARGS>"
                if invalid_arguments
                else ",".join(shapes) if shapes else "<NO_ARGS>"
            )
            add(
                BehaviorFeatureKind.PARAM_SHAPE,
                f"{action.tool_name}:{shape_value}",
                [] if call_sequence is None else [call_sequence],
            )
            sensitivities = (
                []
                if invalid_arguments
                else cls._office_parameter_sensitivities(action, evidence)
            )
            for sensitivity in sensitivities:
                add(
                    BehaviorFeatureKind.PARAM_SENSITIVITY,
                    f"{action.tool_name}:{sensitivity}",
                    [] if call_sequence is None else [call_sequence],
                )

            add(
                BehaviorFeatureKind.AUTHORIZATION,
                f"{action.tool_name}:{authorization}",
                sequences,
            )
            transition_sequences = [] if call_sequence is None else [call_sequence]
            if previous_sequence is not None:
                transition_sequences.insert(0, previous_sequence)
            add(
                BehaviorFeatureKind.AUTHORIZATION_TRANSITION,
                f"{previous_authorization}→{authorization}",
                transition_sequences,
            )
            previous_authorization = authorization
            if call_sequence is not None:
                previous_sequence = call_sequence

            state_change = (
                "changed"
                if action.before_state_digest != action.after_state_digest
                else "unchanged"
            )
            add(
                BehaviorFeatureKind.STATE_CHANGE,
                f"{action.tool_name}:{state_change}",
                sequences,
            )

        cls._add_collection_state_changes(evidence, add)

    @staticmethod
    def _office_result_kind(
        action: OfficeBaselineActionEvidence | OfficeToolExecutionEvidence,
    ) -> str:
        if isinstance(action, OfficeToolExecutionEvidence) and not action.allowed:
            return "blocked"
        if action.outcome in {"success", "succeeded"}:
            return "succeeded"
        if action.outcome == "not_found":
            return "not_found"
        return "error"

    @staticmethod
    def _office_parameter_sensitivities(
        action: OfficeBaselineActionEvidence | OfficeToolExecutionEvidence,
        evidence: OfficeExecutionEvidence,
    ) -> list[str]:
        values: set[str] = set()
        sensitivity = action.arguments.get("sensitivity")
        if sensitivity in {"public", "internal", "restricted"}:
            values.add(f"declared={sensitivity}")
        visibility = action.arguments.get("visibility")
        if visibility in {"private", "internal", "public"}:
            values.add(f"visibility={visibility}")

        file_id = action.arguments.get("file_id")
        if isinstance(file_id, str):
            for state in (
                evidence.scenario_initial_state,
                evidence.episode_initial_state,
                evidence.final_state,
            ):
                files = state.get("drive_files")
                if not isinstance(files, dict):
                    continue
                item = files.get(file_id)
                if not isinstance(item, dict):
                    continue
                resource_sensitivity = item.get("sensitivity")
                if resource_sensitivity in {"public", "internal", "restricted"}:
                    values.add(f"resource={resource_sensitivity}")
                    break

        recipients: list[str] = []
        for name in ("recipient", "to"):
            value = action.arguments.get(name)
            if isinstance(value, str):
                recipients.append(value)
        attendees = action.arguments.get("attendees")
        if isinstance(attendees, list):
            recipients.extend(item for item in attendees if isinstance(item, str))
        principals = evidence.episode_initial_state.get("principals")
        if isinstance(principals, dict):
            for recipient in recipients:
                principal = principals.get(recipient)
                if not isinstance(principal, dict):
                    continue
                principal_class = principal.get("principal_class")
                if principal_class in {"internal", "external"}:
                    values.add(f"recipient={principal_class}")
        return sorted(values)

    @staticmethod
    def _add_collection_state_changes(
        evidence: OfficeExecutionEvidence,
        add: FeatureAdder,
    ) -> None:
        changed = False
        for schema in evidence.test_case.scenario.state_schema:
            before = evidence.scenario_initial_state[schema.collection_id]
            after = evidence.final_state[schema.collection_id]
            before_items = (
                before
                if schema.container == "mapping"
                else {item[schema.item_id_field]: item for item in before}
            )
            after_items = (
                after
                if schema.container == "mapping"
                else {item[schema.item_id_field]: item for item in after}
            )
            for item_id in sorted(set(before_items) | set(after_items)):
                if item_id not in before_items:
                    delta = "added"
                elif item_id not in after_items:
                    delta = "removed"
                elif before_items[item_id] != after_items[item_id]:
                    delta = "updated"
                else:
                    continue
                changed = True
                add(
                    BehaviorFeatureKind.STATE_CHANGE,
                    f"{schema.collection_id}:{delta}",
                    [],
                )
        if not changed:
            add(BehaviorFeatureKind.STATE_CHANGE, "episode:unchanged", [])
