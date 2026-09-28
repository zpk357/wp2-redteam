"""Formal Runtime adapter for one structured-scenario episode."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from app.adapter.base import AdapterConfigurationError, AgentAdapter
from app.agent.fake_react_provider import FakeReactProvider
from app.agent.react_contract import ReactModelProvider, ReactToolCall, ReactTurn
from app.protocol import ExecutionRequest, ModelProvider, TraceEvent
from app.structured_v1_model_port import ReactProviderModelPort
from app.structured_v1_runtime_surface import (
    FREEZE_MANIFEST_PATH,
    StructuredV1Run,
    assemble_tool_port,
    build_structured_v1_run,
)
from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.bundle import load_bundle, write_bundle
from sandbox.structured_v1.drivers import MonotonicClock, UnavailableHost
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal
from sandbox.structured_v1.session import ClockPort, HostProbe, run_episode
from sandbox.tool_contracts import OFFICE_V2_TOOL_SPECS


class StructuredV1Adapter(AgentAdapter):
    """Execute the new request kind without falling back to an older scenario route."""

    def __init__(
        self,
        *,
        provider: ReactModelProvider | None = None,
        freeze_manifest_path: Path = FREEZE_MANIFEST_PATH,
        bundle_directory: Path = Path("/tmp/structured-v1"),
        clock: ClockPort | None = None,
        host: HostProbe | None = None,
    ) -> None:
        self._provider = provider
        self._freeze_manifest_path = freeze_manifest_path
        self._bundle_directory = bundle_directory
        self._clock = clock
        self._host = host
        self.last_checkpoint_digests = ()
        self.last_final_state_digest = None

    async def execute(self, request: ExecutionRequest) -> AsyncIterator[TraceEvent]:
        cancellation = threading.Event()
        try:
            run = build_structured_v1_run(
                request.structured_case_execution,
                freeze_manifest_path=self._freeze_manifest_path,
            )
            provider = self._provider or self._build_provider(request, run)
            options = request.model
            model = ReactProviderModelPort(
                provider,
                model_name=options.model_name if options is not None else "fake",
                seed=request.seed,
            )
            bundle = await asyncio.to_thread(
                run_episode,
                run.request.envelope,
                assets=run.assets,
                tooling=run.tooling,
                model=model,
                tools=assemble_tool_port(run),
                clock=self._clock or MonotonicClock(),
                host=self._host or UnavailableHost(),
                model_tools=tuple(OFFICE_V2_TOOL_SPECS),
                cancelled=cancellation.is_set,
            )
            self._bundle_directory.mkdir(parents=True, exist_ok=True)
            path = write_bundle(
                bundle, self._bundle_directory / f"{request.execution_id}.bundle.json"
            )
            load_bundle(path)
        except EnvelopeRefusal as refusal:
            raise AdapterConfigurationError(refusal.code.value, refusal.detail) from refusal
        except BaseException:
            cancellation.set()
            raise

        self.last_final_state_digest = bundle.final_state_digest
        yield TraceEvent(
            execution_id=request.execution_id,
            sequence=0,
            event_type="execution_finished",
            source="structured_v1",
            data={
                "final_answer": None,
                "bundle_path": str(path),
                "bundle_digest": bundle.bundle_digest,
                "bundle_complete": bundle.complete,
                "bundle": bundle.model_dump(mode="json", exclude_none=False),
            },
        )

    @staticmethod
    def _build_provider(
        request: ExecutionRequest, run: StructuredV1Run
    ) -> ReactModelProvider:
        options = request.model
        if options is None or options.provider is ModelProvider.FAKE:
            if options is not None and options.model_name == "structured-v1-scripted":
                drive_targets = tuple(
                    (target.object_id, target.slot_id)
                    for target in run.request.envelope.overlay.slot_targets
                    if target.field_kind.value == "drive-file-content"
                )
                if not drive_targets:
                    raise AdapterConfigurationError(
                        "structured_configuration_error",
                        "the scripted acceptance provider requires a drive-file slot",
                    )
                drive_file_id, slot_id = drive_targets[0]
                slot = next(
                    item
                    for item in run.request.envelope.material.slots
                    if item.slot_id == slot_id
                )
                return _StructuredScriptedProvider(
                    drive_file_id,
                    expected_content_digest=sha256_digest("\n".join(slot.contents)),
                )
            return FakeReactProvider()
        if options.provider is ModelProvider.OLLAMA:
            from app.agent.ollama_react_provider import OllamaReactProvider

            return OllamaReactProvider(options)
        raise AdapterConfigurationError(
            "structured_model_provider_unsupported",
            f"unsupported structured model provider: {options.provider}",
        )


class _StructuredScriptedProvider:
    """No-model acceptance substitute using the same provider/session path."""

    version = "structured-v1-scripted-provider-v1"
    last_token_usage = None

    def __init__(self, drive_file_id: str, *, expected_content_digest: str) -> None:
        self._drive_file_id = drive_file_id
        self._expected_content_digest = expected_content_digest

    async def generate(self, messages, tools, *, seed):
        del tools, seed
        if not any(message.role == "tool" for message in messages):
            return ReactTurn(
                assistant_text="Reading the frozen material through the real tool.",
                tool_calls=[
                    ReactToolCall(
                        name="read_drive_file",
                        arguments={"file_id": self._drive_file_id},
                    )
                ],
            )
        latest = next(message for message in reversed(messages) if message.role == "tool")
        content = latest.content
        returned = (
            content.get("data", {}).get("content") if isinstance(content, dict) else None
        )
        if not isinstance(returned, str):
            raise RuntimeError("the scripted acceptance provider received no readable content")
        observed_digest = sha256_digest(returned)
        if observed_digest != self._expected_content_digest:
            raise RuntimeError("the model-visible tool content differs from frozen material")
        return ReactTurn(
            assistant_text=f"Observed model-visible material {observed_digest}.",
            tool_calls=[ReactToolCall(name="submit", arguments={"answer": "done"})],
        )
