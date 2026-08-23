"""Validation for one machine-readable release-candidate identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseCandidateError(ValueError):
    """A release identity is incomplete or differs from the current source."""


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReleaseCandidateError(f"{field} must be a SHA-256 digest")
    return value


def _path_value(payload: dict[str, Any], dotted_path: str) -> object:
    value: object = payload
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ReleaseCandidateError(f"release identity field is missing: {dotted_path}")
        value = value[part]
    return value


def release_manifest_digest(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "release_manifest_digest"}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_release_candidate(
    path: Path,
    *,
    require_deployment_ready: bool = False,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseCandidateError("release candidate is not readable JSON") from exc
    if not isinstance(payload, dict):
        raise ReleaseCandidateError("release candidate root must be an object")
    if payload.get("schema_version") != "trace-g-release-candidate-v2":
        raise ReleaseCandidateError("unsupported release candidate schema")
    if payload.get("release_manifest_digest") != release_manifest_digest(payload):
        raise ReleaseCandidateError("release candidate digest differs")

    from app.adapter.deepseek_harness_adapter import DeepSeekHarnessAdapter
    from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

    runtimes = {
        item.get("runtime_kind"): item
        for item in payload.get("agent_runtimes", [])
        if isinstance(item, dict)
    }
    if set(runtimes) != {"langgraph", "deepseek_harness"}:
        raise ReleaseCandidateError("release must bind exactly two Agent runtimes")
    harness = DeepSeekHarnessAdapter().producer_runtime_identity
    expected = {
        "langgraph": {
            "runtime_version": LangGraphReactRuntime.version,
            "composition_digest": LangGraphReactRuntime.composition_digest,
        },
        "deepseek_harness": {
            "runtime_version": harness["producer_runtime_version"],
            "composition_digest": harness[
                "producer_runtime_composition_digest"
            ],
        },
    }
    for kind, identity in expected.items():
        actual = runtimes[kind]
        if any(actual.get(key) != value for key, value in identity.items()):
            raise ReleaseCandidateError(f"{kind} runtime identity differs from source")
        _require_digest(actual.get("composition_digest"), f"{kind}.composition_digest")

    model = payload.get("model")
    if not isinstance(model, dict):
        raise ReleaseCandidateError("release model identity is missing")
    _require_digest(model.get("manifest_digest"), "model.manifest_digest")
    _require_digest(model.get("config_digest"), "model.config_digest")
    layers = model.get("layer_digests")
    if not isinstance(layers, list) or not layers:
        raise ReleaseCandidateError("model.layer_digests must be non-empty")
    for index, digest in enumerate(layers):
        _require_digest(digest, f"model.layer_digests[{index}]")
    _require_digest(model.get("ollama_local_image_id"), "model.ollama_local_image_id")

    deployment = payload.get("deployment_policy")
    if not isinstance(deployment, dict):
        raise ReleaseCandidateError("deployment policy is missing")
    missing = deployment.get("missing_identity_fields")
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        raise ReleaseCandidateError("missing_identity_fields must be a string list")
    required_deployment_identities = (
        "model.ollama_repository_digest",
        "build_inputs.node_repository_digest",
        "agent_images.langgraph_image_id",
        "agent_images.deepseek_harness_image_id",
        "agent_images.controller_image_id",
        "agent_images.mutator_image_id",
    )
    actual_missing = sorted(
        field
        for field in required_deployment_identities
        if _path_value(payload, field) is None
    )
    if sorted(missing) != actual_missing:
        raise ReleaseCandidateError(
            "missing_identity_fields does not match the release identity"
        )
    for field in required_deployment_identities:
        value = _path_value(payload, field)
        if value is not None:
            _require_digest(value, field)
    ready = deployment.get("deployment_ready")
    if not isinstance(ready, bool):
        raise ReleaseCandidateError("deployment_ready must be boolean")
    if ready == bool(missing):
        raise ReleaseCandidateError("deployment readiness disagrees with missing identities")
    if require_deployment_ready and not ready:
        raise ReleaseCandidateError(
            "release is not deployment-ready: " + ", ".join(missing)
        )
    return payload


__all__ = [
    "ReleaseCandidateError",
    "release_manifest_digest",
    "validate_release_candidate",
]
