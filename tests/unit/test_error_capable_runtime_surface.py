"""The container side: what a host-built payload must survive before it runs.

Every test here is a way the container could have run an Opportunity that is not the one
the host recorded.  The surface's job is to make each of them stop the Episode instead.
These run without a container: `app.*` imports the same `sandbox.protocol` the host uses,
which is what makes the round trip meaningful evidence.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.adapter.base import AdapterConfigurationError
from app.error_capable_runtime_surface import (
    SUPPORTED_PAYLOAD_VERSION,
    build_error_capable_run,
)

from sandbox.protocol import ModelOptions
from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import DISCOVERY_TASK
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_submission import error_capable_payload
from sandbox.scenarios.error_capable_world import carrier_ids, planned_file_ids

EPISODE_ID = "campaign.guided.007"
SEED = 4242
MODEL_NAME = "probe-model"
ADAPTER_VERSION = "probe-adapter-v1"

FAMILIES = tuple(spec.task_family for spec in TASK_FAMILY_SPECS)
ATTACKS = tuple(spec.attack_mode for spec in ATTACK_SPECS)
PATHS = tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids)
LAYOUT = "balanced-9"


def _plan_and_material(*, episode_id: str = EPISODE_ID):
    request = SelectorRequest(
        mode=ErrorCapableMode.RANDOM,
        seed=SEED,
        agent_model_name=MODEL_NAME,
        available_task_families=FAMILIES,
        available_paths=PATHS,
        available_attacks=ATTACKS,
    )
    decision = SelectorDecision(
        task_family=TaskFamily.SUMMARY_DELIVERY,
        path_id="summary.public-index-email",
        attack_mode=ATTACKS[0],
        attack_carrier=carrier_ids()[0],
        layout_id=LAYOUT,
        rationale="surface test holds the cell still so only the payload can vary",
    )
    file_ids = planned_file_ids(episode_id, seed=SEED, layout_id=LAYOUT)
    plan = build_plan(
        request,
        decision,
        episode_id=episode_id,
        file_ids=file_ids,
        attack_carrier=decision.attack_carrier,
        model_name=MODEL_NAME,
        layout_id=LAYOUT,
        task_file_id=file_ids[0],
    )
    return plan, materialize_scenario(plan)


def _payload() -> dict:
    plan, material = _plan_and_material()
    return error_capable_payload(
        index=7,
        mode=ErrorCapableMode.RANDOM.value,
        plan=plan,
        material=material,
        fixture=load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID),
        model_identity=ModelIdentity.capture(
            provider_id="local",
            raw_model_label=MODEL_NAME,
            provider_version=ADAPTER_VERSION,
        ),
        adapter_version=ADAPTER_VERSION,
        max_tool_requests=24,
        max_continuations=3,
    )


def test_a_host_built_payload_reproduces_the_opportunity() -> None:
    """The round trip the containerisation rests on: host writes it, image recomputes it."""

    run = build_error_capable_run(_payload(), prompt=DISCOVERY_TASK)
    plan, material = _plan_and_material()

    assert run.episode_id == EPISODE_ID
    assert run.plan == plan
    assert run.material.materialization_digest == material.materialization_digest
    assert run.fixture.freeze_digest == load_error_capable_fixture(
        ERROR_CAPABLE_FIXTURE_ID
    ).freeze_digest


def test_the_payload_version_is_checked() -> None:
    payload = _payload()
    payload["version"] = "error-capable-execution-v0"

    with pytest.raises(AdapterConfigurationError, match="unsupported error-capable payload"):
        build_error_capable_run(payload, prompt=DISCOVERY_TASK)


@pytest.mark.parametrize("missing", [None, "3", True, -1])
def test_a_continuation_budget_the_host_did_not_choose_is_refused(missing) -> None:
    """The budget has to come from the host, not from whatever this image defaults to.

    It used to be a default on `run_agent_episode` and nothing else, so every container ran with
    three whether or not the host had decided three, and the run's configuration could not say
    what the number was.  A payload that does not carry a usable one is refused rather than run:
    substituting the image's own value would run an Episode whose parameters nobody recorded.

    `True` is in the list because it is an `int` in Python, and a budget of `True` is a boolean
    that slipped through a type check, not a budget of one.
    """

    payload = _payload()
    if missing is None:
        del payload["max_continuations"]
    else:
        payload["max_continuations"] = missing

    with pytest.raises(AdapterConfigurationError, match="no usable max_continuations"):
        build_error_capable_run(payload, prompt=DISCOVERY_TASK)


def test_a_prompt_from_another_revision_is_refused() -> None:
    with pytest.raises(AdapterConfigurationError, match="not this image's frozen discovery"):
        build_error_capable_run(_payload(), prompt="carry out the task in the attached file")


def test_a_tampered_plan_is_refused_before_anything_recomputes() -> None:
    """The plan travels, so the container has to prove it is the plan the host hashed."""

    payload = _payload()
    payload["plan"]["seed"] = SEED + 1

    with pytest.raises(AdapterConfigurationError, match="does not hash to the plan digest"):
        build_error_capable_run(payload, prompt=DISCOVERY_TASK)


def test_a_container_with_a_different_fixture_is_refused() -> None:
    payload = _payload()
    payload["fixture_freeze_digest"] = "sha256:" + "f" * 64

    with pytest.raises(AdapterConfigurationError, match="does not reproduce the frozen identity"):
        build_error_capable_run(payload, prompt=DISCOVERY_TASK)


def test_material_that_does_not_match_is_refused() -> None:
    payload = _payload()
    payload["materialization_digest"] = "sha256:" + "e" * 64

    with pytest.raises(AdapterConfigurationError, match="material this image computes"):
        build_error_capable_run(payload, prompt=DISCOVERY_TASK)


def test_a_missing_payload_is_refused() -> None:
    with pytest.raises(AdapterConfigurationError, match="missing execution payload"):
        build_error_capable_run(None, prompt=DISCOVERY_TASK)


def test_the_container_adapter_imports_without_the_docker_client() -> None:
    """The image ships the scenario package and not the host's tooling.

    The first version of the container adapter imported the terminal-event name from the
    runner, which imports the Docker client -- so every container Episode failed at once
    with `No module named 'docker'`.  This test is what makes that mistake impossible to
    make silently: it imports the container's own entry points in a subprocess where
    `docker` cannot be found.

    `error_capable_campaign` is in the list because the container imports it too, and
    because the natural place to put a single "where does this run" abstraction is inside
    the runner -- which would have quietly made the whole scenario package host-only.
    """

    repo = Path(__file__).resolve().parents[2]
    program = "\n".join(
        [
            "import importlib.abc, sys",
            f"sys.path[:0] = [{str(repo / 'src')!r}, {str(repo / 'agent_image')!r}]",
            "class Block(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, name, path=None, target=None):",
            "        if name == 'docker' or name.startswith('docker.'):",
            "            raise ModuleNotFoundError(\"No module named 'docker'\")",
            "        return None",
            "sys.meta_path.insert(0, Block())",
            "from app.adapter.error_capable_adapter import FINISHED_EVENT_SOURCE",
            "from sandbox.scenarios.error_capable_campaign import CAMPAIGN_VERSION",
            "from sandbox.scenarios.error_capable_executor import InProcessExecutor",
            # The workspace backend is reached from `run_agent_episode`, so it is what the
            # container imports at Episode time.  Naming it here is what stops a future
            # `import docker` -- or anything else host-only -- from being added to the one
            # module that writes the material the Agent reads.
            "from sandbox.scenarios.office_v2.workspace_fs import WorkspaceFileSystem",
            "print(FINISHED_EVENT_SOURCE, CAMPAIGN_VERSION, WorkspaceFileSystem.__name__)",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split()[0] == "error_capable"


def test_the_supported_version_is_the_one_the_host_writes() -> None:
    """A drift here would be a silent one: the host would ship a payload nobody accepts."""

    from sandbox.scenarios.error_capable_submission import ERROR_CAPABLE_EXECUTION_VERSION

    assert SUPPORTED_PAYLOAD_VERSION == ERROR_CAPABLE_EXECUTION_VERSION
    assert ModelOptions() is not None
