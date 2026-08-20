from __future__ import annotations

from pathlib import Path

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
