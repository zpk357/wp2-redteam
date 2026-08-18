"""Load verified uploaded artifacts and execute strict or live replay."""

from __future__ import annotations

import json
from pathlib import Path

from app.adapter.factory import AdapterFactory
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.agent.fake_react_provider import FakeReactProvider
from app.agent.office_control_provider import OfficeControlProvider
from app.office_v2_session import (
    OfficeV2LiveOracleArtifact,
    OfficeV2RecordingState,
    load_office_v2_session,
)
from app.replay.checkpoint import RecordingSession
from app.replay.react_decision_recorder import ReactDecisionRecorder, RecordedReactProvider
from app.replay.state_codec import OfficeV2StateCodec, StateCodec
from app.replay.tool_recorder import ToolReplayer
from app.tools.base import ToolRegistry
from sandbox.protocol import (
    ExecutionBackend,
    ExecutionRequest,
    ModelOptions,
    ModelProvider,
    V2ExecutionEnvelope,
    V2ScenarioCaseKind,
)
from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.replay.exceptions import ArtifactIntegrityError, ReplayDivergenceError
from sandbox.replay.manifest import verify_manifest
from sandbox.replay.models import (
    ArtifactRef,
    CheckpointStateEnvelope,
    ForkSuffixMode,
    RecordedModelDecision,
    RecordedToolInteraction,
    ReplayCheckpointsRequest,
    ReplayForkRequest,
    ReplayManifest,
    ReplayMode,
    ReplayRequest,
    StateCheckpoint,
)
from sandbox.scenarios.office_fork import (
    OfficeCarrierForkError,
    replace_office_carrier_payload,
)
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.fork import (
    infer_office_v2_compatibility_purpose,
    rematerialize_office_v2_scenario_text,
)
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective


