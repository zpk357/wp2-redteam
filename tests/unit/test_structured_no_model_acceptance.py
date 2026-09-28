"""P6 no-model container acceptance: the material actually enters the Agent's readable content.

Two things are proven here, both without a model, a container or a server:

1. the build-time freeze constructor round-trips: ``build_freeze_manifest`` freezes a world
   and the fixture mapping, and ``load_assets`` rebuilds the store from it, refusing a world
   that no longer hashes to its own digest;
2. the material reaches the Agent's real readable content: ``drive_structured_v1_episode``
   runs the real ``OfficeV2ToolRuntime`` (not a fake) with a ``ScriptedModelPort`` that
   reads the fixture's slot file, and the session binds the returned content to an exposure
   fact - so the material's presence is a fact about what the tool really returned, not a
   label match.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.adapter.factory import AdapterFactory
from app.adapter.structured_v1_adapter import StructuredV1Adapter
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ExecutionRequest, ModelOptions
from app.structured_v1_model_port import ReactProviderModelPort

from sandbox.structured_v1.assets import FixtureBaseMapping, FrozenWorldAsset
from sandbox.structured_v1.bundle import StopReason, load_bundle, verify_bundle
from sandbox.structured_v1.container import StructuredCaseRequest
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.exposure import carried_content_digest, return_content_digest
from sandbox.structured_v1.freeze import (
    FreezeManifest,
    build_freeze_manifest,
    load_assets,
    write_freeze_manifest,
)
from sandbox.structured_v1.obligations import ObligationId, ObligationOutcome
from sandbox.structured_v1.oracle_io import judge_artifacts
from sandbox.structured_v1.session import ModelDecision
from sandbox.structured_v1.tool_catalogue import ImageManifest
from sandbox.tool_contracts import ToolSpec

DIGEST_C = "sha256:" + "c" * 64


class _ScriptedClock:
    """A clock that returns a fixed sequence, so closure can be proven deterministically."""

    source = "test-substitute"

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def now(self) -> int | None:
        return self._values.pop(0) if self._values else None

    def wait_until(self, target: int) -> None:
        del target


class _RecordingReactProvider:
    version = "recording-react-provider-v1"

    def __init__(self) -> None:
        self.inputs: list[tuple[ReactMessage, ...]] = []
        self.last_token_usage: dict[str, int] | None = None

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del tools, seed
        self.inputs.append(messages)
        self.last_token_usage = {"prompt_tokens": 20, "completion_tokens": 5}
        if len(self.inputs) == 1:
            return ReactTurn(
                assistant_text="I will read the overview.",
                tool_calls=[
                    ReactToolCall(
                        name="read_drive_file", arguments={"file_id": "f-overview"}
                    )
                ],
            )
        return ReactTurn(
            assistant_text="The material is visible in the tool result.",
            tool_calls=[ReactToolCall(name="submit", arguments={"answer": "done"})],
        )


def test_build_freeze_manifest_round_trips(manifest, root_case, tmp_path) -> None:
    import test_structured_episode_rehearsal as rehearsal

    base = rehearsal._base_world()
    catalogue = rehearsal._catalogue()
    image_manifest = ImageManifest(tool_catalogue=catalogue)
    asset = FrozenWorldAsset(world=base)
    mapping = FixtureBaseMapping(
        fixture_id=manifest.fixture_id,
        fixture_version=manifest.fixture_version,
        manifest_digest=manifest.manifest_digest,
        base_locator=asset.locator,
        base_world_digest=base.world_digest,
        overlay_digest="sha256:" + "d" * 64,
    )
    manifest_file = build_freeze_manifest(
        image_manifest=image_manifest,
        worlds=(base,),
        mapping=(mapping,),
    )
    path = write_freeze_manifest(manifest_file, tmp_path / "freeze-manifest.json")

    loaded = FreezeManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assets, tooling = load_assets(loaded)

    assert assets.resolve(asset.locator).world_digest == base.world_digest
    assert assets.mapping_for(manifest.fixture_id) == mapping
    assert tooling.catalogue.catalogue_digest == catalogue.catalogue_digest


def test_the_material_is_in_the_agents_readable_content(manifest, root_case) -> None:
    import test_structured_episode_rehearsal as rehearsal

    envelope, store, slot_content, _ = rehearsal._prepared(manifest, root_case)
    model = ScriptedModelPort(
        [
            ModelDecision(
                call_id="model.001",
                action_request_id="action-0001",
                tool_name="read_drive_file",
                arguments={"file_id": "f-overview"},
                input_tokens=10,
                output_tokens=5,
            ),
            ModelDecision(call_id="model.002", input_tokens=3, output_tokens=2),
        ]
    )
    # clock values: start, the moment after the read, then the moment closure is claimed.
    clock = _ScriptedClock([0, 1, 60])

    bundle = drive_structured_v1_episode(
        envelope,
        assets=store,
        tooling=rehearsal._tooling_facts(),
        model=model,
        clock=clock,
    )

    verify_bundle(bundle, known_sources=frozenset({("f-overview", "current_version.content")}))

    assert len(bundle.exposures) == 1
    fact = bundle.exposures[0]
    # bound to the real return, the real source, and the real material - not a label.
    assert fact.tool_call_id == "call.001"
    assert fact.source_object_id == "f-overview"
    assert fact.source_field == "current_version.content"
    assert fact.material.slot_id == "s1"
    assert fact.material.carried_content_digest == carried_content_digest(slot_content)
    assert fact.return_digest == return_content_digest(slot_content)
    # the returned content really carried the material (it is the file's own content).
    assert bundle.tool_returns[0].content_digest == return_content_digest(slot_content)
    # a read is not a delivery, the episode is clean and closure is proven.
    assert bundle.artifacts().deliveries() == ()
    assert bundle.complete is True
    assert bundle.stop_reason is StopReason.MODEL_STOPPED


def test_normal_control_reads_real_material_and_delivers_only_public_content(
    manifest,
) -> None:
    import test_structured_episode_rehearsal as rehearsal

    control = prepare_inputs(manifest, count=1).normal_control
    envelope, store, slot_content, _ = rehearsal._prepared(manifest, control.case)
    model = ScriptedModelPort(
        [
            ModelDecision(
                call_id="model.001",
                action_request_id="action-0001",
                tool_name="read_drive_file",
                arguments={"file_id": "f-overview"},
            ),
            ModelDecision(
                call_id="model.002",
                action_request_id="action-0002",
                tool_name="send_email",
                arguments={
                    "to": ["partner.contact"],
                    "cc": [],
                    "subject": "Public summary",
                    "body": "Public summary only.",
                },
            ),
            ModelDecision(call_id="model.003"),
        ]
    )
    bundle = drive_structured_v1_episode(
        envelope,
        assets=store,
        tooling=rehearsal._tooling_facts(),
        model=model,
        clock=_ScriptedClock([0, 1, 2, 60]),
    )
    judgment = judge_artifacts(bundle.artifacts(), manifest=manifest)

    assert slot_content in model.observations[0].returned_content
    assert bundle.exposures[0].material.material_digest == control.rendered.material_digest
    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.NO_OBSERVED_VIOLATION
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.NO_OBSERVED_VIOLATION


def test_real_tool_result_enters_the_next_provider_context(manifest, root_case) -> None:
    import test_structured_episode_rehearsal as rehearsal

    envelope, store, slot_content, _ = rehearsal._prepared(manifest, root_case)
    provider = _RecordingReactProvider()
    model = ReactProviderModelPort(provider, model_name="recording", seed=7)

    bundle = drive_structured_v1_episode(
        envelope,
        assets=store,
        tooling=rehearsal._tooling_facts(),
        model=model,
        clock=_ScriptedClock([0, 1, 60]),
    )

    assert len(provider.inputs) == 2
    first_context = provider.inputs[0]
    assert slot_content not in json.dumps(
        [message.model_dump(mode="json") for message in first_context],
        ensure_ascii=False,
    )
    tool_message = provider.inputs[1][-1]
    assert tool_message.role == "tool"
    assert tool_message.name == "read_drive_file"
    assert tool_message.content["status"] == "succeeded"
    assert tool_message.content["data"]["content"] == slot_content
    assert [decision.tool_name for decision in bundle.model_decisions] == [
        "read_drive_file",
        None,
    ]
    assert bundle.usage.model_calls == 2
    assert bundle.usage.input_tokens == 40
    assert bundle.usage.output_tokens == 10


async def test_formal_runtime_adapter_exports_a_reverified_bundle(
    manifest, root_case, tmp_path
) -> None:
    import test_structured_episode_rehearsal as rehearsal

    envelope, store, _, _ = rehearsal._prepared(manifest, root_case)
    image_manifest = ImageManifest(tool_catalogue=rehearsal._catalogue())
    freeze = build_freeze_manifest(
        image_manifest=image_manifest,
        worlds=(store.resolve(envelope.base_locator),),
        mapping=(store.mapping_for(manifest.fixture_id),),
    )
    freeze_path = write_freeze_manifest(freeze, tmp_path / "freeze-manifest.json")
    structured_request = StructuredCaseRequest(
        request_id="request-0001",
        envelope=envelope,
    )
    request = ExecutionRequest(
        execution_id="episode-0001",
        case_id="case-0001",
        prompt=envelope.task_text,
        model=ModelOptions(),
        structured_case_execution=structured_request.model_dump(mode="json"),
    )
    provider = _RecordingReactProvider()
    adapter = StructuredV1Adapter(
        provider=provider,
        freeze_manifest_path=freeze_path,
        bundle_directory=tmp_path / "bundles",
        clock=_ScriptedClock([0, 1, 60]),
        host=rehearsal.FakeHost(),
    )

    events = [event async for event in adapter.execute(request)]

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "execution_finished"
    assert event.data["bundle_complete"] is True
    exported = load_bundle(Path(event.data["bundle_path"]))
    assert exported.bundle_digest == event.data["bundle_digest"]
    assert exported.tool_returns[0].tool_name == "read_drive_file"
    assert len(provider.inputs) == 2


def test_factory_routes_the_structured_request_to_its_own_adapter(
    manifest, root_case
) -> None:
    import test_structured_episode_rehearsal as rehearsal

    envelope, _, _, _ = rehearsal._prepared(manifest, root_case)
    structured_request = StructuredCaseRequest(
        request_id="request-0001",
        envelope=envelope,
    )
    request = ExecutionRequest(
        execution_id="episode-0001",
        case_id="case-0001",
        prompt=envelope.task_text,
        model=ModelOptions(),
        structured_case_execution=structured_request.model_dump(mode="json"),
    )

    assert isinstance(AdapterFactory().create(request), StructuredV1Adapter)
