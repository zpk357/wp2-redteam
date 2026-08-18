from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_server_ollama_requires_one_explicit_gpu() -> None:
    compose = (ROOT / "deploy" / "docker-compose.server.yaml").read_text()
    env_example = (ROOT / "deploy" / ".env.server.example").read_text()
    preflight = (ROOT / "scripts" / "server_preflight.sh").read_text()

    assert "gpus: all" not in compose
    assert "device_ids:" in compose
    assert "${OLLAMA_GPU_DEVICE:?" in compose
    assert "OLLAMA_GPU_DEVICE=" in env_example
    assert '--gpus "device=$OLLAMA_GPU_DEVICE"' in preflight
    assert "--gpus all" not in preflight


def test_cpu_stage_is_independent_from_gpu_runtime() -> None:
    stage = (ROOT / "scripts" / "server_stage_offline.sh").read_text()
    bootstrap = (ROOT / "scripts" / "server_bootstrap_offline.sh").read_text()

    for forbidden in ("nvidia-smi", "nvidia-ctk", "--gpus", "ALLOW_SYSTEM_CHANGES"):
        assert forbidden not in stage
    assert "server_stage_offline.sh" in bootstrap
    assert "exec bash" in bootstrap


def test_gpu_activation_enforces_packaged_model_lock() -> None:
    activation = (ROOT / "scripts" / "server_activate_gpu.sh").read_text()
    smoke = (ROOT / "scripts" / "server_real_model_smoke.sh").read_text()

    assert "verify_server_locks.py model" in activation
    assert ".trace-g/model-lock.json" in activation
    assert "activation_failed EXIT" in activation
    assert "docker compose" in activation
    assert " down " in activation.replace("\\\n", " ")
    assert "verify_server_locks.py model" in smoke
    assert "target profile digest differs from the packaged model lock" in smoke


def test_server_model_storage_uses_explicit_persistent_bind() -> None:
    compose = (ROOT / "deploy" / "docker-compose.server.yaml").read_text()
    env_loader = (ROOT / "scripts" / "server_env.sh").read_text()
    stage = (ROOT / "scripts" / "server_stage_offline.sh").read_text()

    assert "${TRACE_G_MODEL_DIR:?" in compose
    assert "ollama-models:/models" not in compose
    assert "--no-same-owner" in stage
    assert "--no-same-permissions" in stage
    assert "--touch" in stage
    assert "--no-overwrite-dir" in stage
    assert "TRACE_G_MODEL_DIR" in env_loader
    assert ".trace-g-model-archive.sha256" in stage
    assert 'mktemp -d "${MODEL_DIR}.stage.XXXXXX"' in stage


def test_abort_script_preserves_data_and_stops_ollama() -> None:
    abort = (ROOT / "scripts" / "server_abort.sh").read_text()

    assert 'docker stop "$OLLAMA_CONTAINER"' in abort
    assert "docker compose" in abort
    assert "server_export_incomplete.sh" in abort
    assert "rm -rf" not in abort
    assert "docker volume rm" not in abort
    assert "compose down -v" not in abort


def test_trace_workspace_validation_and_export_are_standalone() -> None:
    validation = (ROOT / "scripts" / "server_validate_trace_workspace.sh").read_text()
    export = (ROOT / "scripts" / "server_export_trace_workspace.sh").read_text()
    packaging = (ROOT / "scripts" / "prepare_server_kit.ps1").read_text()

    assert "validate_trace_workspace_results.py" in validation
    assert "stage_trace_workspace_results.py" in export
    assert "campaign-validation.json" not in export
    assert "golden-set-candidate-manifest.json" not in export
    for script in (
        "validate_trace_workspace_results.py",
        "stage_trace_workspace_results.py",
        "server_export_trace_workspace.sh",
    ):
        assert f'"{script}"' in packaging


def test_server_python_only_propagates_explicit_docker_e2e_opt_in() -> None:
    wrapper = (ROOT / "scripts" / "server_python.sh").read_text()

    assert 'RUN_DOCKER_E2E="${TRACE_G_RUN_DOCKER_E2E:-0}"' in wrapper
    assert 'TRACE_G_RUN_DOCKER_E2E must be 0 or 1' in wrapper
    assert 'extra_env_args+=(--env TRACE_G_RUN_DOCKER_E2E=1)' in wrapper
