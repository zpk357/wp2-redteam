import json
import subprocess
import sys
from pathlib import Path

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
    OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]


def test_formal_image_contains_ollama_model_and_hash_locked_python_stack() -> None:
    dockerfile = (ROOT / "agent_image" / "Dockerfile.qwen").read_text(encoding="utf-8")

    assert "COPY --from=ollama-runtime /usr/bin/ollama /usr/bin/ollama" in dockerfile
    assert "COPY --from=locked-models / /opt/ollama-models/" in dockerfile
    assert "--no-index --find-links=/tmp/wheelhouse" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'OLLAMA_HOST=127.0.0.1:11434' in dockerfile
    assert 'TRACE_G_OLLAMA_ENDPOINT=http://127.0.0.1:11434' in dockerfile
    assert 'org.trace-g.system-prompt.version="${SYSTEM_PROMPT_VERSION}"' in dockerfile
    assert 'org.trace-g.system-prompt.digest="${SYSTEM_PROMPT_DIGEST}"' in dockerfile
    assert 'org.trace-g.mutator-prompt.version="${MUTATOR_PROMPT_VERSION}"' in dockerfile
    assert 'org.trace-g.mutator-prompt.digest="${MUTATOR_PROMPT_DIGEST}"' in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "app.agent_qwen_bootstrap"]' in dockerfile
    assert "curl " not in dockerfile
    assert "ollama pull" not in dockerfile


def test_formal_python_lock_does_not_contain_windows_marker_artifact() -> None:
    lock = (ROOT / "agent_image" / "requirements.agent-qwen.lock").read_text(
        encoding="utf-8"
    )

    assert "fastapi==0.139.0" in lock
    assert "langgraph==1.2.10" in lock
    assert "langchain-ollama==1.1.0" in lock
    assert "colorama" not in lock.lower()
    assert lock.count("--hash=sha256:") == 42


def test_build_verifies_archives_wheels_and_final_image_identity() -> None:
    script = (ROOT / "scripts" / "build_agent_qwen_image.ps1").read_text(
        encoding="utf-8"
    )

    assert "verify_ollama_model_archive.py" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert '"--require-hashes"' in script
    assert '"--platform", "manylinux_2_17_x86_64"' in script
    assert '"--build-context", "ollama-models=$modelContext"' in script
    assert "print_agent_prompt_identity.py" in script
    assert '"SYSTEM_PROMPT_VERSION=$($promptIdentity.version)"' in script
    assert '"SYSTEM_PROMPT_DIGEST=$($promptIdentity.digest)"' in script
    assert '"MUTATOR_PROMPT_VERSION=$($promptIdentity.mutator_version)"' in script
    assert '"MUTATOR_PROMPT_DIGEST=$($promptIdentity.mutator_digest)"' in script
    assert "source_image_id" in script
    assert "archive_config_digest" in script
    assert 'Remove-Item -LiteralPath $temporaryRoot -Recurse -Force' in script


def test_build_prompt_identity_matches_runtime_contract() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "print_agent_prompt_identity.py")],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
        "digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
        "mutator_version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
        "mutator_digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    }
