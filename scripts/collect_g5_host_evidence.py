#!/usr/bin/env python3
"""Collect bounded host and cleanup evidence after the 5.G5 gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import docker

from scripts.verify_g5_server_kit import load_lock


def collect(lock_path: Path, gpu_device: str, gpu_evidence_path: Path) -> dict:
    lock = load_lock(lock_path)
    client = docker.from_env()
    image = client.images.get(lock["agent_image"]["reference"])
    containers = client.containers.list(
        all=True, filters={"label": "trace-g.component=agent-sandbox"}
    )
    volumes = client.volumes.list(
        filters={"label": "trace-g.component=workspace-volume"}
    )
    gpu_line = gpu_evidence_path.read_text(encoding="utf-8").strip()
    passed = (
        image.id.lower() == lock["agent_image"]["image_id"]
        and not containers
        and not volumes
        and bool(gpu_line)
    )
    return {
        "schema_version": "1.0",
        "gate": "5.G5",
        "passed": passed,
        "docker_server_version": client.version().get("Version"),
        "agent_image_id": image.id.lower(),
        "agent_container_residue": [item.id for item in containers],
        "workspace_volume_residue": [item.name for item in volumes],
        "gpu_device": gpu_device,
        "gpu": gpu_line,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--gpu-device", required=True)
    parser.add_argument("--gpu-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.lock, args.gpu_device, args.gpu_evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
