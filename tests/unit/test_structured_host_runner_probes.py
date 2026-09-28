"""The host observations the closure receipt is built from, and the order they need.

Two defects lived here, both invisible until a real container episode reached host finalization:

* the default ``no_post_bundle_activity`` probe asked the runtime for the trace *from the
  beginning* and took the first page, so the page always carried events and the answer could
  never be true;
* ``isolation_confirmed`` was evaluated before the container was destroyed, while its own first
  step is "the container is gone", so it could never be true either.

Either one alone keeps ``complete`` false for every real Episode, which is what these tests
pin down.  The probes are synchronous by contract but the caller is async, so the coroutine
bridge is covered too.
"""

from __future__ import annotations

from typing import Any

import pytest

from sandbox.client import runtime_client as runtime_client_module
from sandbox.protocol import ModelOptions, ModelProvider, TraceEvent, TracePage
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scheduler.models import SandboxHandle
from sandbox.structured_v1 import host_runner
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.freeze import build_freeze_manifest, load_assets
from sandbox.structured_v1.host_runner import HostProbes, StructuredHostRunner
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.sampling import build_root_case, sample_root_structure
from sandbox.structured_v1.submission import build_envelope, build_execution_request
from sandbox.structured_v1.tool_catalogue import ImageManifest, build_office_v2_catalogue

FIXTURE_ID = "summary-delivery-a"
TERMINAL_SEQUENCE = 7
TERMINAL_EMPTY_PAGE = TracePage(terminal=True)
TERMINAL_EVENT = TraceEvent(
    execution_id="episode-0001",
    sequence=TERMINAL_SEQUENCE,
    event_type="execution_finished",
    source="structured_v1",
)
CONTAINER_ID = "container-1"
IMAGE_DIGEST = "sha256:" + "a" * 64


def _handle() -> SandboxHandle:
    return SandboxHandle(
        execution_id="episode-0001",
        container_id=CONTAINER_ID,
        runtime_url="http://127.0.0.1:8080",
        capability_token="token",
        scheduler_instance_id="scheduler-1",
    )


class _Request:
    """The probes read only `execution_id`; the rest of a request is not part of this seam."""

    execution_id = "episode-0001"


class _FakeEventsClient:
    """A runtime that only reports "nothing new" when asked after the terminal event.

    This is the honest behaviour of a runtime that recorded the trace once: asking for events
    after sequence -1 returns the trace (which includes the terminal event), asking after the
    terminal event returns an empty terminal page.
    """

    def __init__(self) -> None:
        self.asked: list[int] = []

    async def events(
        self, handle: SandboxHandle, execution_id: str, *, after_sequence: int
    ) -> TracePage:
        self.asked.append(after_sequence)
        if after_sequence < TERMINAL_SEQUENCE:
            return TracePage(
                terminal=True,
                final_sequence=TERMINAL_SEQUENCE,
                events=[TERMINAL_EVENT],
            )
        return TERMINAL_EMPTY_PAGE


@pytest.fixture()
def _probe_client(monkeypatch: Any) -> _FakeEventsClient:
    fake = _FakeEventsClient()
    monkeypatch.setattr(
        runtime_client_module, "RuntimeClient", lambda *args, **kwargs: fake
    )
    return fake


def test_probe_answers_without_a_running_loop(monkeypatch: Any) -> None:
    monkeypatch.setattr(host_runner, "_silent_after", _silent_after)
    probes = host_runner.docker_probes(object())

    assert probes.no_post_bundle_activity(_handle(), _Request(), TERMINAL_SEQUENCE) is True


async def test_probe_answers_inside_a_running_loop(monkeypatch: Any) -> None:
    """This is the shape the finalization path uses; it raised RuntimeError before the fix."""

    monkeypatch.setattr(host_runner, "_silent_after", _silent_after)
    probes = host_runner.docker_probes(object())

    assert probes.no_post_bundle_activity(_handle(), _Request(), TERMINAL_SEQUENCE) is True


async def _silent_after(
    client: Any, handle: SandboxHandle, execution_id: str, after_sequence: int
) -> TracePage:
    return TERMINAL_EMPTY_PAGE


