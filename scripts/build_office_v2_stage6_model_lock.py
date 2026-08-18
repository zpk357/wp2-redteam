#!/usr/bin/env python3
"""Build the complete Office V2 Stage 6 model and runtime identity lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from sandbox.agent_prompts import OFFICE_AGENT_BASE_RULES_V2_DIGEST
from sandbox.fuzzer.v2_stage6_identity import (
    Stage6Role,
    seal_inference_config,
    seal_role_identity,
    seal_stage6_model_lock,
)
from sandbox.mutation.v2_brief import V2_MUTATION_PROMPT_IDENTITY_DIGEST
from scripts.verify_ollama_model_archive import verify_model_archive


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _image_id(reference: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().lower()
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"invalid Docker image ID for {reference}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-archive", type=Path, required=True)
    parser.add_argument("--acquisition-lock", type=Path, required=True)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--mutator-image", required=True)
    parser.add_argument("--controller-image", required=True)
    parser.add_argument("--combined-role-archive", type=Path, required=True)
    parser.add_argument("--controller-archive", type=Path, required=True)
    parser.add_argument("--ollama-image", default="ollama/ollama:0.32.1")
    parser.add_argument("--ollama-version", default="0.32.1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    acquisition = json.loads(args.acquisition_lock.read_text(encoding="utf-8"))
    verified = verify_model_archive(args.model_archive, acquisition)
    descriptors = verified["descriptors"]
    config = next(item for item in descriptors if item["label"] == "config")
    layers = tuple(
        item["digest"] for item in descriptors if item["label"].startswith("layer[")
    )
    role_archive_sha256 = _sha256(args.combined_role_archive)
    agent = seal_role_identity(
        role=Stage6Role.AGENT,
        image_reference=args.agent_image,
        image_id=_image_id(args.agent_image),
        image_archive_sha256=role_archive_sha256,
        prompt_identity_digest=OFFICE_AGENT_BASE_RULES_V2_DIGEST,
        provider_identity="ollama-react-stage6",
        inference=seal_inference_config(
            num_predict=4096,
            temperature="0.2",
            thinking=True,
        ),
    )
    mutator = seal_role_identity(
        role=Stage6Role.MUTATOR,
        image_reference=args.mutator_image,
        image_id=_image_id(args.mutator_image),
        image_archive_sha256=role_archive_sha256,
        prompt_identity_digest=V2_MUTATION_PROMPT_IDENTITY_DIGEST,
        provider_identity="provider-docker-ollama-v2",
        inference=seal_inference_config(
            num_predict=2048,
            temperature="0.7",
            thinking=False,
        ),
    )
    lock = seal_stage6_model_lock(
        manifest_digest=verified["model_digest"],
        config_digest=config["digest"],
        chat_protocol_digest=verified["chat_protocol_digest"],
        layer_digests=layers,
        archive_sha256=verified["archive_sha256"],
        archive_bytes=verified["archive_bytes"],
        ollama_image_reference=args.ollama_image,
        ollama_image_id=_image_id(args.ollama_image),
        ollama_version=args.ollama_version,
        controller_image_reference=args.controller_image,
        controller_image_id=_image_id(args.controller_image),
        controller_archive_sha256=_sha256(args.controller_archive),
        roles=(agent, mutator),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(lock.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(lock.lock_digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
