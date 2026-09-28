"""The container side of the structured-scenario branch (R2/R10; P6).

This module is the **new branch**: it is reached only by a request carrying
``structured_case_execution``, it never touches the Office V2 envelope or its constants,
and it refuses rather than falling back when anything does not line up - which is the whole
point of §7 of the envelope draft.

What it does today: parse the request, rebuild the image's own assets from the freeze
manifest, and hand back the store and frozen tooling facts plus the running session's
entry point. Host image identity is intentionally outside the container evidence.

What it does not do yet: bind the two ports the session needs from the app runtime - the
model client and the tool runtime over the container's state owner. Those two bindings are
the remaining container work, and until they exist the branch raises
``structured_stage_not_bound`` instead of pretending to run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapter.base import AdapterConfigurationError
from sandbox.structured_v1.assets import FrozenAssetStore
from sandbox.structured_v1.container import (
    REQUEST_KIND,
    StructuredCaseRequest,
    parse_structured_case_request,
)
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal
from sandbox.structured_v1.freeze import FreezeManifest, load_assets
from sandbox.structured_v1.tool_catalogue import ToolingFacts

#: Where the image build leaves what it froze.
FREEZE_MANIFEST_PATH = Path("/opt/structured-v1/freeze-manifest.json")


@dataclass(frozen=True, slots=True)
class StructuredV1Run:
    """The pieces one structured episode needs, assembled but not yet driven."""

    request: StructuredCaseRequest
    assets: FrozenAssetStore
    tooling: ToolingFacts


def build_structured_v1_run(
    payload: dict[str, Any] | None,
    *,
    freeze_manifest_path: Path = FREEZE_MANIFEST_PATH,
) -> StructuredV1Run:
    """Assemble one structured episode from a request and the image's frozen assets."""

    if payload is None:
        raise AdapterConfigurationError(
            "structured_configuration_error",
            f"{REQUEST_KIND} requires its request payload",
        )
    try:
        request = parse_structured_case_request(payload)
    except EnvelopeRefusal as refusal:
        raise AdapterConfigurationError(refusal.code.value, refusal.detail) from refusal

    if not freeze_manifest_path.exists():
        raise AdapterConfigurationError(
            "structured_configuration_error",
            f"the image has no freeze manifest at {freeze_manifest_path}",
        )
    manifest = FreezeManifest.model_validate(
        json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
    )
    try:
        assets, tooling = load_assets(manifest)
    except EnvelopeRefusal as refusal:
        raise AdapterConfigurationError(refusal.code.value, refusal.detail) from refusal
    return StructuredV1Run(request=request, assets=assets, tooling=tooling)


def assemble_tool_port(
    run: StructuredV1Run,
) -> object:
    """Bind the real tool runtime and its port over the materialised initial state.

    The effect capture therefore reads the runtime's own policy decisions, not a
    test-fabricated audience. The model port is the one binding still owned by the image's
    agent loop, and :func:`require_model_port_bound` refuses until it is supplied.
    """

    from sandbox.structured_v1.ports import (
        RegisteredContent,
        SlotResolver,
        ToolRuntimePort,
    )
    from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
    from sandbox.structured_v1.world import materialize_world

    materialized = materialize_world(
        run.request.envelope.manifest,
        run.request.envelope.material,
        base_world=run.assets.resolve(run.request.envelope.base_locator),
        overlay=run.request.envelope.overlay,
        episode_id=run.request.envelope.schedule.episode_id,
        expected_base_world_digest=run.request.envelope.base_world_digest,
    )
    runtime = assemble_tool_runtime(
        materialized=materialized, manifest=run.request.envelope.manifest
    )
    return ToolRuntimePort(
        runtime,
        slots=SlotResolver.from_overlay(run.request.envelope.overlay),
        registered=RegisteredContent.from_manifest(run.request.envelope.manifest),
        public_delivery=run.request.envelope.overlay.public_delivery,
        episode_id=run.request.envelope.schedule.episode_id,
    )


def require_model_port_bound(model_port: object | None) -> None:
    """Refuse to run until the model port is supplied by the image's agent loop.

    Raising here is deliberate: an episode that cannot be driven must not silently produce
    a bundle that looks like an episode that ran.
    """

    if model_port is None:
        raise AdapterConfigurationError(
            "structured_stage_not_bound",
            "the model port is not bound yet; "
            f"{REQUEST_KIND} cannot run in this image build",
        )
