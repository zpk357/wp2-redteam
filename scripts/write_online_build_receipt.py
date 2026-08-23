#!/usr/bin/env python3
"""Write one atomic receipt for the server-side online image build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--model-verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-snapshot-sha256", required=True)
    parser.add_argument("--ollama-image-id", required=True)
    parser.add_argument("--node-image-id", required=True)
    parser.add_argument("--python-image-id", required=True)
    parser.add_argument("--langgraph-image-id", required=True)
    parser.add_argument("--harness-image-id", required=True)
    parser.add_argument("--mutator-image-id", required=True)
    parser.add_argument("--controller-image-id", required=True)
    args = parser.parse_args()
    if COMMIT.fullmatch(args.source_commit) is None:
        raise ValueError("source commit must be a full lowercase Git object ID")
    digest_values = {
        key: value
        for key, value in vars(args).items()
        if key.endswith("sha256") or key.endswith("image_id")
    }
    if any(DIGEST.fullmatch(value) is None for value in digest_values.values()):
        raise ValueError("receipt identities must be canonical SHA-256 digests")

    release = json.loads(args.release.read_text(encoding="utf-8"))
    release_body = {
        key: value for key, value in release.items() if key != "release_manifest_digest"
    }
    release_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            release_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if release.get("release_manifest_digest") != release_digest:
        raise ValueError("release identity digest differs")
    model = json.loads(args.model_verification.read_text(encoding="utf-8"))
    if model["manifest_digest"] != release["model"]["manifest_digest"]:
        raise ValueError("model verification and release identity differ")
    payload = {
        "schema_version": "trace-g-online-server-build-receipt-v1",
        "source": {
            "commit": args.source_commit,
            "snapshot_sha256": args.source_snapshot_sha256,
        },
        "release_manifest_digest": release_digest,
        "model_verification": model,
        "base_image_ids": {
            "ollama": args.ollama_image_id,
            "node": args.node_image_id,
            "python": args.python_image_id,
        },
        "built_image_ids": {
            "langgraph_agent": args.langgraph_image_id,
            "deepseek_harness_agent": args.harness_image_id,
            "mutator": args.mutator_image_id,
            "controller": args.controller_image_id,
        },
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload["receipt_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, args.output)
    print(json.dumps({"receipt_digest": payload["receipt_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
