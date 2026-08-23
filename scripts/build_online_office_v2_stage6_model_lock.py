#!/usr/bin/env python3
"""Bind a verified online server build receipt to the Stage 6 runtime lock."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sandbox.fuzzer.v2_stage6_identity import (
    Stage6ModelLock,
    Stage6Role,
    seal_role_identity,
    seal_stage6_model_lock,
)
from sandbox.replay.digests import sha256_digest


def _load_receipt(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "trace-g-online-server-build-receipt-v1":
        raise ValueError("unsupported online build receipt")
    observed = payload.get("receipt_digest")
    body = {key: value for key, value in payload.items() if key != "receipt_digest"}
    if observed != sha256_digest(body):
        raise ValueError("online build receipt digest differs")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-lock", type=Path, required=True)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--mutator-image", required=True)
    parser.add_argument("--controller-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = Stage6ModelLock.model_validate_json(args.base_lock.read_bytes())
    receipt = _load_receipt(args.build_receipt)
    model = receipt["model_verification"]
    base_images = receipt["base_image_ids"]
    built_images = receipt["built_image_ids"]
    receipt_digest = receipt["receipt_digest"]
    if not isinstance(model, dict) or not isinstance(base_images, dict):
        raise ValueError("online build receipt model identity is malformed")
    if not isinstance(built_images, dict) or not isinstance(receipt_digest, str):
        raise ValueError("online build receipt image identity is malformed")
    if (
        model.get("model_name") != base.model_name
        or model.get("manifest_digest") != base.manifest_digest
        or model.get("config_digest") != base.config_digest
        or tuple(model.get("layer_digests", ())) != base.layer_digests
        or base_images.get("ollama") != base.ollama_image_id
    ):
        raise ValueError("online build receipt differs from the frozen model identity")

    base_roles = {item.role: item for item in base.roles}
    roles = []
    for role, reference, image_key in (
        (Stage6Role.AGENT, args.agent_image, "langgraph_agent"),
        (Stage6Role.MUTATOR, args.mutator_image, "mutator"),
    ):
        template = base_roles[role]
        roles.append(
            seal_role_identity(
                role=role,
                image_reference=reference,
                image_id=built_images[image_key],
                image_build_receipt_digest=receipt_digest,
                prompt_identity_digest=template.prompt_identity_digest,
                provider_identity=template.provider_identity,
                inference=template.inference,
            )
        )

    lock = seal_stage6_model_lock(
        manifest_digest=base.manifest_digest,
        config_digest=base.config_digest,
        chat_protocol_digest=base.chat_protocol_digest,
        layer_digests=base.layer_digests,
        model_build_receipt_digest=receipt_digest,
        ollama_image_reference=base.ollama_image_reference,
        ollama_image_id=base.ollama_image_id,
        ollama_version=base.ollama_version,
        controller_image_reference=args.controller_image,
        controller_image_id=built_images["controller"],
        controller_build_receipt_digest=receipt_digest,
        roles=tuple(roles),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(lock.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"lock_digest": lock.lock_digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
