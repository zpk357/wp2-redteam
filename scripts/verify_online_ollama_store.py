#!/usr/bin/env python3
"""Verify an online-downloaded Ollama model store against the release identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def blob_path(store: Path, digest: str) -> Path:
    match = DIGEST.fullmatch(digest)
    if match is None:
        raise ValueError(f"invalid Ollama digest: {digest}")
    return store / "blobs" / f"sha256-{match.group(1)}"


def verify_release_digest(payload: dict) -> None:
    body = {key: value for key, value in payload.items() if key != "release_manifest_digest"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if payload.get("release_manifest_digest") != expected:
        raise ValueError("release identity digest differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    release = json.loads(args.release.read_text(encoding="utf-8"))
    verify_release_digest(release)
    model = release["model"]
    name, tag = model["name"].split(":", 1)
    if not re.fullmatch(r"[a-z0-9._-]+", name) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", tag
    ):
        raise ValueError("release model name cannot be mapped to an Ollama manifest")
    manifest_path = (
        args.store / "manifests" / "registry.ollama.ai" / "library" / name / tag
    )
    raw_manifest_digest = file_digest(manifest_path)
    if raw_manifest_digest != model["manifest_digest"]:
        raise ValueError("downloaded model manifest digest differs from release identity")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_digest = manifest.get("config", {}).get("digest")
    layer_digests = [item.get("digest") for item in manifest.get("layers", [])]
    if config_digest != model["config_digest"]:
        raise ValueError("downloaded model config digest differs from release identity")
    if layer_digests != model["layer_digests"]:
        raise ValueError("downloaded model layer order differs from release identity")

    descriptors = [manifest["config"], *manifest["layers"]]
    verified = []
    for descriptor in descriptors:
        digest = descriptor["digest"]
        path = blob_path(args.store, digest)
        size = path.stat().st_size
        if size != descriptor["size"] or file_digest(path) != digest:
            raise ValueError(f"downloaded model blob differs: {digest}")
        verified.append({"digest": digest, "size": size})

    payload = {
        "schema_version": "trace-g-online-ollama-verification-v1",
        "model_name": model["name"],
        "manifest_path": str(manifest_path),
        "manifest_digest": raw_manifest_digest,
        "config_digest": config_digest,
        "layer_digests": layer_digests,
        "verified_blobs": verified,
        "verified_bytes": sum(item["size"] for item in verified),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, args.output)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
