from __future__ import annotations

import pytest

from scripts.verify_server_locks import (
    LockVerificationError,
    verify_image_lock_entry,
    verify_model_lock,
)


def _image_entry() -> dict:
    return {
        "reference": "example/image:server",
        "archive": "images/example.tar",
        "archive_sha256": "a" * 64,
        "archive_config_digest": "sha256:" + "1" * 64,
        "source_image_id": "sha256:" + "2" * 64,
    }


@pytest.mark.parametrize(
    ("observed", "identity_type"),
    [
        ("sha256:" + "1" * 64, "archive_config_digest"),
        ("sha256:" + "2" * 64, "source_image_id"),
    ],
)
def test_image_lock_accepts_only_declared_runtime_identities(
    observed: str,
    identity_type: str,
) -> None:
    result = verify_image_lock_entry(
        "agent",
        _image_entry(),
        observed_reference="example/image:server",
        observed_image_id=observed,
    )
    assert result["matched_identity_type"] == identity_type


def test_image_lock_rejects_unknown_runtime_identity() -> None:
    with pytest.raises(LockVerificationError, match="not a locked runtime identity"):
        verify_image_lock_entry(
            "agent",
            _image_entry(),
            observed_reference="example/image:server",
            observed_image_id="sha256:" + "3" * 64,
        )


def _model_lock() -> dict:
    return {
        "schema_version": "1.0",
        "model_name": "qwen3:8b",
        "model_digest": "sha256:" + "4" * 64,
        "ollama_image": "ollama/ollama:0.32.1",
    }


def test_model_lock_requires_packaged_digest() -> None:
    result = verify_model_lock(
        _model_lock(),
        {"models": [{"name": "qwen3:8b", "digest": "4" * 64}]},
        expected_ollama_image="ollama/ollama:0.32.1",
    )
    assert result["model_digest"] == "sha256:" + "4" * 64


def test_model_lock_rejects_same_tag_with_different_digest() -> None:
    with pytest.raises(LockVerificationError, match="model digest mismatch"):
        verify_model_lock(
            _model_lock(),
            {"models": [{"name": "qwen3:8b", "digest": "5" * 64}]},
            expected_ollama_image="ollama/ollama:0.32.1",
        )