class ReplayAdapter:
    def __init__(
        self,
        input_dir: Path = Path("/workspace/replay-in"),
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.input_dir = input_dir
        self.adapter_factory = adapter_factory or AdapterFactory()
        self.last_checkpoint_digests = []
        self.last_final_state_digest: str | None = None
        self._v2_initial_recording_state: OfficeV2RecordingState | None = None
        self._v2_expected_recording_state: OfficeV2RecordingState | None = None
        self._v2_expected_oracle: OfficeV2LiveOracleArtifact | None = None

    def load(self, replay_request: ReplayRequest):
        self._v2_initial_recording_state = None
        self._v2_expected_recording_state = None
        self._v2_expected_oracle = None
        manifest_path = self._safe_input_path(replay_request.manifest_relative_path)
        manifest = ReplayManifest.model_validate_json(manifest_path.read_bytes())
        verify_manifest(manifest)
        prompt_payload = self._read_json(manifest.prompt)
        determinism = self._read_json(manifest.determinism_config)
        initial_envelope = CheckpointStateEnvelope.model_validate(
            self._read_json(manifest.initial_state)
        )
        decisions = self._read_jsonl(manifest.model_decisions, RecordedModelDecision)
        interactions = self._read_jsonl(manifest.tool_records, RecordedToolInteraction)
        prompt = prompt_payload.get("prompt")
        if not isinstance(prompt, str):
            raise ArtifactIntegrityError("recorded prompt artifact is invalid")
        execution_backend = self._require_trace_backend(determinism)
        state_codec_version = determinism.get("state_codec_version", "2.0")
        if (
            manifest.state_codec_version != state_codec_version
            or initial_envelope.state_codec_version != state_codec_version
        ):
            raise ArtifactIntegrityError("recorded state codec identities differ")
        tools = ToolRegistry()
        office_v2_execution = None
        if state_codec_version == "office-v2-state-codec-v1":
            office_v2_execution = self._load_office_v2_replay_artifacts(
                manifest,
                determinism,
                initial_envelope,
            )
            initial = OfficeV2StateCodec(lambda: None).restore(
                initial_envelope,
                tools,
                execution_id=replay_request.execution_id,
            )
        else:
            initial = StateCodec().restore(
                initial_envelope,
                tools,
                execution_id=replay_request.execution_id,
            )
        initial["prompt"] = prompt
        try:
            request = ExecutionRequest(
                execution_id=replay_request.execution_id,
                case_id=manifest.case_id,
                prompt=prompt,
                max_steps=int(determinism["max_steps"]),
                timeout_seconds=int(determinism["timeout_seconds"]),
                seed=manifest.seed,
                scenario_id=manifest.scenario_id,
                metadata=determinism.get("metadata", {}),
                agent_version=manifest.agent_version,
                image_digest=manifest.image_digest,
                execution_backend=execution_backend,
                model=(
                    ModelOptions.model_validate(determinism["model"])
                    if determinism.get("model") is not None
                    else None
                ),
                office_v2_execution=office_v2_execution,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("recorded execution request is invalid") from exc
        if replay_request.mode != ReplayMode.STRICT:
            raise ReplayDivergenceError(
                -32112,
                "trace_react_v2 currently supports strict replay only",
            )
        model = RecordedReactProvider(
            decisions,
            start_index=initial_envelope.next_model_decision_index,
        )
        return (
            request,
            model,
            ToolReplayer(
                tools,
                interactions,
                start_index=initial_envelope.next_tool_interaction_index,
            ),
            initial,
            str(determinism.get("start_node", "agent")),
        )

    async def execute(self, replay_request: ReplayRequest):
        loaded = self.load(replay_request)
        async for event in self.execute_loaded(*loaded):
            yield event

    async def execute_loaded(self, request, model, tools, initial, start_node):
        if request.office_v2_execution is not None:
            if (
                self._v2_initial_recording_state is None
                or self._v2_expected_recording_state is None
                or self._v2_expected_oracle is None
            ):
                raise ReplayDivergenceError(
                    -32108,
                    "Office V2 replay artifacts were not loaded",
                )
            from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

            adapter = LangGraphReactRuntime()
            events = adapter.execute_strict_replay(
                request,
                initial=initial,
                initial_recording_state=self._v2_initial_recording_state,
                expected_recording_state=self._v2_expected_recording_state,
                expected_oracle=self._v2_expected_oracle,
                provider=model,
                tool_replayer=tools,
            )
            async for event in events:
                yield event
            self.last_checkpoint_digests = adapter.last_checkpoint_digests
            self.last_final_state_digest = adapter.last_final_state_digest
            return
        adapter = TraceReactAdapter(provider=model, registry=tools)
        events = adapter.execute_replay_from(request, initial=initial)
        async for event in events:
            yield event
        self.last_checkpoint_digests = adapter.last_checkpoint_digests
        self.last_final_state_digest = adapter.last_final_state_digest

    def _load_office_v2_replay_artifacts(
        self,
        manifest: ReplayManifest,
        determinism: dict,
        initial_envelope: CheckpointStateEnvelope,
    ) -> V2ExecutionEnvelope:
        if (
            initial_envelope.scenario_state_codec != "office-v2-state-codec-v1"
            or initial_envelope.scenario_state is None
            or manifest.office_v2_recording_state is None
            or manifest.office_v2_oracle is None
        ):
            raise ArtifactIntegrityError("Office V2 replay artifacts are incomplete")
        try:
            execution = V2ExecutionEnvelope.model_validate(
                determinism.get("office_v2_execution")
            )
            initial_state = OfficeV2RecordingState.model_validate(
                initial_envelope.scenario_state
            )
            final_state = OfficeV2RecordingState.model_validate(
                self._read_json(manifest.office_v2_recording_state)
            )
            oracle = OfficeV2LiveOracleArtifact.model_validate(
                self._read_json(manifest.office_v2_oracle)
            )
        except ValueError as exc:
            raise ArtifactIntegrityError("Office V2 replay artifact is invalid") from exc
        if (
            initial_envelope.next_model_decision_index != 0
            or initial_envelope.next_tool_interaction_index != 0
            or initial_state.tool_invocations
            or initial_state.tool_results
            or initial_state.interaction_events
            or initial_state.session.history
        ):
            raise ArtifactIntegrityError("Office V2 initial replay state is not pristine")
        if (
            initial_state.session.execution_envelope_digest
            != execution.canonical_digest()
            or final_state.session.execution_envelope_digest
            != execution.canonical_digest()
            or initial_state.session.episode_id != final_state.session.episode_id
            or initial_state.session.state_digest != execution.initial_state_digest
            or final_state.session.state_digest
            != oracle.evidence_bundle.identity.final_state_digest
            or final_state.session.state_digest != oracle.oracle_result.final_state_digest
            or initial_state.session.state_digest
            != oracle.evidence_bundle.identity.initial_state_digest
        ):
            raise ArtifactIntegrityError("Office V2 replay identities or states diverge")
        self._v2_initial_recording_state = initial_state
        self._v2_expected_recording_state = final_state
        self._v2_expected_oracle = oracle
        return execution

    def load_fork(self, fork_request: ReplayForkRequest):
        manifest_path = self._safe_input_path(fork_request.manifest_relative_path)
        manifest = ReplayManifest.model_validate_json(manifest_path.read_bytes())
        verify_manifest(manifest)
        determinism = self._read_json(manifest.determinism_config)
        checkpoints = self._read_jsonl(manifest.checkpoints, StateCheckpoint)
        checkpoint = next(
            (
                item
                for item in checkpoints
                if item.checkpoint_id == fork_request.checkpoint_id
            ),
            None,
        )
        if checkpoint is None or not checkpoint.recoverable or checkpoint.state_artifact is None:
            raise ReplayDivergenceError(-32105, "checkpoint is missing or not recoverable")
        if fork_request.injection.type not in checkpoint.allowed_injection_types:
            raise ReplayDivergenceError(-32112, "injection type is not allowed at checkpoint")
        envelope = CheckpointStateEnvelope.model_validate(
            self._read_json(checkpoint.state_artifact)
        )
        base_tools = ToolRegistry()
        v2_execution = None
        v2_recording_state = None
        if envelope.scenario_state_codec == "office-v2-state-codec-v1":
            try:
                v2_execution = V2ExecutionEnvelope.model_validate(
                    determinism.get("office_v2_execution")
                )
                v2_recording_state = OfficeV2RecordingState.model_validate(
                    envelope.scenario_state
                )
            except ValueError as exc:
                raise ArtifactIntegrityError(
                    "Office V2 fork checkpoint is invalid"
                ) from exc
            if (
                v2_recording_state.tool_invocations
                or v2_recording_state.tool_results
                or v2_recording_state.interaction_events
                or v2_recording_state.session.history
            ):
                raise ReplayDivergenceError(
                    -32112,
                    "Office V2 verification fork currently requires a pristine checkpoint",
                )
            if (
                v2_recording_state.session.execution_envelope_digest
                != v2_execution.canonical_digest()
                or v2_recording_state.session.state_digest
                != v2_execution.initial_state_digest
            ):
                raise ArtifactIntegrityError(
                    "Office V2 fork checkpoint identity diverges"
                )
            if fork_request.injection.type == "carrier_payload_replace":
                if not isinstance(fork_request.injection.content, str):
                    raise ReplayDivergenceError(
                        -32112,
                        "carrier payload replacement must be a string",
                    )
                try:
                    v2_execution, v2_recording_state = self._replace_v2_carrier_payload(
                        v2_execution,
                        v2_recording_state,
                        fork_request.injection.content,
                    )
                except ValueError as exc:
                    raise ReplayDivergenceError(-32112, str(exc)) from exc
                envelope = envelope.model_copy(
                    update={
                        "scenario_state": v2_recording_state.model_dump(
                            mode="json", exclude_none=False
                        )
                    }
                )
            initial = {
                **envelope.agent_state,
                "execution_id": fork_request.execution_id,
            }
        else:
            if fork_request.injection.type == "carrier_payload_replace":
                if not isinstance(fork_request.injection.content, str):
                    raise ReplayDivergenceError(
                        -32112,
                        "carrier payload replacement must be a string",
                    )
                try:
                    envelope = replace_office_carrier_payload(
                        envelope,
                        fork_request.injection.content,
                    ).checkpoint_state
                except OfficeCarrierForkError as exc:
                    raise ReplayDivergenceError(-32112, str(exc)) from exc
            initial = StateCodec().restore(
                envelope,
                base_tools,
                execution_id=fork_request.execution_id,
            )
        self._require_trace_backend(determinism)
        return self._load_trace_fork(
            manifest,
            determinism,
            checkpoint,
            initial,
            base_tools,
            fork_request,
            v2_execution=v2_execution,
            v2_recording_state=v2_recording_state,
        )

    @staticmethod
    def _replace_v2_carrier_payload(
        execution: V2ExecutionEnvelope,
        recording_state: OfficeV2RecordingState,
        content: str,
    ) -> tuple[V2ExecutionEnvelope, OfficeV2RecordingState]:
        if execution.scenario_case_kind is not V2ScenarioCaseKind.ATTACK:
            raise ValueError("Office V2 carrier replacement requires an attack case")
        source_case = MaterializedScenarioCase.model_validate(
            execution.scenario_case_payload
        )
        canonical_world = load_canonical_world()
        purpose = infer_office_v2_compatibility_purpose(source_case, canonical_world)
        materialization = rematerialize_office_v2_scenario_text(
            source_case=source_case,
            canonical_world=canonical_world,
            generated_content=content,
            purpose=purpose,
            seed=source_case.seed,
        )
        if materialization.scenario_case.task.instruction != source_case.task.instruction:
            raise ValueError("Office V2 fork cannot change the frozen task")
        directives = tuple(
            ScriptedResponseDirective.model_validate(item.model_dump(mode="json"))
            for item in execution.interaction_response_directives
        )
        child_execution = build_v2_execution_envelope(
            materialization.scenario_case,
            initial_state=materialization.initial_state,
            initialization_transition=materialization.initialization_transition,
            model_identity=execution.model_identity,
            response_directives=directives,
        )
        child_session = load_office_v2_session(
            child_execution,
            episode_id=recording_state.session.episode_id,
        )
        return child_execution, child_session.export_recording_state()

    async def execute_fork(self, fork_request: ReplayForkRequest):
        (
            request,
            model,
            tools,
            initial,
            recording,
            start_node,
            audit_events,
            v2_recording_state,
        ) = self.load_fork(fork_request)
        if request.office_v2_execution is not None:
            if v2_recording_state is None:
                raise ReplayDivergenceError(
                    -32108,
                    "Office V2 fork lost its scenario checkpoint state",
                )
            adapter = self.adapter_factory.create(request)
            from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

            if not isinstance(adapter, LangGraphReactRuntime):
                raise ReplayDivergenceError(
                    -32112,
                    "Office V2 fork requires the LangGraph Agent runtime",
                )
            events = adapter.execute_v2_fork(
                request,
                initial=initial,
                initial_recording_state=v2_recording_state,
                audit_events=audit_events,
            )
            async for event in events:
                yield event
            self.last_checkpoint_digests = adapter.last_checkpoint_digests
            self.last_final_state_digest = adapter.last_final_state_digest
            return
        if request.model is not None and request.model.provider == ModelProvider.OLLAMA:
            adapter = self.adapter_factory.create(request)
            from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

            if not isinstance(adapter, LangGraphReactRuntime):
                raise ReplayDivergenceError(
                    -32112,
                    "formal live fork cannot fall back from the LangGraph Agent runtime",
                )
            execute_fork = getattr(adapter, "execute_fork", None)
            if execute_fork is None:
                raise ReplayDivergenceError(
                    -32112,
                    "formal live fork requires the LangGraph Agent runtime",
                )
            events = execute_fork(
                request,
                initial=initial,
                registry=tools,
                audit_events=audit_events,
            )
            async for event in events:
                yield event
            self.last_checkpoint_digests = adapter.last_checkpoint_digests
            self.last_final_state_digest = adapter.last_final_state_digest
            return
        adapter = TraceReactAdapter(provider=model, registry=tools)
        events = adapter.execute_fork(
            request,
            initial=initial,
            recording=recording,
        )
        async for event in events:
            yield event

    def _load_trace_fork(
        self,
        manifest,
        determinism,
        checkpoint,
        initial,
        base_tools,
        fork_request,
        *,
        v2_execution=None,
        v2_recording_state=None,
    ):
        if fork_request.suffix_mode != ForkSuffixMode.LIVE_AND_RECORD:
            raise ReplayDivergenceError(
                -32112,
                "trace_react_v2 forks require live_and_record",
            )
        if fork_request.injection.type != "carrier_payload_replace":
            self._apply_trace_prompt_injection(initial, fork_request)
        configured_model = (
            ModelOptions.model_validate(determinism["model"])
            if determinism.get("model") is not None
            else None
        )
        if configured_model is not None and configured_model.provider not in {
            ModelProvider.FAKE,
            ModelProvider.OLLAMA,
        }:
            raise ReplayDivergenceError(
                -32112,
                "trace_react_v2 live fork model provider is unsupported",
            )
        prompt = initial.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ReplayDivergenceError(-32112, "fork state contains no valid prompt")
        office = base_tools.office
        if v2_execution is not None:
            v2_case = MaterializedScenarioCase.model_validate(
                v2_execution.scenario_case_payload
            )
            if prompt != v2_case.task.instruction:
                raise ReplayDivergenceError(
                    -32112,
                    "Office V2 fork cannot change the top-level task",
                )
            request = ExecutionRequest(
                execution_id=fork_request.execution_id,
                case_id=v2_execution.scenario_case_id,
                prompt=prompt,
                max_steps=int(initial.get("max_steps", determinism["max_steps"])),
                timeout_seconds=int(determinism["timeout_seconds"]),
                seed=v2_case.seed,
                scenario_id=v2_execution.scenario_id,
                metadata={
                    **determinism.get("metadata", {}),
                    "verification_only": True,
                    "parent_replay_id": manifest.replay_id,
                },
                agent_version=manifest.agent_version,
                image_digest=manifest.image_digest,
                execution_backend=ExecutionBackend.TRACE_REACT_V2,
                model=configured_model,
                office_v2_execution=v2_execution,
            )
            audit_events = [
                {
                    "event_type": "fork_started",
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "suffix_mode": fork_request.suffix_mode.value,
                },
                {
                    "event_type": "fork_injection_applied",
                    "injection_type": fork_request.injection.type,
                    "content_digest": sha256_digest(fork_request.injection.content),
                },
            ]
            return (
                request,
                None,
                base_tools,
                initial,
                None,
                "agent",
                audit_events,
                v2_recording_state,
            )
        if office is not None:
            case = office.initialization.test_case
            control = determinism.get("metadata", {}).get("scenario_control")
            formal_model = (
                configured_model is not None
                and configured_model.provider == ModelProvider.OLLAMA
            )
            if not formal_model and control not in {"safe", "vulnerable"}:
                raise ReplayDivergenceError(
                    -32112,
                    "office fork requires safe or vulnerable scenario_control",
                )
            if prompt != case.benign_task.instruction:
                raise ReplayDivergenceError(
                    -32112,
                    "office fork cannot change the top-level benign task",
                )
            case_id = case.case_id
            scenario_id = case.scenario.template_id
            seed = case.seed
            scenario_initialization = office.initialization.model_dump(mode="json")
            base_model = None if formal_model else OfficeControlProvider(str(control), case)
        else:
            case_id = f"{manifest.case_id}-fork"
            scenario_id = manifest.scenario_id
            seed = manifest.seed
            scenario_initialization = None
            base_model = FakeReactProvider()
        request = ExecutionRequest(
            execution_id=fork_request.execution_id,
            case_id=case_id,
            prompt=prompt,
            max_steps=int(initial.get("max_steps", determinism["max_steps"])),
            timeout_seconds=int(determinism["timeout_seconds"]),
            seed=seed,
            scenario_id=scenario_id,
            metadata=determinism.get("metadata", {}),
            agent_version=manifest.agent_version,
            image_digest=manifest.image_digest,
            execution_backend=ExecutionBackend.TRACE_REACT_V2,
            model=configured_model,
            scenario_initialization=scenario_initialization,
        )
        audit_events = [
            {
                "event_type": "fork_started",
                "checkpoint_id": checkpoint.checkpoint_id,
                "suffix_mode": fork_request.suffix_mode.value,
            },
            {
                "event_type": "fork_injection_applied",
                "injection_type": fork_request.injection.type,
                "content_digest": sha256_digest(fork_request.injection.content),
            },
        ]
        if configured_model is not None and configured_model.provider == ModelProvider.OLLAMA:
            return request, None, base_tools, initial, None, "agent", audit_events, None
        recording = RecordingSession(
            request,
            base_model,
            base_tools,
            start_node="agent",
            model_recorder_factory=ReactDecisionRecorder,
            runtime_id="trace-react-v2",
        )
        recording.audit_events.extend(audit_events)
        return (
            request,
            recording.model,
            recording.tools,
            initial,
            recording,
            "agent",
            audit_events,
            None,
        )

    @staticmethod
    def _apply_trace_prompt_injection(initial: dict, fork_request: ReplayForkRequest) -> None:
        injection = fork_request.injection
        if injection.type not in {"prompt_replace", "prompt_append"} or not isinstance(
            injection.content, str
        ):
            raise ReplayDivergenceError(
                -32112,
                "trace_react_v2 fork requires a string prompt injection",
            )
        messages = initial.get("messages")
        if not isinstance(messages, list):
            raise ReplayDivergenceError(-32112, "fork state contains no React messages")
        user_index = next(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, dict) and message.get("role") == "user"
            ),
            None,
        )
        if user_index is None or not isinstance(messages[user_index].get("content"), str):
            raise ReplayDivergenceError(-32112, "fork state contains no user prompt")
        previous = str(initial.get("prompt", ""))
        if injection.type == "prompt_replace":
            prompt = injection.content
        else:
            prompt = previous + injection.content
        initial["prompt"] = prompt
        messages[user_index] = {**messages[user_index], "content": prompt}
        initial["final_answer"] = None
        initial["submitted"] = False

    @staticmethod
    def _require_trace_backend(determinism: dict) -> ExecutionBackend:
        backend = determinism.get("execution_backend")
        if backend != ExecutionBackend.TRACE_REACT_V2.value:
            raise ArtifactIntegrityError(
                "recording was not created by the supported trace_react_v2 backend"
            )
        return ExecutionBackend.TRACE_REACT_V2

    def checkpoints(self, request: ReplayCheckpointsRequest):
        manifest_path = self._safe_input_path(request.manifest_relative_path)
        manifest = ReplayManifest.model_validate_json(manifest_path.read_bytes())
        verify_manifest(manifest)
        return self._read_jsonl(manifest.checkpoints, StateCheckpoint)

    def _read_json(self, reference: ArtifactRef):
        try:
            return json.loads(self._read_artifact(reference))
        except (ValueError, UnicodeError) as exc:
            raise ArtifactIntegrityError("uploaded JSON artifact is invalid") from exc

    def _read_jsonl(self, reference: ArtifactRef, model_type):
        records = []
        for line in self._read_artifact(reference).splitlines():
            if line.strip():
                records.append(model_type.model_validate_json(line))
        index_name = None
        if model_type is RecordedModelDecision:
            index_name = "decision_index"
        elif model_type is RecordedToolInteraction:
            index_name = "interaction_index"
        if index_name is not None and [getattr(record, index_name) for record in records] != list(
            range(len(records))
        ):
            raise ArtifactIntegrityError("recorded indexes are not contiguous")
        return records

    def _read_artifact(self, reference: ArtifactRef) -> bytes:
        path = self.input_dir / "artifacts" / Path(*reference.relative_path.split("/"))
        resolved = path.resolve()
        artifact_root = (self.input_dir / "artifacts").resolve()
        if artifact_root not in resolved.parents or not resolved.is_file():
            raise ArtifactIntegrityError("uploaded replay artifact is missing")
        payload = resolved.read_bytes()
        if len(payload) != reference.size_bytes or sha256_bytes(payload) != reference.sha256:
            raise ArtifactIntegrityError("uploaded replay artifact digest mismatch")
        return payload

    def _safe_input_path(self, relative_path: str) -> Path:
        if not relative_path or "\\" in relative_path:
            raise ArtifactIntegrityError("manifest path is invalid")
        path = (self.input_dir / Path(*relative_path.split("/"))).resolve()
        root = self.input_dir.resolve()
        if root not in path.parents or not path.is_file():
            raise ArtifactIntegrityError("uploaded replay manifest is missing")
        return path


# Backward-compatible name used by the first strict-replay tests.
StrictReplayAdapter = ReplayAdapter
