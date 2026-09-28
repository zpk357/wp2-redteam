from __future__ import annotations

from pathlib import Path

from sandbox.runtime_resources import ResourceSnapshot, snapshot_resources, write_resource_snapshot


def test_resource_snapshot_is_serializable_without_gpu(tmp_path: Path) -> None:
    snapshot = snapshot_resources(tmp_path)
    assert snapshot.disk_free_bytes is not None
    output = tmp_path / "resource.json"
    write_resource_snapshot(output, snapshot)
    assert '"gpu_available"' in output.read_text(encoding="utf-8")


def test_resource_snapshot_shape_is_stable() -> None:
    snapshot = ResourceSnapshot(1, False, None, None, ())
    assert snapshot.as_dict() == {
        "disk_free_bytes": 1,
        "gpu_available": False,
        "gpu_memory_used_mib": None,
        "gpu_memory_free_mib": None,
        "gpu_processes": (),
    }