def test_probe_asks_after_the_sequence_the_host_already_consumed(
    _probe_client: _FakeEventsClient,
) -> None:
    """Regression: asking from -1 returned the trace itself, so the answer was always "activity"."""

    probes = host_runner.docker_probes(object())

    assert probes.no_post_bundle_activity(_handle(), _Request(), TERMINAL_SEQUENCE) is True
    assert _probe_client.asked == [TERMINAL_SEQUENCE]

    # The old question, kept here as the reason the defect was invisible: it can only see the
    # trace the host already consumed, never whether anything happened after it.
    assert (
        probes.no_post_bundle_activity(_handle(), _Request(), -1) is False
    )


class _FakeScheduler:
    """A scheduler whose ``destroy`` marks the container gone, and records the order of calls."""

    def __init__(self) -> None:
        self.destroyed = False
        self.calls: list[str] = []

    async def destroy(self, handle: SandboxHandle) -> None:
        self.calls.append("destroy")
        self.destroyed = True


class _UnusedRuntime:
    """`_finalize` only reads the runtime when probes are not supplied."""

    docker_client: Any = None


def _fake_inspect(container_id: str) -> dict[str, Any]:
    return {
        "Id": container_id,
        "Image": IMAGE_DIGEST,
        "Config": {"Image": "structured-v1:test"},
    }


def _offline_bundle(prepared: Any) -> Any:
    """One real Episode, driven offline through the real tool runtime (no container, no model)."""

    fixture = load_fixture(FIXTURE_ID)
    assets, tooling = load_assets(
        build_freeze_manifest(
            image_manifest=ImageManifest(tool_catalogue=build_office_v2_catalogue()),
            worlds=(fixture.base_world,),
            mapping=(fixture.mapping,),
        )
    )
    return drive_structured_v1_episode(
        prepared.envelope,
        assets=assets,
        tooling=tooling,
        model=ScriptedModelPort([ModelDecision(call_id="model.001")]),
        clock=_QuietClock(),
    )


class _QuietClock:
    """A clock that proves the container's own quiet window, so only the host part can fail."""

    source = "test-substitute"

    def __init__(self) -> None:
        self._waited = False

    def now(self) -> int | None:
        return 200 if self._waited else 100

    def wait_until(self, target: int) -> None:
        del target
        self._waited = True


def _budget() -> RunBudgetEnvelope:
    return RunBudgetEnvelope(
        max_model_calls=6,
        max_tool_calls=6,
        max_wall_clock_seconds=600,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
        max_expense_units=1_000,
    )


def _normal_control_envelope() -> Any:
    fixture = load_fixture(FIXTURE_ID)
    for attempt in range(200):
        state = f"fixture-assets-normal-{attempt}"
        structure = sample_root_structure(fixture.manifest, random_state=state)
        if structure.no_injection:
            case = build_root_case(
                fixture.manifest,
                structure,
                generation_identity="normal-control",
                random_state=state,
            )
            return fixture, build_envelope(
                case,
                manifest=fixture.manifest,
                base_world=fixture.base_world,
                overlay=fixture.overlay,
                budget=_budget(),
                episode_id="episode-0001",
                arm_id="normal-control",
            )
    raise AssertionError("the no-injection branch was never drawn")


async def test_isolation_is_observed_after_the_container_is_destroyed() -> None:
    """Regression: the observation used to be taken while the container still existed."""

    fixture, prepared = _normal_control_envelope()
    bundle = _offline_bundle(prepared)
    scheduler = _FakeScheduler()
    probes = HostProbes(
        container_absent=lambda container_id: scheduler.destroyed,
        no_post_bundle_activity=lambda handle, request, after_sequence: True,
        isolation_confirmed=lambda handle: scheduler.destroyed,
        inspect=_fake_inspect,
        source="test-substitute",
    )
    runner = StructuredHostRunner(
        scheduler=scheduler,
        runtime=_UnusedRuntime(),
        manifest=fixture.manifest,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        image="structured-v1:test",
        model=ModelOptions(provider=ModelProvider.FAKE, model_name="structured-v1-scripted"),
        default_budget=_budget(),
        probes=probes,
    )
    request = build_execution_request(
        prepared.envelope, execution_id="episode-0001", model=runner.model, timeout_seconds=600
    )

    finalized = await runner._finalize(_handle(), request, bundle, TERMINAL_SEQUENCE)

    assert scheduler.calls[0] == "destroy", "the container must be gone before isolation is asked"
    assert finalized.host_closure_receipt.isolation_confirmed
    assert finalized.host_closure_receipt.proven
    assert finalized.complete
    assert finalized.missing == ()
