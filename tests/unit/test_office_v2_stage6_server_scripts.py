from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

from sandbox.replay.digests import sha256_bytes
from scripts import monitor_office_v2_stage6_gpu
from scripts.build_g5_source_archive import build_archive

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_preflight_and_campaign_share_one_gpu_lease_contract() -> None:
    preflight = _script("server_preflight_office_v2_step6.sh")
    campaign = _script("server_run_office_v2_step6.sh")
    lease = 'exec 9>"$PERSIST_ROOT/locks/gpu-$GPU_DEVICE.lock"'
    assert lease in preflight
    assert lease in campaign
    assert "flock -n 9" in preflight
    assert "flock -n 9" in campaign


def test_controller_and_campaign_resources_share_trace_labels() -> None:
    controller = _script("server_python.sh")
    campaign = _script("server_run_office_v2_step6.sh")
    assert "trace-g.campaign-id=$TRACE_G_CAMPAIGN_ID" in controller
    assert "trace-g.work-item-id=$TRACE_G_WORK_ITEM_ID" in controller
    assert "type=bind,source=$PROJECT_DIR,target=$PROJECT_DIR,readonly" in controller
    assert '--campaign-id "$CAMPAIGN_ID" --work-item-id replay.strict-gate' in campaign


def test_new_campaign_forces_active_one_to_two_resume_boundary() -> None:
    campaign = _script("server_run_office_v2_step6.sh")
    assert "real-run" in campaign
    assert "--generations 1" in campaign
    assert "CLI_COMMAND=real-resume" in campaign
    assert "append_progress run 1" in campaign
    assert '2> "$RESULT_ROOT/run-to-${TARGET}.stderr.log"' in campaign


def test_repair_install_and_archive_bind_the_frozen_source_identity() -> None:
    repair = _script("server_apply_office_v2_step6_repair.sh")
    campaign = _script("server_run_office_v2_step6.sh")
    assert "docker\", \"tag" in repair
    assert "stage6-source-tree.json" in repair
    assert "stage6-repair-plan.json" in repair
    assert "--source-tree-identity .trace-g/stage6-source-tree.json" in campaign
    assert "${CAMPAIGN_ID}-to-${TARGET}-${suffix}-${RUN_ATTEMPT_ID}.tar.gz" in campaign


def test_repair_image_tags_are_unique_to_the_source_revision() -> None:
    builder = _script("build_office_v2_stage6_repair_lock.py")
    assert 'repair_tag = f"step6-repair-{args.revision[:12]}"' in builder
    assert "step6-repair-core-v3" not in builder


def test_gpu_monitor_is_scoped_to_the_preflight_campaign(monkeypatch) -> None:
    commands: list[tuple[str, ...]] = []

    def capture(*command: str) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(monitor_office_v2_stage6_gpu, "_run", capture)
    monitor_office_v2_stage6_gpu._containers(
        "trace-g.component=agent-sandbox", "stage6-preflight"
    )

    assert commands == [
        (
            "docker",
            "ps",
            "--filter",
            "label=trace-g.component=agent-sandbox",
            "--filter",
            "label=trace-g.campaign-id=stage6-preflight",
            "--format",
            "{{.ID}}",
        )
    ]


def test_preflight_success_requires_campaign_volume_cleanup() -> None:
    preflight = _script("server_preflight_office_v2_step6.sh")
    volume_filter = (
        "docker volume ls --filter 'label=trace-g.component=workspace-volume' "
        "--filter 'label=trace-g.campaign-id=stage6-preflight'"
    )
    assert preflight.count(volume_filter) == 1
    assert "--campaign-id stage6-preflight" in preflight


def test_server_entrypoints_run_project_checks_in_locked_controller() -> None:
    preflight = _script("server_preflight_office_v2_step6.sh")
    campaign = _script("server_run_office_v2_step6.sh")
    for script in (preflight, campaign):
        assert 'CONTROLLER_IMAGE="${LOCK_IMAGES[0]}"' in script
        assert "scripts/verify_office_v2_stage6_install.py verify" in script
        assert "scripts/verify_office_v2_stage6_install.py chain" in script
        assert 'PYTHONPATH="$PROJECT_DIR/src:$PROJECT_DIR" python3' not in script


def test_locked_controller_can_read_root_stage_record() -> None:
    runner = _script("server_python.sh")
    assert 'ROOT_STAGE_RECORD="$PROJECT_DIR/../stage.json"' in runner
    assert "stage_mount_args" in runner


def test_preflight_timeout_stays_within_execution_contract() -> None:
    preflight = _script("run_office_v2_stage6_preflight.py")
    campaign = (ROOT / "src/sandbox/fuzzer/v2_cli.py").read_text(encoding="utf-8")
    assert "timeout_seconds=600" in preflight
    assert "\n        timeout_seconds=900," not in preflight
    assert "startup_timeout_seconds=600" in preflight
    assert "startup_timeout_seconds=600" in campaign


def test_source_archive_preserves_git_blob_bytes(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.tar"
    relative = "src/sandbox/scenarios/office_v2/data/office-world-v2.0/calendar.json"
    manifest_relative = (
        "src/sandbox/scenarios/office_v2/data/office-world-v2.0/manifest.json"
    )
    build_archive(ROOT, archive_path, "HEAD")
    expected = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(archive_path, "r:") as archive:
        stream = archive.extractfile("./" + relative)
        manifest_stream = archive.extractfile("./" + manifest_relative)
        assert stream is not None and manifest_stream is not None
        archived = stream.read()
        manifest = json.loads(manifest_stream.read())
    expected_digest = next(
        item["sha256"]
        for item in manifest["files"]
        if item["filename"] == "calendar.json"
    )
    assert archived == expected
    assert sha256_bytes(archived) == expected_digest


def test_repair_kit_writes_linux_compatible_checksum_lines() -> None:
    builder = _script("build_office_v2_stage6_repair_kit.ps1")
    assert '$sumPayload = ($sumLines -join "`n") + "`n"' in builder
    assert "[IO.File]::WriteAllLines" not in builder
