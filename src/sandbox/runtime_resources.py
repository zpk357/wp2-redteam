"""Small, dependency-free resource snapshots for bounded local/server runs."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceSnapshot:
    disk_free_bytes: int | None
    gpu_available: bool
    gpu_memory_used_mib: int | None
    gpu_memory_free_mib: int | None
    gpu_processes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def snapshot_resources(path: Path | None = None) -> ResourceSnapshot:
    disk_free = None
    if path is not None:
        try:
            disk_free = shutil.disk_usage(path).free
        except OSError:
            disk_free = None
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return ResourceSnapshot(disk_free, False, None, None, ())
    query = [
        executable,
        "--query-gpu=memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(query, check=True, capture_output=True, text=True, timeout=5)
        first = result.stdout.strip().splitlines()[0].split(",")
        used, free = (int(item.strip()) for item in first[:2])
        processes = subprocess.run(
            [executable, "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
        return ResourceSnapshot(
            disk_free, True, used, free, tuple(item.strip() for item in processes)
        )
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return ResourceSnapshot(disk_free, True, None, None, ())


def write_resource_snapshot(path: Path, snapshot: ResourceSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


__all__ = ["ResourceSnapshot", "snapshot_resources", "write_resource_snapshot"]
