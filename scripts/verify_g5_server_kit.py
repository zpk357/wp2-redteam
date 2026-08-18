#!/usr/bin/env python3
"""Verify the locked identities used by the 5.G5 server kit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_AGENT_LABELS = {
    "org.trace-g.runtime": "self-contained-agent-qwen",
    "org.trace-g.agent-framework": "langgraph",
}


class G5KitError(RuntimeError):
    pass


def load_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise G5KitError(f"unable to load G5 lock: {path}") from exc
    if not isinstance(value, dict):
        raise G5KitError("G5 lock must be a JSON object")
    validate_lock(value)
    return value


def _locked_artifact(lock: dict[str, Any], name: str) -> dict[str, Any]:
    value = lock.get(name)
    if not isinstance(value, dict):
        raise G5KitError(f"G5 lock is missing {name}")
    if not DIGEST.fullmatch(str(value.get("image_id", ""))):
        raise G5KitError(f"{name}.image_id must be a SHA-256 digest")
    if not SHA256.fullmatch(str(value.get("archive_sha256", ""))):
        raise G5KitError(f"{name}.archive_sha256 must be a bare SHA-256 digest")
    if not isinstance(value.get("reference"), str) or not value["reference"]:
        raise G5KitError(f"{name}.reference is required")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_lock(lock: dict[str, Any]) -> None:
    if lock.get("schema_version") != "1.0" or lock.get("gate") != "5.G5":
        raise G5KitError("expected a 5.G5 lock with schema_version 1.0")
    agent = _locked_artifact(lock, "agent_image")
    _locked_artifact(lock, "controller_image")
    labels = agent.get("labels")
    if not isinstance(labels, dict):
        raise G5KitError("agent_image.labels is required")
    for key, expected in REQUIRED_AGENT_LABELS.items():
        if labels.get(key) != expected:
            raise G5KitError(f"agent image label mismatch: {key}")
    if labels.get("org.trace-g.model.name") != lock.get("model_name"):
        raise G5KitError("agent image model name label does not match the lock")
    if labels.get("org.trace-g.model.digest") != lock.get("model_digest"):
        raise G5KitError("agent image model digest label does not match the lock")
    if not DIGEST.fullmatch(str(lock.get("model_digest", ""))):
        raise G5KitError("model_digest must be a SHA-256 digest")
    source = lock.get("source")
    g4 = lock.get("g4_acceptance")
    for name, value in (("source", source), ("g4_acceptance", g4)):
        if not isinstance(value, dict) or not SHA256.fullmatch(
            str(value.get("sha256", ""))
        ):
            raise G5KitError(f"{name}.sha256 must be a bare SHA-256 digest")
    forbidden = lock.get("forbidden_external_artifacts")
    if forbidden != ["ollama_image", "ollama_model_archive", "host_model_mount"]:
        raise G5KitError("G5 lock must explicitly forbid external model artifacts")


def verify_artifacts(lock: dict[str, Any], kit_root: Path) -> dict[str, Any]:
    root = kit_root.resolve()
    artifacts = {
        "agent_image": lock["agent_image"],
        "controller_image": lock["controller_image"],
        "source": lock["source"],
        "g4_acceptance": lock["g4_acceptance"],
    }
    verified: dict[str, Any] = {}
    for name, entry in artifacts.items():
        relative = entry.get("archive") if name != "g4_acceptance" else entry.get("path")
        if not isinstance(relative, str) or not relative:
            raise G5KitError(f"{name} has no packaged path")
        path = (root / Path(*relative.split("/"))).resolve()
        if root not in path.parents or not path.is_file():
            raise G5KitError(f"{name} artifact is missing or outside the kit")
        observed = _sha256_file(path)
        expected = entry.get("archive_sha256") if name.endswith("_image") else entry.get("sha256")
        if observed != expected:
            raise G5KitError(f"{name} artifact digest does not match the G5 lock")
        verified[name] = {"path": relative, "sha256": observed, "size_bytes": path.stat().st_size}
    return verified


def verify_loaded_images(lock: dict[str, Any]) -> dict[str, Any]:
    import docker

    client = docker.from_env()
    observed: dict[str, Any] = {}
    for key in ("agent_image", "controller_image"):
        entry = lock[key]
        image = client.images.get(entry["reference"])
        image_id = image.id.lower()
        if image_id != entry["image_id"]:
            raise G5KitError(
                f"{key} identity mismatch: expected {entry['image_id']}, observed {image_id}"
            )
        observed[key] = {"reference": entry["reference"], "image_id": image_id}
    labels = client.images.get(lock["agent_image"]["reference"]).attrs.get(
        "Config", {}
    ).get("Labels") or {}
    for key, expected in lock["agent_image"]["labels"].items():
        if labels.get(key) != expected:
            raise G5KitError(f"loaded agent image label mismatch: {key}")
    return {"schema_version": "1.0", "gate": "5.G5", "passed": True, **observed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--kit-root", type=Path)
    parser.add_argument("--loaded-images", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
        result = (
            verify_loaded_images(lock)
            if args.loaded_images
            else {"schema_version": "1.0", "gate": "5.G5", "passed": True}
        )
        if args.kit_root is not None:
            result["artifacts"] = verify_artifacts(lock, args.kit_root)
    except G5KitError as exc:
        result = {"schema_version": "1.0", "gate": "5.G5", "passed": False, "error": str(exc)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
