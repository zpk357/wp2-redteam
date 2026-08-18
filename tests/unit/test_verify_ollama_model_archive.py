from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.verify_ollama_model_archive import (
    ModelArchiveVerificationError,
    verify_model_archive,
)


def _archive(
    tmp_path: Path,
    *,
    include_manifest: bool = True,
    corrupt_layer: bool = False,
    renderer_protocol: bool = False,
):
    config = (
        b'{"model_format":"gguf","renderer":"qwen3.5","parser":"qwen3.5",'
        b'"requires":"0.17.1"}'
        if renderer_protocol
        else b'{"model_format":"gguf","template":"{{ .Messages }}"}'
    )
    layer = b"synthetic model weights"
    config_digest = hashlib.sha256(config).hexdigest()
    layer_digest = hashlib.sha256(layer).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": f"sha256:{config_digest}", "size": len(config)},
            "layers": [{"digest": f"sha256:{layer_digest}", "size": len(layer)}],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    path = tmp_path / "ollama-models.tar"
    with tarfile.open(path, "w") as archive:
        files = {
            f"blobs/sha256-{config_digest}": config,
            f"blobs/sha256-{layer_digest}": b"corrupt" if corrupt_layer else layer,
        }
        if include_manifest:
            files["manifests/registry.ollama.ai/library/qwen3/8b"] = manifest
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    lock = {
        "schema_version": "1.0",
        "model_name": "qwen3:8b",
        "model_digest": f"sha256:{manifest_digest}",
    }
    return path, lock


def test_model_archive_verifies_manifest_closure_and_blob_content(tmp_path: Path) -> None:
    path, lock = _archive(tmp_path)

    result = verify_model_archive(path, lock)

    assert result["passed"] is True
    assert result["verified_blob_count"] == 2
    assert result["referenced_blob_count"] == 2
    assert result["chat_protocol_kind"] == "template"
    assert result["chat_protocol_digest"].startswith("sha256:")
    assert result["chat_template_digest"].startswith("sha256:")


def test_model_archive_verifies_renderer_parser_protocol(tmp_path: Path) -> None:
    path, lock = _archive(tmp_path, renderer_protocol=True)

    result = verify_model_archive(path, lock)

    assert result["passed"] is True
    assert result["chat_protocol_kind"] == "renderer_parser"
    assert result["chat_protocol_digest"].startswith("sha256:")
    assert result["chat_template_digest"] is None
    assert result["renderer"] == "qwen3.5"
    assert result["parser"] == "qwen3.5"
    assert result["requires"] == "0.17.1"


def test_model_archive_rejects_missing_manifest(tmp_path: Path) -> None:
    path, lock = _archive(tmp_path, include_manifest=False)

    with pytest.raises(ModelArchiveVerificationError, match="missing manifest"):
        verify_model_archive(path, lock)


def test_model_archive_rejects_blob_whose_filename_digest_is_false(tmp_path: Path) -> None:
    path, lock = _archive(tmp_path, corrupt_layer=True)

    with pytest.raises(ModelArchiveVerificationError, match="does not match its filename"):
        verify_model_archive(path, lock)
