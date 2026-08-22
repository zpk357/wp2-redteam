#!/usr/bin/env python3
"""Capture model residency while the two Stage 6 roles are actually running."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROLE_FILTERS = {
    "agent": "trace-g.component=agent-sandbox",
    "mutator": "trace-g.component=office-v2-llm-mutator",
}


def _run(*command: str) -> str:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _gpu_processes(device: str) -> dict[int, int]:
    raw = _run(
        "nvidia-smi",
        "-i",
        device,
        "--query-compute-apps=pid,used_gpu_memory",
        "--format=csv,noheader,nounits",
    )
    result: dict[int, int] = {}
    for line in raw.splitlines():
        if not line.strip() or "," not in line:
            continue
        pid, memory = (item.strip() for item in line.split(",", 1))
        if not pid.isdigit() or not memory.isdigit():
            continue
        result[int(pid)] = int(memory)
    return result


def _containers(label: str, campaign_id: str) -> tuple[str, ...]:
    raw = _run(
        "docker",
        "ps",
        "--filter",
        f"label={label}",
        "--filter",
        f"label=trace-g.campaign-id={campaign_id}",
        "--format",
        "{{.ID}}",
    )
    return tuple(line for line in raw.splitlines() if line)


def _container_pids(container_id: str) -> tuple[int, ...]:
    raw = _run("docker", "top", container_id, "-eo", "pid")
    return tuple(int(line.strip()) for line in raw.splitlines()[1:] if line.strip().isdigit())


def _ollama_models(container_id: str) -> list[dict[str, object]]:
    program = (
        "import json,urllib.request;"
        "print(json.dumps(json.load(urllib.request.urlopen("
        "'http://127.0.0.1:11434/api/ps',timeout=2))))"
    )
    for python in ("python", "python3"):
        try:
            payload = json.loads(_run("docker", "exec", container_id, python, "-c", program))
            models = payload.get("models", [])
            return models if isinstance(models, list) else []
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return []


def monitor(
    *,
    device: str,
    campaign_id: str,
    output: Path,
    stop_file: Path,
    interval: float,
) -> bool:
    samples: list[dict[str, object]] = []
    observed_pids: set[int] = set()
    observed_roles: set[str] = set()
    while not stop_file.exists():
        try:
            gpu = _gpu_processes(device)
            for role, label in ROLE_FILTERS.items():
                for container_id in _containers(label, campaign_id):
                    pids = _container_pids(container_id)
                    models = _ollama_models(container_id)
                    active = {pid: gpu[pid] for pid in pids if pid in gpu}
                    observed_pids.update(active)
                    if active and models:
                        observed_roles.add(role)
                    samples.append(
                        {
                            "role": role,
                            "container_id": container_id,
                            "gpu_processes": active,
                            "models": models,
                        }
                    )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            samples.append({"sample_error": type(exc).__name__})
        time.sleep(interval)
    residual_gpu = _gpu_processes(device)
    full_residency = {
        role: any(
            sample.get("role") == role
            and any(
                isinstance(model, dict)
                and isinstance(model.get("size"), int)
                and model["size"] > 0
                and model.get("size_vram") == model["size"]
                for model in sample.get("models", [])
            )
            for sample in samples
        )
        for role in ROLE_FILTERS
    }
    payload = {
        "schema_version": "office-v2-stage6-gpu-residency-v1",
        "campaign_id": campaign_id,
        "gpu_device": device,
        "observed_roles": sorted(observed_roles),
        "full_residency": full_residency,
        "peak_observed_mib": max(
            (memory for sample in samples for memory in sample.get("gpu_processes", {}).values()),
            default=0,
        ),
        "observed_model_process_pids": sorted(observed_pids),
        "residual_observed_model_process_pids": sorted(observed_pids & residual_gpu.keys()),
        "sample_count": len(samples),
    }
    payload["passed"] = (
        all(full_residency.values())
        and payload["peak_observed_mib"] > 0
        and not payload["residual_observed_model_process_pids"]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return bool(payload["passed"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu-device", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    return 0 if monitor(
        device=args.gpu_device,
        campaign_id=args.campaign_id,
        output=args.output,
        stop_file=args.stop_file,
        interval=args.interval,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
